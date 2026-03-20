from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xiaomusic.api.dependencies import verification
from xiaomusic.api.routers import file, music, v1


def _internal_client() -> TestClient:
    app = FastAPI()
    app.include_router(file.router)
    app.include_router(music.router)
    app.dependency_overrides[verification] = lambda: True
    return TestClient(app)


def _v1_client() -> TestClient:
    app = FastAPI()
    app.include_router(v1.router)
    return TestClient(app)


def test_refreshmusictag_removed_from_internal_routes() -> None:
    client = _internal_client()
    assert client.post("/refreshmusictag").status_code == 404


def test_fetch_playlist_json_requires_non_empty_url() -> None:
    client = _internal_client()
    resp = client.post("/api/file/fetch_playlist_json", json={"url": ""})
    assert resp.status_code == 200
    assert resp.json()["ret"] == "URL required"


def test_fetch_playlist_json_rejects_non_http_scheme() -> None:
    client = _internal_client()
    resp = client.post("/api/file/fetch_playlist_json", json={"url": "file:///tmp/test.json"})
    assert resp.status_code == 200
    assert resp.json()["ret"] == "URL must use http or https"


def test_fetch_playlist_json_requires_json_payload(monkeypatch) -> None:
    async def _fake_downloadfile(url, config):  # noqa: ANN001
        _ = (url, config)
        return "not-json"

    monkeypatch.setattr(file, "downloadfile", _fake_downloadfile)
    monkeypatch.setattr(file, "log", type("_Log", (), {"info": staticmethod(lambda *args, **kwargs: None)})())

    client = _internal_client()
    resp = client.post("/api/file/fetch_playlist_json", json={"url": "https://example.com/list.json"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ret"] == "Playlist JSON required"
    assert body["content"] == "not-json"


def test_fetch_playlist_json_success_for_valid_json(monkeypatch) -> None:
    async def _fake_downloadfile(url, config):  # noqa: ANN001
        _ = (url, config)
        return '{"items":[1,2,3]}'

    monkeypatch.setattr(file, "downloadfile", _fake_downloadfile)
    monkeypatch.setattr(file, "log", type("_Log", (), {"info": staticmethod(lambda *args, **kwargs: None)})())

    client = _internal_client()
    resp = client.post("/api/file/fetch_playlist_json", json={"url": "https://example.com/list.json"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ret"] == "OK"
    assert body["content"] == '{"items":[1,2,3]}'


def test_cleantempdir_and_public_refresh_can_coexist(monkeypatch) -> None:
    called = {"clean": False, "refresh": False}

    async def _fake_clean_temp_dir(config):  # noqa: ANN001
        _ = config
        called["clean"] = True

    class _XM:
        async def gen_music_list(self, **kwargs):
            _ = kwargs
            called["refresh"] = True

    monkeypatch.setattr(file, "clean_temp_dir", _fake_clean_temp_dir)
    monkeypatch.setattr(file, "xiaomusic", type("_Fxm", (), {"config": object()})())
    monkeypatch.setattr(file, "log", type("_Log", (), {"info": staticmethod(lambda *args, **kwargs: None)})())
    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())

    internal_client = _internal_client()
    public_client = _v1_client()

    assert internal_client.post("/api/file/cleantempdir").status_code == 200
    assert public_client.post("/api/v1/library/refresh", json={}).status_code == 200
    assert called == {"clean": True, "refresh": True}
