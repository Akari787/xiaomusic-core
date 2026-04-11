from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PathCheck:
    path: str
    exists: bool
    is_dir: bool
    readable: bool
    writable: bool
    error: str = ""


@dataclass
class ToolCheck:
    name: str
    found: bool
    version: str = ""
    error: str = ""


@dataclass
class StartupDiagnostics:
    ok: bool = True
    checked_at: float = field(default_factory=lambda: time.time())
    paths: list[PathCheck] = field(default_factory=list)
    tools: list[ToolCheck] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _check_path(path: str) -> PathCheck:
    path = path or ""
    exists = os.path.exists(path)
    is_dir = os.path.isdir(path)
    readable = False
    writable = False
    err = ""
    try:
        if exists:
            readable = os.access(path, os.R_OK)
            writable = os.access(path, os.W_OK)
            # If dir is writable, do a small write test.
            if is_dir and writable:
                test_file = os.path.join(path, ".xiaomusic_write_test")
                with open(test_file, "w", encoding="utf-8") as f:
                    f.write("ok")
                os.remove(test_file)
    except Exception as e:
        err = str(e)
    return PathCheck(
        path=path,
        exists=exists,
        is_dir=is_dir,
        readable=readable,
        writable=writable,
        error=err,
    )


def _run_version(cmd: list[str]) -> tuple[bool, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        out = (p.stdout or "") + (p.stderr or "")
        out = out.strip().splitlines()
        return True, (out[0] if out else ""), ""
    except Exception as e:
        return False, "", str(e)


def build_startup_diagnostics(config) -> StartupDiagnostics:
    d = StartupDiagnostics()
    # Paths
    for p in [config.music_path, config.download_path, config.temp_path, config.cache_dir, config.conf_path]:
        ck = _check_path(p)
        d.paths.append(ck)
        if not (ck.exists and ck.is_dir and ck.readable):
            d.ok = False

    # Tools
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if config.ffmpeg_location:
        # Best-effort: if ffmpeg_location points to a dir, check inside.
        if os.path.isdir(config.ffmpeg_location):
            for n in ("ffmpeg", "ffprobe"):
                cand = os.path.join(config.ffmpeg_location, n)
                if os.path.exists(cand):
                    if n == "ffmpeg":
                        ffmpeg = cand
                    else:
                        ffprobe = cand

    for name, path in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        if not path:
            d.tools.append(ToolCheck(name=name, found=False, error="not found"))
            d.ok = False
            continue
        ok, ver, err = _run_version([path, "-version"])
        d.tools.append(ToolCheck(name=name, found=ok, version=ver, error=err))
        if not ok:
            d.ok = False

    if not d.ok:
        d.notes.append(
            "If running in Docker, ensure /app/music and /app/conf are mounted and writable."
        )
        d.notes.append(
            "If ffmpeg is missing, install ffmpeg or set XIAOMUSIC_FFMPEG_LOCATION to a valid path."
        )

    return d


_STATUS_OK = "ok"
_STATUS_DEGRADED = "degraded"
_STATUS_FAILED = "failed"
_STATUS_UNKNOWN = "unknown"


def _area(status: str, summary: str, last_failure: str = "", data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "summary": summary,
        "last_failure": str(last_failure or ""),
        "data": data or {},
    }


def _startup_area(config, xiaomusic) -> dict[str, Any]:
    startup = getattr(xiaomusic, "startup_diagnostics", None)
    if startup is None:
        try:
            startup = build_startup_diagnostics(config)
        except Exception as exc:
            return _area(
                _STATUS_UNKNOWN,
                "startup diagnostics unavailable",
                last_failure=str(exc),
                data={"ok": None, "checked_at": None, "notes": []},
            )

    startup_dict = asdict(startup)
    paths = startup_dict.get("paths") or []
    tools = startup_dict.get("tools") or []
    last_failure = ""
    if not startup_dict.get("ok", False):
        for path in paths:
            if not (path.get("exists") and path.get("is_dir") and path.get("readable")):
                last_failure = f"path check failed: {path.get('path', '')}"
                break
        if not last_failure:
            for tool in tools:
                if not tool.get("found"):
                    detail = str(tool.get("error") or "not found")
                    last_failure = f"tool check failed: {tool.get('name', '')} ({detail})"
                    break
        if not last_failure:
            last_failure = "; ".join(str(note) for note in (startup_dict.get("notes") or [])[:1])

    status = _STATUS_OK if bool(startup_dict.get("ok", False)) else _STATUS_FAILED
    summary = "startup checks passed" if status == _STATUS_OK else "startup checks failed"
    return _area(
        status,
        summary,
        last_failure=last_failure,
        data={
            "ok": startup_dict.get("ok"),
            "checked_at": startup_dict.get("checked_at"),
            "notes": list(startup_dict.get("notes") or []),
            "keyword_override_mode": getattr(config, "keyword_override_mode", "override"),
            "keyword_conflicts": list(getattr(config, "keyword_conflicts", []) or []),
            "last_download_result": getattr(xiaomusic, "last_download_result", None),
        },
    )


def _auth_area(auth_payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = auth_payload or {}
    if not payload:
        return _area(
            _STATUS_UNKNOWN,
            "auth status unavailable",
            data={
                "runtime_auth_ready": None,
                "persistent_auth_available": None,
                "short_session_available": None,
                "auth_mode": "unknown",
                "auth_locked": None,
                "status_reason": "unknown",
                "status_reason_detail": "",
            },
        )

    reason = str(payload.get("status_reason") or "unknown")
    detail = str(payload.get("status_reason_detail") or payload.get("last_error") or "")
    runtime_ready = bool(payload.get("runtime_auth_ready", False))
    auth_locked = bool(payload.get("auth_locked", False))
    persistent_auth_available = bool(payload.get("persistent_auth_available", False))
    short_session_available = bool(payload.get("short_session_available", False))

    if runtime_ready and not auth_locked and reason == "healthy":
        status = _STATUS_OK
        summary = "persistent auth and runtime auth are ready"
    elif reason in {"manual_login_required", "persistent_auth_missing"}:
        status = _STATUS_FAILED
        summary = "auth startup checks failed"
    elif reason in {
        "temporarily_locked",
        "short_session_rebuild_failed",
        "short_session_missing",
        "runtime_not_ready",
    }:
        status = _STATUS_DEGRADED
        summary = "auth is present but not fully ready"
    else:
        status = _STATUS_UNKNOWN
        summary = "auth readiness is unknown"

    return _area(
        status,
        summary,
        last_failure=detail,
        data={
            "runtime_auth_ready": runtime_ready,
            "persistent_auth_available": persistent_auth_available,
            "short_session_available": short_session_available,
            "auth_mode": str(payload.get("auth_mode") or "unknown"),
            "auth_locked": auth_locked,
            "status_reason": reason,
            "status_reason_detail": str(payload.get("status_reason_detail") or ""),
        },
    )


def _local_library_source_item(config, xiaomusic) -> dict[str, Any]:
    path_check = _check_path(str(getattr(config, "music_path", "") or ""))
    music_library = getattr(xiaomusic, "music_library", None)
    all_music = getattr(music_library, "all_music", None)
    indexed = isinstance(all_music, dict)
    indexed_count = len(all_music) if indexed else 0
    if path_check.exists and path_check.is_dir and path_check.readable and indexed:
        return {
            "source": "local_library",
            "status": _STATUS_OK,
            "summary": f"music path ready; indexed_tracks={indexed_count}",
            "last_failure": "",
        }
    if path_check.exists and path_check.is_dir and path_check.readable:
        return {
            "source": "local_library",
            "status": _STATUS_DEGRADED,
            "summary": "music path readable but library index not initialized",
            "last_failure": "music_library.all_music unavailable",
        }
    failure = path_check.error or f"music path not readable: {path_check.path}"
    return {
        "source": "local_library",
        "status": _STATUS_FAILED,
        "summary": "music path unavailable",
        "last_failure": failure,
    }


def _jellyfin_source_item(config) -> dict[str, Any]:
    enabled = bool(getattr(config, "jellyfin_enabled", False))
    base_url = str(getattr(config, "jellyfin_base_url", "") or "").strip()
    api_key = str(getattr(config, "jellyfin_api_key", "") or "").strip()
    if not enabled and not base_url and not api_key:
        return {
            "source": "jellyfin",
            "status": _STATUS_UNKNOWN,
            "summary": "jellyfin not configured",
            "last_failure": "",
        }
    if base_url and api_key:
        return {
            "source": "jellyfin",
            "status": _STATUS_OK,
            "summary": "jellyfin configuration is present",
            "last_failure": "",
        }
    return {
        "source": "jellyfin",
        "status": _STATUS_FAILED,
        "summary": "jellyfin configuration is incomplete",
        "last_failure": "missing jellyfin_base_url or jellyfin_api_key",
    }


def _direct_url_source_item() -> dict[str, Any]:
    return {
        "source": "direct_url",
        "status": _STATUS_OK,
        "summary": "direct URL source is stateless and available",
        "last_failure": "",
    }


def _site_media_source_item(xiaomusic) -> dict[str, Any]:
    online_music_service = getattr(xiaomusic, "online_music_service", None)
    js_plugin_manager = getattr(xiaomusic, "js_plugin_manager", None)
    if online_music_service is not None or js_plugin_manager is not None:
        return {
            "source": "site_media",
            "status": _STATUS_OK,
            "summary": "site media runtime is initialized",
            "last_failure": "",
        }
    return {
        "source": "site_media",
        "status": _STATUS_UNKNOWN,
        "summary": "site media runtime not initialized",
        "last_failure": "",
    }


def _aggregate_items_area(
    items: list[dict[str, Any]],
    *,
    item_name: str,
    ready_key: str,
    failed_key: str,
) -> dict[str, Any]:
    ready_count = sum(1 for item in items if item.get("status") == _STATUS_OK)
    degraded_count = sum(1 for item in items if item.get("status") == _STATUS_DEGRADED)
    failed_count = sum(1 for item in items if item.get("status") == _STATUS_FAILED)
    unknown_count = sum(1 for item in items if item.get("status") == _STATUS_UNKNOWN)
    last_failure = next(
        (str(item.get("last_failure") or "") for item in items if item.get("status") == _STATUS_FAILED and item.get("last_failure")),
        "",
    )
    if failed_count > 0:
        status = _STATUS_FAILED
    elif degraded_count > 0:
        status = _STATUS_DEGRADED
    elif items and unknown_count == len(items):
        status = _STATUS_UNKNOWN
    elif items and ready_count == len(items):
        status = _STATUS_OK
    else:
        status = _STATUS_UNKNOWN

    summary = (
        f"{ready_count} {item_name} ready, {failed_count} failed, {unknown_count} unknown"
    )
    return _area(
        status,
        summary,
        last_failure=last_failure,
        data={
            ready_key: ready_count,
            "degraded_count": degraded_count,
            failed_key: failed_count,
            "unknown_count": unknown_count,
            "items": items,
        },
    )


def _sources_area(config, xiaomusic) -> dict[str, Any]:
    items = [
        _local_library_source_item(config, xiaomusic),
        _jellyfin_source_item(config),
        _direct_url_source_item(),
        _site_media_source_item(xiaomusic),
    ]
    area = _aggregate_items_area(
        items,
        item_name="sources",
        ready_key="ready_count",
        failed_key="failed_count",
    )
    if area["status"] == _STATUS_OK:
        area["summary"] = "all built-in sources are ready"
    elif area["status"] == _STATUS_UNKNOWN:
        area["summary"] = "source readiness is partially unknown"
    return area


def _get_device_reachability_cache() -> dict[str, Any]:
    try:
        from xiaomusic.api.routers import v1

        facade = getattr(v1, "_facade", None)
        core = getattr(facade, "_core_coordinator", None)
        registry = getattr(core, "_device_registry", None)
        cache = getattr(registry, "_reachability", None)
        if isinstance(cache, dict):
            return cache
    except Exception:
        pass
    return {}


def _devices_area(xiaomusic) -> dict[str, Any]:
    devices = getattr(getattr(xiaomusic, "device_manager", None), "devices", {}) or {}
    if not isinstance(devices, dict):
        devices = {}
    reachability_cache = _get_device_reachability_cache()
    items: list[dict[str, Any]] = []
    reachable = 0
    unreachable = 0
    unknown = 0
    for device_id, device_player in devices.items():
        reachability = reachability_cache.get(device_id)
        device_name = str(getattr(getattr(device_player, "device", None), "name", device_id) or device_id)
        if reachability is None:
            unknown += 1
            items.append(
                {
                    "device_id": device_id,
                    "name": device_name,
                    "status": _STATUS_UNKNOWN,
                    "summary": "no cached reachability probe",
                    "last_failure": "",
                    "reachability": None,
                }
            )
            continue

        local_reachable = bool(getattr(reachability, "local_reachable", False))
        cloud_reachable = bool(getattr(reachability, "cloud_reachable", False))
        payload = {
            "ip": str(getattr(reachability, "ip", "") or ""),
            "local_reachable": local_reachable,
            "cloud_reachable": cloud_reachable,
            "last_probe_ts": int(getattr(reachability, "last_probe_ts", 0) or 0),
        }
        if local_reachable or cloud_reachable:
            reachable += 1
            mode = "local/cloud" if local_reachable and cloud_reachable else "local" if local_reachable else "cloud"
            items.append(
                {
                    "device_id": device_id,
                    "name": device_name,
                    "status": _STATUS_OK,
                    "summary": f"{mode} reachable",
                    "last_failure": "",
                    "reachability": payload,
                }
            )
        else:
            unreachable += 1
            items.append(
                {
                    "device_id": device_id,
                    "name": device_name,
                    "status": _STATUS_FAILED,
                    "summary": "last probe marked device unreachable",
                    "last_failure": "device unreachable",
                    "reachability": payload,
                }
            )

    if not items:
        return _area(
            _STATUS_UNKNOWN,
            "no devices registered",
            data={
                "total": 0,
                "reachable": 0,
                "unreachable": 0,
                "unknown": 0,
                "items": [],
            },
        )

    if unreachable > 0:
        status = _STATUS_FAILED
    elif unknown > 0:
        status = _STATUS_UNKNOWN
    else:
        status = _STATUS_OK
    summary = f"{len(items)} devices known, {reachable} reachable, {unreachable} unreachable, {unknown} unknown"
    return _area(
        status,
        summary,
        last_failure="device unreachable" if unreachable > 0 else "",
        data={
            "total": len(items),
            "reachable": reachable,
            "unreachable": unreachable,
            "unknown": unknown,
            "items": items,
        },
    )


def _playback_readiness_area() -> dict[str, Any]:
    return _area(
        _STATUS_UNKNOWN,
        "structured playback readiness check not implemented",
        data={
            "can_resolve_source": None,
            "can_dispatch_transport": None,
            "requires_auth": None,
            "notes": [],
        },
    )


def _build_overall_status(areas: dict[str, dict[str, Any]]) -> str:
    critical = [areas.get("startup", {}), areas.get("auth", {})]
    if any(area.get("status") == _STATUS_FAILED for area in critical):
        return _STATUS_FAILED
    if any(area.get("status") == _STATUS_DEGRADED for area in areas.values()):
        return _STATUS_DEGRADED
    if areas and all(area.get("status") == _STATUS_OK for area in areas.values()):
        return _STATUS_OK
    return _STATUS_UNKNOWN


def _build_overall_summary(overall_status: str, areas: dict[str, dict[str, Any]]) -> str:
    if overall_status == _STATUS_OK:
        return "startup/self-check diagnostics are healthy"
    if overall_status == _STATUS_DEGRADED:
        degraded_names = [name for name, area in areas.items() if area.get("status") == _STATUS_DEGRADED]
        if degraded_names:
            return f"startup/self-check degraded: {', '.join(degraded_names)}"
        return "startup/self-check degraded"
    if overall_status == _STATUS_FAILED:
        failed_names = [name for name, area in areas.items() if area.get("status") == _STATUS_FAILED]
        if failed_names:
            return f"startup/self-check failed: {', '.join(failed_names)}"
        return "startup/self-check failed"
    unknown_names = [name for name, area in areas.items() if area.get("status") == _STATUS_UNKNOWN]
    if unknown_names:
        return f"startup/self-check partially unknown: {', '.join(unknown_names)}"
    return "startup/self-check status unknown"


def build_runtime_diagnostics_view(config, xiaomusic, auth_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    areas = {
        "startup": _startup_area(config, xiaomusic),
        "auth": _auth_area(auth_payload),
        "sources": _sources_area(config, xiaomusic),
        "devices": _devices_area(xiaomusic),
        "playback_readiness": _playback_readiness_area(),
    }
    overall_status = _build_overall_status(areas)
    return {
        "generated_at_ms": int(time.time() * 1000),
        "overall_status": overall_status,
        "summary": _build_overall_summary(overall_status, areas),
        "areas": areas,
    }
