from __future__ import annotations

import base64
import importlib
from types import SimpleNamespace

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

from xiaomusic.api.routers import register_routers


def _current_file_router():
    return importlib.import_module("xiaomusic.api.routers.file")


def _current_dependencies():
    return importlib.import_module("xiaomusic.api.dependencies")


def _basic(username: str, password: str) -> dict[str, str]:
    value = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {value}"}


def _media_config(tmp_path, *, disable_httpauth=False):
    music_path = tmp_path / "music"
    picture_path = tmp_path / "picture"
    music_path.mkdir()
    picture_path.mkdir()
    return SimpleNamespace(
        disable_httpauth=disable_httpauth,
        music_path=str(music_path),
        picture_cache_path=str(picture_path),
        remove_id3tag=False,
        convert_to_mp3=False,
        get_self_netloc=lambda: "127.0.0.1",
        get_basic_auth=lambda: "Basic unused",
    ), music_path, picture_path


def test_production_assembly_keeps_media_public_and_management_private(monkeypatch, tmp_path):
    file_router = _current_file_router()
    dependencies = _current_dependencies()
    config, music_path, picture_path = _media_config(tmp_path)
    (music_path / "song.mp3").write_bytes(b"music")
    (picture_path / "cover.jpg").write_bytes(b"picture")
    monkeypatch.setattr(file_router, "config", config)
    monkeypatch.setattr(file_router, "xiaomusic", SimpleNamespace())
    monkeypatch.setattr(dependencies, "config", config)
    password = "route-password"
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    monkeypatch.setattr(
        dependencies,
        "get_auth_settings",
        lambda: SimpleNamespace(HTTP_AUTH_HASH=hashed),
    )

    app = FastAPI()
    register_routers(app)
    client = TestClient(app)

    assert client.get("/music/song.mp3").status_code == 200
    assert client.options("/music/song.mp3").status_code == 200
    assert client.get("/music/missing.mp3").status_code == 404
    assert client.get("/picture/cover.jpg").status_code == 200
    assert client.get("/picture/missing.jpg").status_code == 404
    assert client.get("/api/js-plugins").status_code == 401

    management_requests = [
        ("post", "/api/file/cleantempdir", {}),
        ("post", "/downloadjson", {}),
        ("post", "/api/file/fetch_playlist_json", {}),
        ("post", "/downloadplaylist", {}),
        ("post", "/downloadonemusic", {}),
        ("post", "/uploadytdlpcookie", {"files": {"file": ("x.txt", b"x")}}),
        ("post", "/uploadmusic", {"data": {"playlist": "其他"}, "files": {"file": ("x.mp3", b"x")}}),
    ]
    for method, path, kwargs in management_requests:
        assert getattr(client, method)(path, **kwargs).status_code == 401, path


def test_static_files_only_allow_silence_and_search_anonymously(monkeypatch, tmp_path):
    dependencies = _current_dependencies()
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    for name in ("silence.mp3", "search.mp3", "index.html"):
        (static_dir / name).write_bytes(b"static")
    password = "static-password"
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    monkeypatch.setattr(
        dependencies,
        "config",
        SimpleNamespace(disable_httpauth=False, httpauth_username="admin"),
    )
    monkeypatch.setattr(
        dependencies,
        "get_auth_settings",
        lambda: SimpleNamespace(HTTP_AUTH_HASH=hashed),
    )

    app = FastAPI()
    app.mount("/static", dependencies.AuthStaticFiles(directory=static_dir), name="static")
    client = TestClient(app)

    assert client.get("/static/silence.mp3").status_code == 200
    assert client.get("/static/search.mp3").status_code == 200
    assert client.get("/static/index.html").status_code == 401
    assert client.get(
        "/static/index.html", headers=_basic("admin", password)
    ).status_code == 200


def test_proxy_token_and_legacy_auth_boundaries(monkeypatch, tmp_path):
    file_router = _current_file_router()
    dependencies = _current_dependencies()
    config, _, _ = _media_config(tmp_path, disable_httpauth=False)
    password = "proxy-password"
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    monkeypatch.setattr(file_router, "config", config)
    monkeypatch.setattr(
        dependencies,
        "config",
        SimpleNamespace(
            disable_httpauth=False,
            httpauth_username="admin",
        ),
    )
    monkeypatch.setattr(
        dependencies,
        "get_auth_settings",
        lambda: SimpleNamespace(HTTP_AUTH_HASH=hashed),
    )
    resolve_calls = []
    session_constructions = []

    class _NoNetworkClientSession:
        def __init__(self, *args, **kwargs):
            session_constructions.append((args, kwargs))
            raise AssertionError("invalid proxy token must not construct a network session")

    monkeypatch.setattr(file_router.aiohttp, "ClientSession", _NoNetworkClientSession)

    class _Library:
        def resolve_proxy_url_token(self, token):
            resolve_calls.append(token)
            return ""

    monkeypatch.setattr(file_router, "xiaomusic", SimpleNamespace(music_library=_Library()))
    app = FastAPI()
    app.include_router(file_router.media_router)
    client = TestClient(app)

    invalid = client.get("/proxy", params={"urlb64": "t.invalid"})
    assert 400 <= invalid.status_code < 500
    assert resolve_calls == ["invalid"]
    assert session_constructions == []

    calls = []

    async def _fake_proxy(urlb64, is_radio):
        calls.append((urlb64, is_radio))
        return Response(content=b"ok")

    monkeypatch.setattr(file_router, "_proxy_handler", _fake_proxy)
    legacy = base64.urlsafe_b64encode(b"https://example.com/a.mp3").decode().rstrip("=")
    assert client.get("/proxy", params={"urlb64": "t.valid"}).status_code == 200
    assert client.get("/proxy", params={"urlb64": legacy}).status_code == 401
    assert client.get(
        "/proxy", params={"urlb64": legacy}, headers=_basic("admin", "wrong")
    ).status_code == 401
    assert client.get(
        "/proxy", params={"urlb64": legacy}, headers=_basic("admin", password)
    ).status_code == 200

    config.disable_httpauth = True
    assert client.get("/proxy", params={"urlb64": legacy}).status_code == 200
    assert len(calls) == 3


def test_media_paths_reject_sibling_prefix_on_all_platforms(monkeypatch, tmp_path):
    file_router = _current_file_router()
    config, music_path, picture_path = _media_config(tmp_path)
    (music_path.parent / "music_evil").mkdir()
    (music_path.parent / "music_evil" / "secret.mp3").write_bytes(b"secret")
    (picture_path.parent / "picture_evil").mkdir()
    (picture_path.parent / "picture_evil" / "secret.jpg").write_bytes(b"secret")

    monkeypatch.setattr(file_router, "config", config)
    app = FastAPI()
    app.include_router(file_router.media_router)
    client = TestClient(app)

    assert client.get("/music/%2E%2E/music_evil/secret.mp3").status_code == 404
    assert client.get("/picture/%2E%2E/picture_evil/secret.jpg").status_code == 404


def test_media_paths_reject_symlink_escape_when_supported(monkeypatch, tmp_path):
    file_router = _current_file_router()
    config, music_path, _ = _media_config(tmp_path)
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"outside")
    symlink = music_path / "link.mp3"
    try:
        symlink.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable on this platform")

    monkeypatch.setattr(file_router, "config", config)
    app = FastAPI()
    app.include_router(file_router.media_router)
    assert TestClient(app).get("/music/link.mp3").status_code == 404
