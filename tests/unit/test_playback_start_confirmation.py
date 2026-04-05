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
    calls: list[tuple[str, str, str]] = []

    class _Core:
        async def _confirm_playback_started(
            self, device_id: str, request_context: dict
        ):  # noqa: ARG002
            return False

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
            self._core = _Core()

        @staticmethod
        def did_exist(did: str) -> bool:
            return did == "did-1"

        async def do_play_music_list(
            self, did: str, playlist_name: str, music_name: str
        ):
            calls.append((did, playlist_name, music_name))
            return False

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

    assert calls == [("did-1", "All", "Song A")]


@pytest.mark.asyncio
async def test_local_library_play_succeeds_when_start_is_confirmed():
    calls: list[tuple[str, str, str]] = []

    class _Core:
        async def _confirm_playback_started(
            self, device_id: str, request_context: dict
        ):  # noqa: ARG002
            return True

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

        async def do_play_music_list(
            self, did: str, playlist_name: str, music_name: str
        ):
            calls.append((did, playlist_name, music_name))
            return True

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

    assert calls == [("did-1", "All", "Song A")]
    assert out["status"] == "playing"
    assert out["source_plugin"] == "local_library"
