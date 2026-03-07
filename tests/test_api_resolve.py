from __future__ import annotations

import pytest

pytest.importorskip("aiofiles")

from xiaomusic.api.models import ResolveRequest
from xiaomusic.api.routers import v1
from xiaomusic.core.errors import SourceResolveError


@pytest.mark.asyncio
async def test_api_v1_resolve_success(monkeypatch):
    class _Facade:
        async def resolve(self, *, query, source_hint="auto", options=None, request_id=None):  # noqa: ANN001
            _ = (query, source_hint, options)
            return {
                "resolved": True,
                "source_plugin": "site_media",
                "request_id": request_id,
                "media": {
                    "media_id": "m1",
                    "title": "video",
                    "stream_url": "https://cdn.example.com/a.m4a",
                    "source": "site_media",
                    "is_live": False,
                },
                "extra": {},
            }

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    out = await v1.api_v1_resolve(ResolveRequest(query="https://youtube.com/watch?v=1"))
    assert out["code"] == 0
    assert out["request_id"]
    assert out["data"]["resolved"] is True
    assert out["data"]["source_plugin"] == "site_media"


@pytest.mark.asyncio
async def test_api_v1_resolve_error_mapping(monkeypatch):
    class _Facade:
        async def resolve(self, *, query, source_hint="auto", options=None, request_id=None):  # noqa: ANN001
            _ = (query, source_hint, options, request_id)
            raise SourceResolveError("resolve failed")

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    out = await v1.api_v1_resolve(ResolveRequest(query="https://youtube.com/watch?v=1"))
    assert out["code"] == 20002
    assert out["request_id"]
    assert out["message"] == "source resolve failed"
