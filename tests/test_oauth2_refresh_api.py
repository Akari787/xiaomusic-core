import pytest

pytest.importorskip("aiofiles")

from xiaomusic.api.routers import system


@pytest.mark.asyncio
async def test_oauth2_refresh_api_contract(monkeypatch):
    class _AuthManager:
        async def manual_refresh(self, reason="manual_refresh"):
            assert reason == "manual_refresh"
            return {
                "refreshed": True,
                "runtime_auth_ready": True,
                "token_saved": True,
                "last_error": None,
                "timestamps": {
                    "saveTime": 1,
                    "last_ok_ts": 2,
                    "last_refresh_ts": 3,
                },
            }

    monkeypatch.setattr(system, "xiaomusic", type("_XM", (), {"auth_manager": _AuthManager()})())

    out = await system.oauth2_refresh()
    assert out["refreshed"] is True
    assert out["runtime_auth_ready"] is True
    assert out["token_saved"] is True
    assert out["last_error"] is None
    assert set(out["timestamps"].keys()) == {"saveTime", "last_ok_ts", "last_refresh_ts"}
