import ast
import ipaddress
import socket
from typing import Any

import requests
from pydantic import BaseModel, Field, ValidationError

from xiaomusic.security.errors import (
    ExecDisabledError,
    ExecNotAllowedError,
    ExecValidationError,
)
from xiaomusic.security.redaction import redact_text


class HttpGetArgs(BaseModel):
    url: str = Field(..., min_length=1)


class ExecCall(BaseModel):
    command: str
    args: list[Any] = Field(default_factory=list)
    kwargs: dict[str, Any] = Field(default_factory=dict)


def _ast_literal(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Dict):
        return {
            _ast_literal(k): _ast_literal(v)
            for k, v in zip(node.keys, node.values, strict=True)
        }
    if isinstance(node, ast.List):
        return [_ast_literal(x) for x in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_ast_literal(x) for x in node.elts)
    raise ExecValidationError("Only literal arguments are allowed")


def parse_exec_code(code: str) -> ExecCall:
    """Parse 'cmd("x", k=1)' into a safe ExecCall.

    Disallows attribute access, imports, names other than the function, etc.
    """

    code = (code or "").strip()
    if not code:
        raise ExecValidationError("Empty exec command")

    try:
        tree = ast.parse(code, mode="eval")
    except SyntaxError as e:
        raise ExecValidationError(f"Invalid exec syntax: {e}") from e

    if not isinstance(tree.body, ast.Call):
        raise ExecValidationError("Exec must be a function call")

    call = tree.body
    if not isinstance(call.func, ast.Name):
        raise ExecValidationError("Only direct function calls are allowed")

    command = call.func.id
    args = [_ast_literal(a) for a in call.args]
    kwargs = {}
    for kw in call.keywords:
        if kw.arg is None:
            raise ExecValidationError("**kwargs is not allowed")
        kwargs[kw.arg] = _ast_literal(kw.value)

    return ExecCall(command=command, args=list(args), kwargs=kwargs)


def _domain_allowed(host: str, allowlist: list[str]) -> bool:
    host = host.strip().lower().rstrip(".")
    if not host or not allowlist:
        return False

    for d in allowlist:
        d = d.strip().lower().rstrip(".")
        if not d:
            continue
        if host == d:
            return True
        if host.endswith("." + d):
            return True
    return False


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _is_private_ip(ip: str) -> bool:
    obj = ipaddress.ip_address(ip)
    return bool(
        obj.is_private
        or obj.is_loopback
        or obj.is_link_local
        or obj.is_multicast
        or obj.is_reserved
    )


def _resolve_and_block_private(host: str, port: int) -> None:
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    for family, _, _, _, sockaddr in infos:
        if family == socket.AF_INET:
            ip = sockaddr[0]
        elif family == socket.AF_INET6:
            ip = sockaddr[0]
        else:
            continue
        if _is_private_ip(ip):
            raise ExecNotAllowedError("Private/loopback/link-local IPs are not allowed")


def http_get(
    *,
    url: str,
    allowlist_domains: list[str],
    timeout_sec: float = 5.0,
    max_bytes: int = 1024 * 1024,
    max_redirects: int = 3,
):
    if not allowlist_domains:
        raise ExecNotAllowedError("http_get requires allowlist_domains")

    current = url
    for _ in range(max_redirects + 1):
        parsed = requests.utils.urlparse(current)
        if parsed.scheme not in ("http", "https"):
            raise ExecNotAllowedError("Only http/https URLs are allowed")

        host = parsed.hostname or ""
        if not host or host in ("localhost",):
            raise ExecNotAllowedError("Host not allowed")
        if _is_ip_literal(host):
            raise ExecNotAllowedError("IP literal URLs are not allowed")
        if not _domain_allowed(host, allowlist_domains):
            raise ExecNotAllowedError("Domain not in allowlist")

        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        _resolve_and_block_private(host, port)

        resp = requests.get(
            current,
            timeout=timeout_sec,
            allow_redirects=False,
            stream=True,
            headers={"User-Agent": "XiaoMusic/exec-http-get"},
        )
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location")
            if not loc:
                raise ExecValidationError("Redirect without Location")
            current = requests.compat.urljoin(current, loc)
            continue

        resp.raise_for_status()
        buf = bytearray()
        for chunk in resp.iter_content(chunk_size=8192):
            if not chunk:
                continue
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise ExecValidationError("Response too large")
        return buf.decode("utf-8", errors="replace")

    raise ExecValidationError("Too many redirects")


class ExecPluginEngine:
    def __init__(self, config, log, plugin_manager=None):
        self.config = config
        self.log = log
        self.plugin_manager = plugin_manager

    def _security_log(self, msg: str) -> None:
        # Keep logs safe by default.
        try:
            self.log.warning("SECURITY: %s", redact_text(msg))
        except Exception:
            self.log.warning("SECURITY: blocked exec")

    async def execute(self, code: str):
        if not getattr(self.config, "enable_exec_plugin", False):
            self._security_log("exec plugin disabled")
            raise ExecDisabledError("exec plugin disabled")

        call = parse_exec_code(code)
        allowed = set(getattr(self.config, "allowed_exec_commands", []) or [])
        if call.command not in allowed:
            self._security_log(f"exec command not allowed: {call.command}")
            raise ExecNotAllowedError("exec command not allowed")

        if call.command in ("http_get", "httpget"):
            # accept both names, normalize
            try:
                if call.kwargs:
                    args = HttpGetArgs(**call.kwargs)
                elif call.args and isinstance(call.args[0], dict):
                    args = HttpGetArgs(**call.args[0])
                elif call.args and isinstance(call.args[0], str):
                    args = HttpGetArgs(url=call.args[0])
                else:
                    raise ExecValidationError("http_get requires url")
            except ValidationError as e:
                raise ExecValidationError(str(e)) from e

            return http_get(
                url=args.url,
                allowlist_domains=list(getattr(self.config, "allowlist_domains", []) or []),
            )

        if not self.plugin_manager:
            raise ExecNotAllowedError("plugin manager not available")
        func = self.plugin_manager.get_func(call.command)
        if not func:
            raise ExecNotAllowedError("plugin command not found")

        # call plugin (sync or async)
        import inspect

        if inspect.iscoroutinefunction(func):
            return await func(*call.args, **call.kwargs)
        return func(*call.args, **call.kwargs)
