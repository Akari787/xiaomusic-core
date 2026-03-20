from __future__ import annotations

import logging

import pytest

from xiaomusic.api.models import Did, DidVolume
from xiaomusic.api.routers import device


def _bind_test_logger(monkeypatch):
    monkeypatch.setattr(device, "log", logging.getLogger("test.legacy.device.router"))


@pytest.mark.asyncio
async def test_getplayerstatus_wrapper_uses_new_status_signature(monkeypatch, caplog):
    _bind_test_logger(monkeypatch)
    caplog.set_level(logging.WARNING)
    class _XM:
        @staticmethod
        def did_exist(did: str) -> bool:
            return did == "did-1"

    class _Facade:
        async def status(self, device_id: str):
            assert device_id == "did-1"
            return {"raw": {"status": 1, "volume": 50}}

    monkeypatch.setattr(device, "xiaomusic", _XM())
    monkeypatch.setattr(device, "_get_facade", lambda: _Facade())
    out = await device.getplayerstatus(did="did-1")
    assert out["status"] == 1
    assert "deprecated_endpoint endpoint=/getplayerstatus" in caplog.text


@pytest.mark.asyncio
async def test_setvolume_wrapper_stays_compatible(monkeypatch, caplog):
    _bind_test_logger(monkeypatch)
    caplog.set_level(logging.WARNING)
    class _XM:
        @staticmethod
        def did_exist(did: str) -> bool:
            return did == "did-1"

    class _Facade:
        async def set_volume(self, device_id: str, volume: int):
            assert device_id == "did-1"
            assert volume == 40
            return {"status": "ok"}

    monkeypatch.setattr(device, "xiaomusic", _XM())
    monkeypatch.setattr(device, "_get_facade", lambda: _Facade())
    out = await device.setvolume(DidVolume(did="did-1", volume=40))
    assert out["ret"] == "OK"
    assert out["volume"] == 40
    assert "deprecated_endpoint endpoint=/setvolume" in caplog.text


@pytest.mark.asyncio
async def test_stop_wrapper_calls_formal_stop(monkeypatch, caplog):
    _bind_test_logger(monkeypatch)
    caplog.set_level(logging.WARNING)
    class _XM:
        @staticmethod
        def did_exist(did: str) -> bool:
            return did == "did-1"

    called = {"stop": False}

    class _Facade:
        async def stop(self, device_id: str):
            assert device_id == "did-1"
            called["stop"] = True
            return {"status": "stopped", "transport": "mina", "extra": {"dispatch": {}}}

    monkeypatch.setattr(device, "xiaomusic", _XM())
    monkeypatch.setattr(device, "_get_facade", lambda: _Facade())
    out = await device.stop(Did(did="did-1"))
    assert out["ret"] == "OK"
    assert called["stop"] is True
    assert "deprecated_endpoint endpoint=/device/stop" in caplog.text
