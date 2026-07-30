"""Internal diagnostics API 路由

面向调试与故障排查的诊断能力。仅供项目内部使用，不承诺兼容性。
所有端点 include_in_schema=False，不进入公开 OpenAPI schema。

路径前缀: /api/internal/diagnostics/*
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from xiaomusic.api.routers.v1_shared import (
    _api_ok,
    _map_api_exception,
    _next_request_id,
    diag_query_svc,
)

router = APIRouter()
LOG = logging.getLogger("xiaomusic.api.diagnostics")


def _diag_endpoint(key: str):
    """Factory: build a diagnostics endpoint handler for the given key."""
    async def _handler():
        request_id = _next_request_id(None)
        try:
            return _api_ok(diag_query_svc(key), request_id=request_id)
        except Exception as exc:
            return _map_api_exception(exc, request_id)
    return _handler


# ── Auth Diagnostics (all include_in_schema=False) ─────────────────────

router.get("/api/internal/diagnostics/auth_state", include_in_schema=False)(
    _diag_endpoint("auth_state")
)
router.get("/api/internal/diagnostics/auth_recovery_state", include_in_schema=False)(
    _diag_endpoint("auth_recovery_state")
)
router.get("/api/internal/diagnostics/miaccount_login_trace", include_in_schema=False)(
    _diag_endpoint("miaccount_login_trace")
)
router.get("/api/internal/diagnostics/auth_rebuild_state", include_in_schema=False)(
    _diag_endpoint("auth_rebuild_state")
)
router.get("/api/internal/diagnostics/auth_runtime_reload_state", include_in_schema=False)(
    _diag_endpoint("auth_runtime_reload_state")
)
router.get(
    "/api/internal/diagnostics/auth_short_session_rebuild_state",
    include_in_schema=False,
)(_diag_endpoint("auth_short_session_rebuild_state"))
