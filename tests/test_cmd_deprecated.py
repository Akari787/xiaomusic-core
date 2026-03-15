from __future__ import annotations

import json

import pytest

from xiaomusic.api.models import DidCmd
from xiaomusic.api.routers import device


@pytest.mark.asyncio
async def test_cmd_returns_410_deprecated_response(monkeypatch) -> None:
    class _Log:
        @staticmethod
        def warning(*args, **kwargs):
            _ = (args, kwargs)

    monkeypatch.setattr(device, "log", _Log())
    resp = await device.do_cmd(DidCmd(did="981257654", cmd="上一首"))
    assert resp.status_code == 410
    body = json.loads(resp.body)
    assert body["success"] is False
    assert body["deprecated"] is True
    assert body["message"] == "/cmd has been deprecated; use structured /api/v1/* endpoints instead"
    assert "/api/v1/control/previous" in body["replacement"]
    assert "/api/v1/playlist/play-index" in body["replacement"]
