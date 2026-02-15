import ast
from typing import Any

from xiaomusic import __version__
from xiaomusic.security.errors import OutboundBlockedError
from xiaomusic.security.outbound import OutboundBlockedError as _OutboundBlocked
from xiaomusic.security.outbound import OutboundPolicy, fetch_text
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
        out = {}
        for k, v in zip(node.keys, node.values, strict=True):
            if k is None:
                raise ExecValidationError("**dict unpack is not allowed")
            out[_ast_literal(k)] = _ast_literal(v)
        return out
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
async def http_get(
    *,
    url: str,
    allowlist_domains: list[str],
    timeout_sec: float = 5.0,
    max_bytes: int = 1024 * 1024,
    max_redirects: int = 3,
) -> str:
    if not allowlist_domains:
        raise ExecNotAllowedError("http_get requires allowlist_domains")
    policy = OutboundPolicy(tuple(allowlist_domains))
    try:
        return await fetch_text(
            url,
            policy=policy,
            timeout_s=timeout_sec,
            max_bytes=max_bytes,
            max_redirects=max_redirects,
            user_agent=f"XiaoMusic/{__version__} exec-http-get",
        )
    except _OutboundBlocked as e:
        raise OutboundBlockedError(str(e))


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

            domains = (
                list(getattr(self.config, "outbound_allowlist_domains", []) or [])
                or list(getattr(self.config, "allowlist_domains", []) or [])
            )
            return await http_get(url=args.url, allowlist_domains=domains)

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
