"""Behavior-test harness: a real XiaoMusicDevice wired to a fake speaker.

The only fake is at the cloud seam (``auth_manager.mina_call``): the fake
speaker records every command it receives and answers according to a
script. Everything else -- device player, runtime state machine, command
arbiter, timers, failure policy, music library -- is real code.

Tests drive the player only through public entry points (play a playlist,
next, stop, ...) and assert on what the speaker received and on the public
player state. They must not read or write private attributes, so the
internals can be refactored freely underneath them.

Time is scaled: ``asyncio.sleep`` inside the player modules runs
``TIME_SCALE`` times faster, so a 30 s song "ends" after 0.3 s of wall time
while every relative ordering stays the same.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import types
from dataclasses import dataclass, field
from typing import Any

from xiaomusic.config import Config, Device
from xiaomusic.const import PLAY_TYPE_ALL
from xiaomusic.music_library import MusicLibrary

TIME_SCALE = 100.0
DID = "did-1"
DEVICE_ID = "speaker-1"


class SpeakerTimeout(Exception):
    """What the Mina gateway raises for '远程控制超时 code 3012'."""


@dataclass
class Command:
    name: str
    url: str = ""
    title: str = ""


@dataclass
class FakeSpeaker:
    """Scripted stand-in for the Xiaomi cloud + speaker.

    ``fail_next[name]`` is a list of booleans consumed per call of that
    command: True -> raise SpeakerTimeout for that call.
    """

    commands: list[Command] = field(default_factory=list)
    playing_url: str = ""
    paused: bool = False
    fail_next: dict[str, list[bool]] = field(default_factory=dict)
    # Per-command latency in player seconds, e.g. {"play_by_music_url": 3}.
    latency: dict[str, float] = field(default_factory=dict)
    tts: list[str] = field(default_factory=list)
    # Returns the player's current track title; set by build_rig. URLs are
    # opaque proxy tokens, so the title is how tests tell songs apart.
    current_title: Any = None

    def fail(self, name: str, *pattern: bool) -> None:
        self.fail_next.setdefault(name, []).extend(pattern)

    def _should_fail(self, name: str) -> bool:
        queue = self.fail_next.get(name)
        return bool(queue) and queue.pop(0)

    # -- the seam -------------------------------------------------------
    async def mina_call(self, method_name: str, *args, retry: int = 1, ctx: str = "", **kwargs):
        # Title of the track the player meant when it issued the command.
        title_at_call = self.current_title() if self.current_title else ""
        await asyncio.sleep(self.latency.get(method_name, 0.0) / TIME_SCALE)
        if self._should_fail(method_name):
            self.commands.append(Command(f"{method_name}!timeout"))
            raise SpeakerTimeout(f"{method_name} timed out")
        if method_name in ("play_by_music_url", "play_by_url"):
            url = args[1] if len(args) > 1 else kwargs.get("url", "")
            if not title_at_call:
                # A one-off external URL has no current track until it starts;
                # raw harness URLs carry the song name.
                m = re.search(r"/Audio/([^/]+)/stream", url)
                title_at_call = m.group(1) if m else ""
            self.commands.append(Command("play", url, title_at_call))
            self.playing_url = url
            self.paused = False
            return {"code": 0}
        if method_name == "player_stop":
            self.commands.append(Command("stop"))
            self.playing_url = ""
            self.paused = False
            return {"code": 0}
        if method_name == "player_pause":
            # Mina's player_pause toggles play/pause on a loaded track.
            self.commands.append(Command("pause"))
            if self.playing_url:
                self.paused = not self.paused
            return {"code": 0}
        if method_name == "player_get_status":
            status = 1 if (self.playing_url and not self.paused) else 2
            info = json.dumps({"status": status, "volume": 30, "loop_type": 1})
            return {"code": 0, "data": {"code": 0, "info": info}}
        if method_name == "player_set_volume":
            return {"code": 0}
        if method_name == "text_to_speech":
            self.tts.append(str(args[1] if len(args) > 1 else ""))
            self.commands.append(Command("tts"))
            return {"code": 0}
        if method_name == "mina_request":
            return {"code": 0, "data": {"songList": []}}
        raise AssertionError(f"unexpected speaker command {method_name}")

    # -- helpers for assertions ----------------------------------------
    def played(self) -> list[str]:
        """Titles of the tracks actually handed to the speaker, in order."""
        return [c.title for c in self.commands if c.name == "play"]

    def timeline(self) -> list[str]:
        """Compact command log, e.g. ['stop', 'play:a', 'stop!timeout']."""
        return [f"play:{c.title}" if c.name == "play" else c.name for c in self.commands]


def song_url(name: str) -> str:
    return f"http://jf.test/Audio/{name}/stream.mp3?api_key=t"


def _music_list_json(playlists: dict[str, list[str]]) -> str:
    out = []
    for list_name, songs in playlists.items():
        out.append(
            {
                "name": list_name,
                "source": "jellyfin",
                "source_playlist_id": f"pl-{list_name}",
                "musics": [
                    {
                        "entity_id": f"jellyfin:{s}",
                        "source": "jellyfin",
                        "source_item_id": s,
                        "name": s,
                        "url": song_url(s),
                        "type": "music",
                    }
                    for s in songs
                ],
            }
        )
    return json.dumps(out, ensure_ascii=False)


def _scaled_asyncio() -> types.SimpleNamespace:
    real_sleep = asyncio.sleep

    async def sleep(delay, result=None):
        return await real_sleep(max(float(delay), 0.0) / TIME_SCALE, result)

    ns = types.SimpleNamespace(**{k: getattr(asyncio, k) for k in dir(asyncio) if not k.startswith("__")})
    ns.sleep = sleep
    return ns


@dataclass
class Rig:
    player: Any
    speaker: FakeSpeaker
    library: MusicLibrary

    # -- public driving helpers -----------------------------------------
    async def play_playlist(self, list_name: str, song: str = "") -> Any:
        return await self.player.play_music_list(list_name, song)

    async def play_url(self, song: str, playlist: str = "", **context_extra) -> Any:
        """The v1 API path: a pre-resolved URL with optional playlist context,
        as PlaybackFacade/Coordinator hand it to the device."""
        context: dict[str, Any] = {"title": song, **context_extra}
        if playlist:
            context["context_hint"] = {
                "context_type": "playlist",
                "context_id": playlist,
                "context_name": playlist,
            }
        resolved = {"title": song, "id": song}
        return await self.player.submit_external_url_play(
            url=song_url(song), context=context, resolved=resolved
        )

    def state(self):
        return self.player.get_runtime_state()

    async def settle(self, seconds: float = 0.0) -> None:
        """Let scaled time pass (``seconds`` of player time)."""
        await asyncio.sleep(seconds / TIME_SCALE + 0.01)

    async def wait_until(self, predicate, *, player_seconds: float = 60.0) -> bool:
        deadline = asyncio.get_running_loop().time() + player_seconds / TIME_SCALE + 1.0
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.005)
        return predicate()

    async def close(self) -> None:
        await self.player.stop(arg1="notts")
        await self.settle(5)
        await self.player.close_command_arbiter()


def build_rig(
    monkeypatch,
    tmp_path,
    *,
    playlists: dict[str, list[str]] | None = None,
    durations: dict[str, float] | None = None,
    play_type: int = PLAY_TYPE_ALL,
    config_overrides: dict[str, Any] | None = None,
) -> Rig:
    import xiaomusic.device_player as dp
    import xiaomusic.playback.command_arbiter as arbiter_mod
    from xiaomusic.device_player import XiaoMusicDevice

    scaled = _scaled_asyncio()
    monkeypatch.setattr(dp, "asyncio", scaled)
    monkeypatch.setattr(arbiter_mod, "asyncio", scaled)
    monkeypatch.setattr("xiaomusic.music_library.traverse_music_directory", lambda *a, **k: {})

    playlists = playlists or {"日语": ["a", "b", "c"]}
    durations = durations or {}

    music_dir = tmp_path / "music"
    music_dir.mkdir()
    config = Config(
        music_path=str(music_dir),
        download_path=str(music_dir / "download"),
        temp_path=str(music_dir / "tmp"),
        conf_path=str(tmp_path / "conf"),
        music_list_json=_music_list_json(playlists),
        disable_download=True,
    )
    for key, value in (config_overrides or {}).items():
        setattr(config, key, value)
    log = logging.getLogger("behavior")
    library = MusicLibrary(config=config, log=log, event_bus=None)
    library.gen_all_music_list()

    async def _duration(name: str) -> float:
        return float(durations.get(name, 30.0))

    library.get_music_duration = _duration

    speaker = FakeSpeaker()
    device = Device(
        did=DID,
        device_id=DEVICE_ID,
        hardware="OH2P",
        name="speaker",
        play_type=play_type,
        cur_playlist=next(iter(playlists)),
    )
    xm = types.SimpleNamespace(
        config=config,
        log=log,
        auth_manager=speaker,
        music_library=library,
        device_manager=types.SimpleNamespace(
            get_group_device_id_list=lambda group: [DEVICE_ID],
            get_group_devices=lambda group: {},
        ),
        js_plugin_manager=None,
        event_bus=None,
        last_download_result=None,
    )
    player = XiaoMusicDevice(xm, device, group_name="g")
    # Group fan-out helpers look players up through the device manager.
    xm.device_manager.get_group_devices = lambda group: {DID: player}
    speaker.current_title = player.get_cur_music
    return Rig(player=player, speaker=speaker, library=library)
