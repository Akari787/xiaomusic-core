from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


if "miservice" not in sys.modules:
    stub = types.ModuleType("miservice")

    async def _miio_command(*args, **kwargs):  # noqa: ARG001
        return None

    stub.miio_command = _miio_command
    sys.modules["miservice"] = stub


from xiaomusic.core.errors.transport_errors import TransportError
from xiaomusic.core.models.media import PlayOptions
from xiaomusic.playback.facade import PlaybackFacade


@pytest.mark.asyncio
async def test_local_library_play_fails_when_start_is_not_confirmed():
    captured: dict[str, object] = {}

    class _Core:
        async def play(self, request, device_id=None):
            captured["request"] = request
            captured["device_id"] = device_id
            raise TransportError("playback command accepted but device did not start playing")

    class _XM:
        def __init__(self):
            self.log = type(
                "L",
                (),
                {
                    "info": lambda *a, **k: None,
                    "warning": lambda *a, **k: None,
                    "error": lambda *a, **k: None,
                },
            )()

        @staticmethod
        def did_exist(did: str) -> bool:
            return did == "did-1"

    facade = PlaybackFacade(_XM())
    facade._core_coordinator = _Core()

    with pytest.raises(TransportError):
        await facade.play(
            device_id="did-1",
            query="Song A",
            source_hint="local_library",
            options=PlayOptions(
                title="Song A",
                context_hint={
                    "context_type": "playlist",
                    "context_name": "All",
                    "context_id": "All",
                },
                source_payload={
                    "source": "local_library",
                    "playlist_name": "All",
                    "music_name": "Song A",
                    "context_type": "playlist",
                    "context_name": "All",
                },
            ),
            request_id="rid-confirm",
        )

    request = captured["request"]
    assert captured["device_id"] == "did-1"
    assert request.context["source_payload"]["playlist_name"] == "All"
    assert request.context["source_payload"]["music_name"] == "Song A"


@pytest.mark.asyncio
async def test_local_library_play_succeeds_when_start_is_confirmed():
    captured: dict[str, object] = {}

    class _Dispatch:
        transport = "mina"
        data = {"accepted": True}

    class _Prepared:
        source = "local_library"
        final_url = "http://example.com/local/song-a.mp3"

    class _Resolved:
        media_id = "media-song-a"
        title = "Song A"
        source = "local_library"
        is_live = False

    class _Outcome:
        accepted = True
        started = True

    class _Core:
        async def play(self, request, device_id=None):
            captured["request"] = request
            captured["device_id"] = device_id
            return {
                "prepared_stream": _Prepared(),
                "resolved_media": _Resolved(),
                "dispatch": _Dispatch(),
                "outcome": _Outcome(),
                "delivery_plan": None,
            }

    class _XM:
        def __init__(self):
            self.log = type(
                "L",
                (),
                {
                    "info": lambda *a, **k: None,
                    "warning": lambda *a, **k: None,
                    "error": lambda *a, **k: None,
                },
            )()

        @staticmethod
        def did_exist(did: str) -> bool:
            return did == "did-1"

    facade = PlaybackFacade(_XM())
    facade._core_coordinator = _Core()

    out = await facade.play(
        device_id="did-1",
        query="Song A",
        source_hint="local_library",
        options=PlayOptions(
            title="Song A",
            context_hint={
                "context_type": "playlist",
                "context_name": "All",
                "context_id": "All",
            },
            source_payload={
                "source": "local_library",
                "playlist_name": "All",
                "music_name": "Song A",
                "context_type": "playlist",
                "context_name": "All",
            },
        ),
        request_id="rid-confirm-ok",
    )

    request = captured["request"]
    assert captured["device_id"] == "did-1"
    assert request.context["source_payload"]["playlist_name"] == "All"
    assert request.context["source_payload"]["music_name"] == "Song A"
    assert out["status"] == "playing"
    assert out["source_plugin"] == "local_library"
    assert out["transport"] == "mina"
