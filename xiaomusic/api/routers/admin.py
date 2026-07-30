"""Admin API v1 路由

提供源管理与认证状态的管理/诊断能力，面向 WebUI 内部使用。
这些接口不属于 Runtime Public API 正式契约，不承诺兼容性。

路径前缀: /api/admin/v1/*
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, UploadFile

from xiaomusic.api.routers.v1_shared import (
    _api_ok,
    _map_api_exception,
    _map_structured_endpoint_exception,
    _next_request_id,
    auth_status_v1_svc,
    sources_delete_svc,
    sources_disable_svc,
    sources_enable_svc,
    sources_list_svc,
    sources_reload_svc,
    sources_upload_svc,
)

router = APIRouter()
LOG = logging.getLogger("xiaomusic.api.admin")


# ── Sources ────────────────────────────────────────────────────────────

@router.get("/api/admin/v1/sources")
async def admin_v1_sources(request_id: str | None = None):
    rid = _next_request_id(request_id)
    try:
        return _api_ok(sources_list_svc(), request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc, rid,
            default_error_code="E_SOURCES_QUERY_FAILED",
            default_stage="system",
            default_message="sources query failed",
        )


@router.post("/api/admin/v1/sources/reload")
async def admin_v1_sources_reload(request_id: str | None = None):
    rid = _next_request_id(request_id)
    try:
        return _api_ok(sources_reload_svc(), request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc, rid,
            default_error_code="E_SOURCES_RELOAD_FAILED",
            default_stage="system",
            default_message="sources reload failed",
        )


@router.post("/api/admin/v1/sources/upload")
async def admin_v1_sources_upload(
    file: UploadFile = File(...), request_id: str | None = None
):
    rid = _next_request_id(request_id)
    try:
        content = await file.read()
        return _api_ok(sources_upload_svc(file.filename or "", content), request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc, rid,
            default_error_code="E_SOURCES_UPLOAD_FAILED",
            default_stage="system",
            default_message="sources upload failed",
        )


@router.delete("/api/admin/v1/sources/{name}")
async def admin_v1_sources_delete(name: str, request_id: str | None = None):
    rid = _next_request_id(request_id)
    try:
        return _api_ok(sources_delete_svc(name), request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc, rid,
            default_error_code="E_SOURCES_DELETE_FAILED",
            default_stage="system",
            default_message="sources delete failed",
        )


@router.put("/api/admin/v1/sources/{name}/enable")
async def admin_v1_sources_enable(name: str, request_id: str | None = None):
    rid = _next_request_id(request_id)
    try:
        return _api_ok(sources_enable_svc(name), request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc, rid,
            default_error_code="E_SOURCES_ENABLE_FAILED",
            default_stage="system",
            default_message="sources enable failed",
        )


@router.put("/api/admin/v1/sources/{name}/disable")
async def admin_v1_sources_disable(name: str, request_id: str | None = None):
    rid = _next_request_id(request_id)
    try:
        return _api_ok(sources_disable_svc(name), request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc, rid,
            default_error_code="E_SOURCES_DISABLE_FAILED",
            default_stage="system",
            default_message="sources disable failed",
        )


# ── Auth Status ────────────────────────────────────────────────────────

@router.get("/api/admin/v1/auth/status")
async def admin_v1_auth_status(request_id: str | None = None):
    rid = _next_request_id(request_id)
    try:
        return _api_ok(await auth_status_v1_svc(), request_id=rid)
    except Exception as exc:
        return _map_api_exception(exc, rid)
