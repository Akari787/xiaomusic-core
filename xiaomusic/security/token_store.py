import json
import os
import stat
import threading
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from time import sleep, time
from typing import Any


@dataclass
class TokenStoreResult:
    data: dict[str, Any]
    from_env: bool
    persisted: bool


class TokenStore:
    """Thread-safe auth token store with in-memory mirror and atomic flush.

    Notes:
    - This store serializes writes inside a single process.
    - Multi-worker concurrent writes are not supported.
    """

    def __init__(self, path_or_config, log=None):
        self.log = log
        self.config = None
        if isinstance(path_or_config, (str, Path)):
            token_path = str(path_or_config)
        else:
            self.config = path_or_config
            try:
                token_path = path_or_config.auth_token_path
            except Exception:
                conf_path = getattr(path_or_config, "conf_path", "conf") or "conf"
                token_file = (
                    getattr(path_or_config, "auth_token_file", "")
                    or getattr(path_or_config, "oauth2_token_file", "auth.json")
                    or "auth.json"
                )
                if os.path.isabs(token_file):
                    token_path = token_file
                else:
                    token_path = os.path.join(conf_path, token_file)
                self._log(
                    "warning",
                    "TokenStore init fallback token path used: %s",
                    token_path,
                )

        self.path = Path(token_path)
        self._token: dict[str, Any] = {}
        self._loaded = False
        self._dirty = False
        self._lock = threading.Lock()

    def _log(self, level: str, message: str, *args):
        if self.log is None:
            return
        fn = getattr(self.log, level, None)
        if fn is not None:
            fn(message, *args)

    def _warn_if_insecure_permissions(self, path: str) -> None:
        try:
            st = os.stat(path)
            # Only meaningful on unix-like systems
            if os.name != "posix":
                return
            mode = stat.S_IMODE(st.st_mode)
            if mode & (stat.S_IRWXG | stat.S_IRWXO):
                self._log(
                    "warning",
                    "Auth token file permissions are too open; recommend chmod 600."
                )
        except Exception:
            return

    def load(self) -> TokenStoreResult:
        with self._lock:
            if not self._loaded:
                self._token = self._load_from_disk_unlocked()
                self._loaded = True
            data = self._apply_env_overrides_unlocked(deepcopy(self._token))
            return TokenStoreResult(
                data=data,
                from_env=self._env_override_exists(),
                persisted=self.path.exists(),
            )

    def _load_from_disk_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            bak = self.path.with_name(self.path.name + ".bak")
            if bak.exists():
                try:
                    with bak.open(encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        self._log("warning", "TokenStore load: main missing, recovered from backup")
                        return data
                except Exception as e:
                    self._log("warning", "TokenStore load: backup read failed: %s", e)
            self._log("info", "TokenStore load: token file missing (%s)", str(self.path))
            return {}
        self._warn_if_insecure_permissions(str(self.path))
        try:
            with self.path.open(encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                self._log("warning", "TokenStore load: invalid token json type (%s)", type(data))
                return {}
            self._log("info", "TokenStore load: ok")
            return data
        except json.JSONDecodeError as e:
            bak = self.path.with_name(self.path.name + ".bak")
            if bak.exists():
                try:
                    with bak.open(encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        self._log("warning", "TokenStore load: recovered from backup")
                        return data
                except Exception:
                    pass
            bad_name = self.path.with_name(f"{self.path.name}.bad-{int(time())}")
            try:
                os.replace(self.path, bad_name)
                self._log("warning", "TokenStore load: moved bad token file to %s", bad_name)
            except Exception:
                pass
            self._log("warning", "TokenStore load failed, require qr login: %s", e)
            return {}
        except Exception as e:
            self._log("warning", "TokenStore load failed, require qr login: %s", e)
            return {}

    def _env_override_exists(self) -> bool:
        return bool(os.getenv("OAUTH2_ACCESS_TOKEN") or os.getenv("OAUTH2_REFRESH_TOKEN"))

    def _apply_env_overrides_unlocked(self, data: dict[str, Any]) -> dict[str, Any]:
        access = os.getenv("OAUTH2_ACCESS_TOKEN")
        refresh = os.getenv("OAUTH2_REFRESH_TOKEN")
        if access:
            data["serviceToken"] = access
            data.setdefault("yetAnotherServiceToken", access)
        if refresh:
            data["passToken"] = refresh
        return data

    def get(self) -> dict[str, Any]:
        return deepcopy(self.load().data)

    def update(self, new_token: dict[str, Any], reason: str = "") -> None:
        if not isinstance(new_token, dict):
            raise TypeError("new_token must be dict")
        with self._lock:
            if not self._loaded:
                self._token = self._load_from_disk_unlocked()
                self._loaded = True
            self._token = deepcopy(new_token)
            self._dirty = True
            self._log("info", "TokenStore update reason=%s", reason or "")

    def _atomic_write_unlocked(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(f"{self.path}.tmp")
        bak = Path(f"{self.path}.bak")
        if self.path.exists():
            try:
                with self.path.open(encoding="utf-8") as src, bak.open(
                    "w", encoding="utf-8"
                ) as dst:
                    dst.write(src.read())
                    dst.flush()
                    os.fsync(dst.fileno())
            except Exception as e:
                self._log("warning", "TokenStore backup write failed: %s", e)
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        last_exc = None
        for _ in range(20):
            try:
                os.replace(tmp, self.path)
                last_exc = None
                break
            except PermissionError as e:
                last_exc = e
                sleep(0.01)
        if last_exc is not None:
            raise last_exc
        if os.name == "posix":
            try:
                os.chmod(self.path, 0o600)
            except Exception as e:
                self._log("warning", "TokenStore chmod 600 failed: %s", e)

    def flush(self) -> None:
        with self._lock:
            persist_token = bool(getattr(self.config, "persist_token", True))
            if not persist_token:
                self._log("info", "TokenStore flush skipped (persist_token=false)")
                self._dirty = False
                return
            if not self._loaded:
                self._token = self._load_from_disk_unlocked()
                self._loaded = True
            if not self._dirty and self.path.exists():
                return
            self._atomic_write_unlocked(self._token)
            self._dirty = False
            self._log("info", "TokenStore flush complete")

    def reload_from_disk(self) -> None:
        with self._lock:
            self._token = self._load_from_disk_unlocked()
            self._loaded = True
            self._dirty = False

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            if not isinstance(data, dict):
                raise TypeError("data must be dict")
            persist_token = bool(getattr(self.config, "persist_token", True))
            self._token = deepcopy(data)
            self._loaded = True
            self._dirty = False
            if not persist_token:
                self._log("info", "TokenStore save skipped (persist_token=false)")
                return
            self._atomic_write_unlocked(self._token)
            self._log("info", "TokenStore save complete")

    def clear(self) -> None:
        with self._lock:
            self._token = {}
            self._loaded = True
            self._dirty = False

    def clear_and_remove(self) -> tuple[bool, list[str]]:
        removed = False
        removed_paths: list[str] = []
        with self._lock:
            self._token = {}
            self._loaded = True
            self._dirty = False
            candidates = [
                self.path,
                Path(os.path.abspath(str(self.path))),
                Path(os.path.join(os.getcwd(), str(self.path))),
                Path(os.path.join("/app", str(self.path).lstrip("/"))),
            ]
            dedup = []
            seen = set()
            for p in candidates:
                s = str(p)
                if s in seen:
                    continue
                seen.add(s)
                dedup.append(p)
            for p in dedup:
                if p.is_file():
                    p.unlink()
                    removed = True
                    removed_paths.append(str(p))
        return removed, removed_paths
