from __future__ import annotations

from pathlib import Path


def test_router_registration_marks_internal_routers_hidden_from_schema():
    text = Path("xiaomusic/api/routers/__init__.py").read_text(encoding="utf-8")

    assert 'app.include_router(system.router, tags=["系统管理"], include_in_schema=False)' in text
    assert 'app.include_router(device.router, tags=["设备控制"], dependencies=[auth_dep], include_in_schema=False)' in text
    assert 'app.include_router(music.router, tags=["音乐管理"], dependencies=[auth_dep], include_in_schema=False)' in text
    assert 'app.include_router(file.router, tags=["文件操作"], dependencies=[auth_dep], include_in_schema=False)' in text


def test_internal_route_files_have_internal_api_comments():
    system_text = Path("xiaomusic/api/routers/system.py").read_text(encoding="utf-8")
    file_text = Path("xiaomusic/api/routers/file.py").read_text(encoding="utf-8")

    assert "Internal API - 仅供 WebUI/内部认证流程使用，不承诺兼容性。" in system_text
    assert "Internal API - 仅供 WebUI/内部文件流程使用，不承诺兼容性。" in file_text


def test_removed_and_internal_routes_not_promoted_into_public_whitelist():
    text = Path("tests/test_response_consistency.py").read_text(encoding="utf-8")

    assert '"/api/auth/status"' not in text
    assert '"/api/file/fetch_playlist_json"' not in text
    assert '"/refreshmusictag"' not in text
    assert '"/api/v1/playlist/play"' not in text
    assert '"/api/v1/playlist/play-index"' not in text
