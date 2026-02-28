import pytest

pytest.importorskip("aiofiles")

from xiaomusic.api.routers import file as file_router


@pytest.mark.asyncio
async def test_music_endpoint_returns_removed_error_for_legacy_key_code():
    resp = await file_router.music_file(None, "demo.mp3", key="legacy", code="")
    assert resp.status_code == 410
    body = resp.body.decode("utf-8")
    assert '"ok":false' in body
    assert '"success":false' in body
    assert '"error_code":"E_LEGACY_LINK_AUTH_REMOVED"' in body


@pytest.mark.asyncio
async def test_picture_endpoint_returns_removed_error_for_legacy_key_code():
    resp = await file_router.get_picture(None, "cover.jpg", key="", code="legacy")
    assert resp.status_code == 410
    body = resp.body.decode("utf-8")
    assert '"ok":false' in body
    assert '"success":false' in body
    assert '"error_code":"E_LEGACY_LINK_AUTH_REMOVED"' in body
