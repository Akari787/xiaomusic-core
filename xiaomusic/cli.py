#!/usr/bin/env python3
import argparse
import json
import logging
import os
import signal

import sentry_sdk
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger

from xiaomusic.security.redaction import redact_text

LOGO = r"""
 __  __  _                   __  __                 _
 \ \/ / (_)   __ _    ___   |  \/  |  _   _   ___  (_)   ___
  \  /  | |  / _` |  / _ \  | |\/| | | | | | / __| | |  / __|
  /  \  | | | (_| | | (_) | | |  | | | |_| | \__ \ | | | (__
 /_/\_\ |_|  \__,_|  \___/  |_|  |_|  \__,_| |___/ |_|  \___|
          {}
"""


def _sentry_before_send(event, hint):
    try:
        # Best-effort redact strings inside the event payload
        event_json = json.dumps(event, ensure_ascii=False)
        redacted = redact_text(event_json)
        return json.loads(redacted)
    except Exception:
        return event


if os.getenv("XIAOMUSIC_ENABLE_SENTRY", "false").lower() == "true":
    sentry_sdk.init(
        dsn="https://ffe4962642d04b29afe62ebd1a065231@glitchtip.hanxi.cc/1",
        integrations=[
            AsyncioIntegration(),
            LoggingIntegration(
                level=logging.WARNING,
                event_level=logging.ERROR,
            ),
        ],
        before_send=_sentry_before_send,
        # debug=True,
    )

ignore_logger("miservice")


def _detect_configured_worker_count() -> int:
    for key in ("XIAOMUSIC_WORKERS", "UVICORN_WORKERS", "WEB_CONCURRENCY", "GUNICORN_WORKERS"):
        raw = (os.getenv(key, "") or "").strip()
        if not raw:
            continue
        try:
            val = int(raw)
        except ValueError:
            continue
        if val > 0:
            return val
    return 1


def _enforce_single_worker() -> None:
    workers = _detect_configured_worker_count()
    if workers > 1:
        raise RuntimeError(
            "multi-worker is not supported: TokenStore/auth.json is single-process safe only; set workers=1"
        )


def _cors_localhost_only(origins: list[str]) -> bool:
    if not origins:
        return False
    allowed = {"http://localhost", "http://127.0.0.1", "https://localhost", "https://127.0.0.1"}
    for origin in origins:
        if (origin or "").strip().lower() not in allowed:
            return False
    return True


def _warn_if_httpauth_unsafe(config, bind_host: str, logger: logging.Logger) -> None:
    if not getattr(config, "disable_httpauth", False):
        return
    host = (bind_host or "").strip().lower()
    localhost_bind = host in {"127.0.0.1", "localhost"}
    cors_local = _cors_localhost_only(list(getattr(config, "cors_allow_origins", []) or []))
    if (not localhost_bind) or (not cors_local):
        logger.warning(
            "HTTP auth disabled; if exposed beyond LAN this is unsafe (disable_httpauth=true, bind_host=%s, cors_localhost_only=%s)",
            bind_host,
            cors_local,
        )


def main():
    from xiaomusic import __version__
    from xiaomusic.api import (
        HttpInit,
    )
    from xiaomusic.api import (
        app as HttpApp,
    )
    from xiaomusic.config import Config
    from xiaomusic.core.settings import get_settings
    from xiaomusic.xiaomusic import XiaoMusic

    _enforce_single_worker()

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--port",
        dest="port",
        help="监听端口",
    )
    parser.add_argument(
        "--hardware",
        dest="hardware",
        help="小爱音箱型号",
    )
    parser.add_argument(
        "--oauth2_token_file",
        dest="oauth2_token_file",
        help="OAuth2 token file path, relative to conf path by default",
    )
    parser.add_argument(
        "--verbose",
        dest="verbose",
        action="store_true",
        default=None,
        help="show info",
    )
    parser.add_argument(
        "--config",
        dest="config",
        help="config file path",
    )
    parser.add_argument(
        "--ffmpeg_location",
        dest="ffmpeg_location",
        help="ffmpeg bin path",
    )
    parser.add_argument(
        "--enable_config_example",
        dest="enable_config_example",
        help="是否输出示例配置文件",
        action="store_true",
    )

    print(LOGO.format(f"XiaoMusic v{__version__} by: github.com/hanxi"), flush=True)

    options = parser.parse_args()
    config = Config.from_options(options)

    # Load persisted settings early (affects logging/security defaults too).
    try:
        filename = config.getsettingfile()
        if os.path.exists(filename):
            with open(filename, encoding="utf-8") as f:
                data = json.loads(f.read())
                config.update_config(data)
    except Exception as e:
        print(f"Execption {e}")

    # Environment variables should override persisted settings for operational
    # controls like log_file (important for hardened containers).
    env_log_file = os.getenv("XIAOMUSIC_LOG_FILE")
    if env_log_file:
        config.log_file = env_log_file

    def _resolve_writable_log_file(cfg: Config) -> str:
        # Keep backward compatibility:
        # - absolute paths: use as-is
        # - relative paths: try CWD first; if not writable (e.g. read_only rootfs)
        #   fallback to conf/; last resort /tmp.
        raw = (cfg.log_file or "xiaomusic.log.txt").strip() or "xiaomusic.log.txt"

        candidates: list[str] = []
        if os.path.isabs(raw):
            candidates.append(raw)
        else:
            candidates.append(raw)
            conf_dir = (getattr(cfg, "conf_path", "") or "conf").strip() or "conf"
            candidates.append(os.path.join(conf_dir, raw))
        candidates.append("/tmp/xiaomusic.log.txt")

        for path in candidates:
            try:
                log_dir = os.path.dirname(path)
                if log_dir:
                    os.makedirs(log_dir, exist_ok=True)
                with open(path, "a", encoding="utf-8"):
                    pass
                return path
            except Exception:
                continue

        # Should be unreachable, but keep a safe default.
        return "/tmp/xiaomusic.log.txt"

    # In hardened containers we may run with read_only rootfs; make sure the
    # uvicorn file handler points to a writable location.
    config.log_file = _resolve_writable_log_file(config)

    # 自定义过滤器，过滤掉关闭时的 CancelledError
    class CancelledErrorFilter(logging.Filter):
        def filter(self, record):
            if record.exc_info:
                exc_type = record.exc_info[0]
                if exc_type and exc_type.__name__ == "CancelledError":
                    return False
            return True

    formatter_class = "logging.Formatter"
    if config.log_redact:
        formatter_class = "xiaomusic.security.logging.RedactingLogFormatter"

    LOGGING_CONFIG = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": formatter_class,
                "format": f"%(asctime)s [{__version__}] [%(levelname)s] %(message)s",
                "datefmt": "[%Y-%m-%d %H:%M:%S]",
            },
            "access": {
                "()": formatter_class,
                "format": f"%(asctime)s [{__version__}] [%(levelname)s] %(message)s",
                "datefmt": "[%Y-%m-%d %H:%M:%S]",
            },
        },
        "filters": {
            "cancelled_error": {
                "()": CancelledErrorFilter,
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
                "filters": ["cancelled_error"],
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
            "file": {
                "level": "INFO",
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "access",
                "filename": config.log_file,
                "maxBytes": 10 * 1024 * 1024,
                "backupCount": 1,
                "filters": ["cancelled_error"],
            },
        },
        "loggers": {
            "uvicorn": {
                "handlers": [
                    "default",
                    "file",
                ],
                "level": "INFO",
            },
            "uvicorn.error": {
                "level": "INFO",
            },
            "uvicorn.access": {
                "handlers": [
                    "access",
                    "file",
                ],
                "level": "INFO",
                "propagate": False,
            },
        },
    }

    # Note: config is already loaded above; keep this section empty to avoid
    # subtle ordering issues with logging config.

    import asyncio

    import uvicorn

    async def async_main(config: Config) -> None:
        # Validate required runtime secrets at startup.
        get_settings()
        bind_host = "0.0.0.0"
        _warn_if_httpauth_unsafe(config, bind_host, logging.getLogger("xiaomusic"))

        xiaomusic = XiaoMusic(config)
        HttpInit(xiaomusic)
        port = int(config.port)

        # XiaoMusic may adjust config.log_file (e.g. fallback to /tmp in read_only containers).
        try:
            LOGGING_CONFIG["handlers"]["file"]["filename"] = config.log_file
        except Exception:
            pass

        # 创建 uvicorn 配置，禁用其信号处理
        uvicorn_config = uvicorn.Config(
            HttpApp,
            host=bind_host,
            port=port,
            log_config=LOGGING_CONFIG,
        )
        server = uvicorn.Server(uvicorn_config)

        # 自定义信号处理
        shutdown_initiated = False

        def handle_exit(signum, frame):
            nonlocal shutdown_initiated
            if not shutdown_initiated:
                shutdown_initiated = True
                print("\n正在关闭服务器...")
                server.should_exit = True

        signal.signal(signal.SIGINT, handle_exit)
        signal.signal(signal.SIGTERM, handle_exit)

        # 运行服务器
        await server.serve()

    asyncio.run(async_main(config))


if __name__ == "__main__":
    main()
