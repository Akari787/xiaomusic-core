import json
import os
import stat
from dataclasses import dataclass
from typing import Any


@dataclass
class TokenStoreResult:
    data: dict[str, Any]
    from_env: bool
    persisted: bool


class TokenStore:
    """Load/save OAuth2 token data with safe defaults.

    Precedence:
    - Environment variables override file values.

    Supported env vars:
    - OAUTH2_ACCESS_TOKEN
    - OAUTH2_REFRESH_TOKEN
    """

    def __init__(self, config, log):
        self.config = config
        self.log = log
        self._mem_data: dict[str, Any] | None = None

    def _warn_if_insecure_permissions(self, path: str) -> None:
        try:
            st = os.stat(path)
            # Only meaningful on unix-like systems
            if os.name != "posix":
                return
            mode = stat.S_IMODE(st.st_mode)
            if mode & (stat.S_IRWXG | stat.S_IRWXO):
                self.log.warning(
                    "OAuth2 token file permissions are too open; recommend chmod 600."
                )
        except Exception:
            return

    def load(self) -> TokenStoreResult:
        token_path = self.config.oauth2_token_path
        data: dict[str, Any] = {}

        # In-memory takes precedence over file when persist_token is false
        if self._mem_data is not None:
            data.update(self._mem_data)

        if token_path and os.path.isfile(token_path):
            self._warn_if_insecure_permissions(token_path)
            try:
                with open(token_path, encoding="utf-8") as f:
                    data.update(json.load(f))
            except Exception as e:
                self.log.warning(f"Failed to read OAuth2 token file: {e}")

        from_env = False
        access = os.getenv("OAUTH2_ACCESS_TOKEN")
        refresh = os.getenv("OAUTH2_REFRESH_TOKEN")
        # Map env vars onto existing cookie/token semantics
        if access:
            data["serviceToken"] = access
            data.setdefault("yetAnotherServiceToken", access)
            from_env = True
        if refresh:
            data["passToken"] = refresh
            from_env = True

        return TokenStoreResult(data=data, from_env=from_env, persisted=bool(token_path))

    def save(self, data: dict[str, Any]) -> None:
        if not self.config.persist_token:
            self._mem_data = dict(data)
            self.log.info("Token not persisted (persist_token=false)")
            return

        token_path = self.config.oauth2_token_path
        if not token_path:
            return
        os.makedirs(os.path.dirname(token_path), exist_ok=True)
        with open(token_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        # Best-effort chmod 600 on unix
        if os.name == "posix":
            try:
                os.chmod(token_path, 0o600)
            except Exception:
                pass

    def clear(self) -> None:
        self._mem_data = None
