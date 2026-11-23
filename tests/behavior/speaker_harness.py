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

With ``queue=True`` the rig also has a real StreamGateway, and the fake
speaker behaves like the OH2P with a multi-item queue (ADR-0007): it plays
the dispatched items in order on its own, fetches each one through the
gateway when it starts (which drives the player's following), and honours
the loop type.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
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
    # Queue mode (build_rig(queue=True)).
    gateway: Any = None
    duration_of: Any = None  # title -> seconds
    loop_type: int = 1
    heard_log: list[str] = field(default_factory=list)
    # Indices (by position in a dispatched queue) whose fetch never reaches
    # the gateway, e.g. {1}: simulates a lost fetch.
    drop_fetch: set[int] = field(default_factory=set)
    _queue_task: Any = None

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
                if self.gateway is not None and "/stream/v1/" in url:
                    title_at_call = self._title(self._ref(url))
                else:
                    m = re.search(r"/Audio/([^/]+)/stream", url)
                    title_at_call = m.group(1) if m else ""
            self.commands.append(Command("play", url, title_at_call))
            self.playing_url = url
            self.paused = False
            return {"code": 0}
        if method_name == "ubus_request":
            return self._ubus(args[1], args[3])
        if method_name == "player_stop":
            self.commands.append(Command("stop"))
            self._stop_queue()
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

    # -- queue mode -----------------------------------------------------
    def _ubus(self, method: str, message: Any):
        if method == "player_set_loop":
            self.loop_type = int(message["type"])
            self.commands.append(Command(f"loop:{self.loop_type}"))
            return {"code": 0}
        if method == "player_play_music":
            music = json.loads(message["music"])
            items = music["payload"]["audio_items"]
            ids = [it["item_id"]["audio_id"] for it in items]
            refs = [self._ref(it["stream"]["url"]) for it in items]
            start = ids.index(message["startaudioid"])
            titles = [self._title(r) for r in refs]
            self.commands.append(Command("queue", items[start]["stream"]["url"], ",".join(titles)))
            self._stop_queue()
            self.paused = False
            self._queue_task = asyncio.get_running_loop().create_task(
                self._run_queue(refs, start)
            )
            return {"code": 0}
        raise AssertionError(f"unexpected ubus method {method}")

    def _ref(self, url: str):
        from urllib.parse import parse_qs, unquote, urlparse

        parsed = urlparse(url)
        parts = [unquote(p) for p in parsed.path.split("/")]
        did, queue, index, source, item = parts[3:8]
        sig = parse_qs(parsed.query)["sig"][0]
        ref = self.gateway.verify(did, queue, int(index), source, item, sig)
        assert ref is not None, f"speaker got an address the gateway rejects: {url}"
        return ref

    @staticmethod
    def _title(ref) -> str:
        if ref.source == "silence":
            return "<silence>"
        m = re.search(r"/Audio/([^/]+)/stream", ref.item)
        return m.group(1) if m else ref.item

    def _stop_queue(self) -> None:
        if self._queue_task is not None:
            self._queue_task.cancel()
            self._queue_task = None

    async def _run_queue(self, refs, start: int) -> None:
        i = start
        while True:
            ref = refs[i]
            title = self._title(ref)
            self.playing_url = f"queue:{ref.queue}:{ref.index}"
            self.heard_log.append(title)
            if i not in self.drop_fetch:
                self.gateway.tracker.observe(ref, None)
            seconds = 60.0 if ref.source == "silence" else float(self.duration_of(title))
            await asyncio.sleep(seconds / TIME_SCALE)
            if self.loop_type == 0:
                continue  # the OH2P fetches the track again on every repeat
            i += 1
            if i >= len(refs):
                if self.loop_type == 2:
                    self.playing_url = ""
                    return
                i = 0

    def queues(self) -> list[list[str]]:
        """Titles of every dispatched queue, in order."""
        return [c.title.split(",") for c in self.commands if c.name == "queue"]

    def heard(self) -> list[str]:
        """What the speaker actually started playing, including local advances."""
        return list(self.heard_log)

    # -- helpers for assertions ----------------------------------------
    def played(self) -> list[str]:
        """Titles of the tracks actually handed to the speaker, in order."""
        return [c.title for c in self.commands if c.name == "play"]

    def timeline(self) -> list[str]:
        """Compact command log, e.g. ['stop', 'play:a', 'stop!timeout']."""
        out = []
        for c in self.commands:
            if c.name == "play":
                out.append(f"play:{c.title}")
            elif c.name == "queue":
                out.append(f"queue:{c.title}")
            else:
                out.append(c.name)
        return out


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
class EventLog:
    """Records what the player publishes (the WebUI's SSE trigger)."""

    events: list[tuple[str, dict]] = field(default_factory=list)
    # What a WebUI fetching state at publish time would see; set by build_rig.
    probe: Any = None
    seen: list[Any] = field(default_factory=list)

    def publish(self, name: str, **kwargs) -> None:
        self.events.append((name, kwargs))
        if self.probe is not None and name == "player_state_changed":
            self.seen.append(self.probe())

    def count(self, name: str) -> int:
        return sum(1 for n, _ in self.events if n == name)


@dataclass
class Rig:
    player: Any
    speaker: FakeSpeaker
    library: MusicLibrary
    events: EventLog = field(default_factory=EventLog)

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
        self.speaker._stop_queue()
        await self.player.close_command_arbiter()


def build_rig(
    monkeypatch,
    tmp_path,
    *,
    playlists: dict[str, list[str]] | None = None,
    durations: dict[str, float] | None = None,
    play_type: int = PLAY_TYPE_ALL,
    config_overrides: dict[str, Any] | None = None,
    queue: bool = False,
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
    if queue:
        config.public_base_url = "http://xm.test"
    else:
        config.queue_window_size = 0
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
        event_bus=EventLog(),
        last_download_result=None,
    )
    if queue:
        from xiaomusic.stream_gateway import FetchTracker, StreamGateway

        gateway = StreamGateway(config, log, music_library_getter=lambda: library)
        # The 3 s request-merge window must run on scaled time too.
        gateway.tracker = FetchTracker(log, clock=lambda: time.monotonic() * TIME_SCALE)
        xm.stream_gateway = gateway
        speaker.gateway = gateway
        speaker.duration_of = lambda title: float(durations.get(title, 30.0))
    player = XiaoMusicDevice(xm, device, group_name="g")
    # Group fan-out helpers look players up through the device manager.
    xm.device_manager.get_group_devices = lambda group: {DID: player}
    speaker.current_title = player.get_cur_music

    def _webui_view():
        # The state API takes the title from the runtime track.
        track = player.get_runtime_state().confirmed_track
        return (track.display_name if track else "", player.get_offset_duration()[1])

    xm.event_bus.probe = _webui_view
    if queue:
        gateway.add_fetch_listener(
            lambda ref: player.on_track_fetched(ref) if ref.did == DID else None
        )
    return Rig(player=player, speaker=speaker, library=library, events=xm.event_bus)
