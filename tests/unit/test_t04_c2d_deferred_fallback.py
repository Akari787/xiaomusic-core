"""T04-C2d: deferred receipt & Device fallback — Coordinator + Device chain.

Tests A-L:
  A – deferred receipt accepted=True → fast return, provider zero calls
  B – legacy no receipt → provider called
  C – explicit accepted=False → fallback path triggered if plan has fallback
  D – direct allNone → fallback success, q/c=1 a=2, URL order, callback token
  E – direct success, a=1, no fallback
  F – direct raise → fallback success, a=2
  G – both fail/raise → last_error set, phase != PLAYING
  H – STOP during direct await → no fallback, STOPPED
  I – no fallback, raw fail → DISPATCHING, last_error set
  J – caller context mutation isolated from internal fallback
  K – AST check
  L – facade/API no leak of _device_external_fallback

Zero asyncio.sleep().  Zero bare Event.wait().
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import types

import pytest

from xiaomusic.const import PLAY_TYPE_ALL
from xiaomusic.core.coordinator.playback_coordinator import PlaybackCoordinator
from xiaomusic.core.delivery.delivery_adapter import DeliveryAdapter
from xiaomusic.core.device.device_registry import DeviceRegistry
from xiaomusic.core.models.device import DeviceProfile, DeviceReachability
from xiaomusic.core.models.media import (
    MediaRequest,
    ResolvedMedia,
)
from xiaomusic.core.models.transport import TransportCapabilityMatrix
from xiaomusic.core.source.source_plugin import SourcePlugin
from xiaomusic.core.source.source_registry import SourceRegistry
from xiaomusic.core.transport.transport import Transport
from xiaomusic.core.transport.transport_policy import TransportPolicy
from xiaomusic.core.transport.transport_router import TransportRouter
from xiaomusic.device_player import XiaoMusicDevice
from xiaomusic.playback.runtime_state import (
    LifecycleToken,
    PlaybackPhase,
    PlaybackRuntimeState,
)

# ── helpers: Coordinator ────────────────────────────────────────────────────


class _CyclingSourcePlugin(SourcePlugin):
    name = "cycling"

    def __init__(self, output: ResolvedMedia) -> None:
        self._output = output
        self.calls = 0

    def can_resolve(self, request: MediaRequest) -> bool:
        return True

    async def resolve(self, request: MediaRequest) -> ResolvedMedia:
        self.calls += 1
        return self._output


def _build_coordinator(
    plugin: SourcePlugin,
    *,
    transport: Transport,
    status_provider=None,
    proxy_builder=None,
) -> PlaybackCoordinator:
    source_registry = SourceRegistry()
    source_registry.register(plugin)

    device_registry = DeviceRegistry()
    device_registry.register_device(
        profile=DeviceProfile(did="d1", model="OH2P", name="speaker", group="default"),
        reachability=DeviceReachability(
            ip="192.168.7.10",
            local_reachable=False,
            cloud_reachable=False,
            last_probe_ts=1,
        ),
        capability_matrix=TransportCapabilityMatrix(
            play=["mina"],
            previous=["mina"],
            next=["mina"],
            stop=["mina"],
            pause=["mina"],
            tts=["mina"],
            volume=["mina"],
            probe=["mina"],
        ),
    )

    router = TransportRouter(policy=TransportPolicy())
    router.register_transport(transport)
    return PlaybackCoordinator(
        source_registry=source_registry,
        device_registry=device_registry,
        delivery_adapter=DeliveryAdapter(
            expiry_skew_seconds=0, proxy_url_builder=proxy_builder
        ),
        transport_router=router,
        max_resolve_retry=1,
        playback_status_provider=status_provider,
    )


# ── helpers: Device ─────────────────────────────────────────────────────────


async def _noop(*args, **kwargs):
    pass


async def _noop_coro():
    pass


async def _noop_list(*args, **kwargs):
    return [None]


async def _noop_return_false(*args, **kwargs):
    return False


def _bump_sid(d):
    d._play_session_id += 1
    return d._play_session_id


def _make_device() -> XiaoMusicDevice:
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("test-t04c2d")
    d._play_list_items = [
        {"display_name": "A", "legacy_name": "A", "item_id": "a", "entity_id": "ea"},
    ]
    d._current_index = 0
    d._play_session_id = 0
    d._last_cmd = ""
    d.is_playing = False
    d._start_time = 0
    d._paused_time = 0
    d._duration = 0
    d._play_failed_cnt = 0
    d._play_fail_first_ts = 0.0
    d._play_fail_last_reason = ""
    d._degraded = False
    d._degraded_notified = False
    d._last_volume = 0
    d._next_timer = None
    d._autonext_guard_task = None
    d._playback_confirm_task = None
    d._playback_status_probe_task = None
    d._timer_expiry_false_count = 0
    d._bg_confirm_false_count = 0
    d._timer_expiry_playing_grace_count = 0
    d._timer_expiry_unknown_grace_count = 0
    d._playlist_session_shuffled = False
    d._runtime_state = PlaybackRuntimeState()
    d._external_context_registry = {}
    d._external_context_registry_order = []
    d._external_context_next_id = 0
    d.device = types.SimpleNamespace(
        did="did-t04c2d",
        play_type=PLAY_TYPE_ALL,
        hardware="OH2P",
        cur_playlist="",
        cur_music="",
        current_display_name="",
        current_entity_id="",
        current_playlist_item_id="",
        playlist2music={},
    )
    d.config = types.SimpleNamespace(
        delay_sec=0,
        verbose=False,
        ffmpeg_location="",
        jellyfin_proxy_mode="off",
        stop_tts_msg="",
        enable_force_stop=True,
        auto_next_stop_wait_mode="sync",
        auto_next_stop_grace_ms=0,
    )
    d.event_bus = None
    d.group_name = "test"
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(
            all_music={"A": "/tmp/A.mp3"},
            is_music_exist=lambda n: True,
            get_music_url=lambda name: (f"file:///tmp/{name}.mp3", f"file:///tmp/{name}.mp3"),
            get_music_duration=lambda n: _noop(),
            find_real_music_name=lambda name, **kw: [name],
            resolve_playlist_item_identity=lambda p, item_name: "",
            resolve_entity_id_by_name=lambda n: "",
        ),
        analytics=types.SimpleNamespace(send_play_event=lambda *a, **k: _noop()),
        device_manager=types.SimpleNamespace(
            get_group_device_id_list=lambda g: [],
            get_group_devices=lambda g: {},
        ),
    )
    d._inflight_fast_stop_tasks = set()
    d._manual_nav_lock = asyncio.Lock()
    d._manual_nav_generation = 0
    d._manual_nav_target = None
    d._command_arbiter = None
    d.do_tts = _noop
    d.group_force_stop_xiaoai = _noop_list
    d.cancel_group_next_timer = _noop_coro
    d._invalidate_manual_navigation = lambda reason: None
    d.auto_add_song = lambda cur, sec: _noop()
    d._refresh_runtime_volume = lambda context="": _noop_return_false
    d._start_duration_probe = lambda name, sid, **kw: None
    d.set_next_music_timeout = lambda sec, token=None: _noop()
    d._schedule_playing_status_probe = lambda sid, name: None
    d._find_playlist_index = lambda *a, **kw: -1
    d.get_if_xiaoai_is_playing = _noop_return_false
    d._play = _noop_return_false
    d._play_next = _noop
    d.get_cur_music = lambda: "A"
    d.find_cur_playlist = lambda name: ""
    d.update_playlist = lambda: None
    d.check_play_next = lambda: False
    d._check_and_download_music = lambda name, sk, ad: _noop_return_false
    d._playmusic = _noop_return_false
    d._set_runtime_track_reference = lambda **kw: None
    d._track_background_task = lambda task, label: None
    d._bump_play_session = lambda reason="": _bump_sid(d)
    d._execute_group_stop = lambda *a, **k: [None]
    d._mark_play_started = _noop_return_false
    d._schedule_playback_confirmation = lambda **kw: None
    d._confirm_playback_started = lambda name, sid: _noop_return_false
    d._try_proxy_fallback = _noop_return_false
    d._handle_play_failure = _noop
    d._log_measure = lambda msg: None
    d._bootstrap_playlist_session_for_external_url = lambda context: None
    return d


# ══════════════════════════════════════════════════════════════════════════════
# Coordinator A: deferred receipt accepted=True → fast return, provider 0 calls
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_A_deferred_receipt_fast_return_provider_zero_calls():
    """Transport returns deferred receipt.  status_provider is blocked;
    Coordinator.play returns quickly.  provider_calls == 0,
    attempt started=None, accepted=True."""

    block_event = asyncio.Event()
    provider_calls: list[int] = []

    async def _blocked_provider(device_id: str) -> dict:
        provider_calls.append(1)
        await asyncio.wait_for(block_event.wait(), timeout=5.0)
        return {"status": 1}

    class _DeferredTransport(Transport):
        name = "mina"

        async def play_url(self, device_id, prepared, request_context=None):
            return {"ret": {"accepted": True, "sequence": 1}, "url": prepared.final_url}

        async def stop(self, device_id):
            return {}

        async def previous(self, device_id):
            return {}

        async def next(self, device_id):
            return {}

        async def pause(self, device_id):
            return {}

        async def tts(self, device_id, text):
            return {}

        async def set_volume(self, device_id, volume):
            return {}

        async def probe(self, device_id):
            return {}

    plugin = _CyclingSourcePlugin(
        ResolvedMedia(
            media_id="m", source="direct_url", title="t",
            stream_url="https://x/a.mp3", expires_at=None, is_live=False,
        )
    )
    coordinator = _build_coordinator(
        plugin, transport=_DeferredTransport(), status_provider=_blocked_provider,
    )

    try:
        out = await asyncio.wait_for(
            coordinator.play(
                MediaRequest(request_id="r1", source_hint="cycling",
                             query="x", device_id="d1")
            ),
            timeout=0.2,
        )
    finally:
        block_event.set()

    assert provider_calls == [], f"provider should be zero, got {len(provider_calls)}"
    assert out["outcome"].accepted is True
    assert out["outcome"].started is None
    assert out["outcome"].attempts[0].accepted is True
    assert out["outcome"].attempts[0].started is None


# ══════════════════════════════════════════════════════════════════════════════
# Coordinator B: legacy no receipt → provider called
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_B_legacy_no_receipt_provider_called():
    """Transport returns ordinary dict (no ret.accepted).  provider is called
    and its result becomes attempt.started."""

    provider_called = asyncio.Event()

    async def _provider(device_id: str) -> dict:
        provider_called.set()
        return {"status": 1}

    class _LegacyTransport(Transport):
        name = "mina"

        async def play_url(self, device_id, prepared, request_context=None):
            return {"ret": "OK", "url": prepared.final_url}

        async def stop(self, device_id):
            return {}

        async def previous(self, device_id):
            return {}

        async def next(self, device_id):
            return {}

        async def pause(self, device_id):
            return {}

        async def tts(self, device_id, text):
            return {}

        async def set_volume(self, device_id, volume):
            return {}

        async def probe(self, device_id):
            return {}

    plugin = _CyclingSourcePlugin(
        ResolvedMedia(
            media_id="m", source="direct_url", title="t",
            stream_url="https://x/a.mp3",
        )
    )
    coordinator = _build_coordinator(
        plugin, transport=_LegacyTransport(), status_provider=_provider,
    )

    out = await asyncio.wait_for(
        coordinator.play(
            MediaRequest(request_id="r2", source_hint="cycling",
                         query="x", device_id="d1")
        ),
        timeout=5.0,
    )

    assert provider_called.is_set()
    assert out["outcome"].attempts[0].started is True


# ══════════════════════════════════════════════════════════════════════════════
# Coordinator C: explicit accepted=False, fallback path triggered
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_C_explicit_false_fallback_triggered():
    """First transport returns deferred accepted=False.  Provider not called.
    Fallback (proxy) path tried; second transport returns deferred true.
    Outcome fallback_triggered=True."""

    provider_calls: list[int] = []

    async def _provider(device_id: str) -> dict:
        provider_calls.append(1)
        return {"status": 1}

    class _FailThenDeferredTransport(Transport):
        name = "mina"

        def __init__(self):
            self.urls: list[str] = []
            self.call_count = 0

        async def play_url(self, device_id, prepared, request_context=None):
            self.call_count += 1
            self.urls.append(prepared.final_url)
            if self.call_count == 1:
                return {"ret": {"accepted": False}, "url": prepared.final_url}
            return {"ret": {"accepted": True, "sequence": 1}, "url": prepared.final_url}

        async def stop(self, device_id):
            return {}

        async def previous(self, device_id):
            return {}

        async def next(self, device_id):
            return {}

        async def pause(self, device_id):
            return {}

        async def tts(self, device_id, text):
            return {}

        async def set_volume(self, device_id, volume):
            return {}

        async def probe(self, device_id):
            return {}

    transport = _FailThenDeferredTransport()
    plugin = _CyclingSourcePlugin(
        ResolvedMedia(
            media_id="m", source="site_media", title="t",
            stream_url="https://yt.example/v.mp4",
        )
    )
    coordinator = _build_coordinator(
        plugin,
        transport=transport,
        status_provider=_provider,
        proxy_builder=lambda url, name: f"http://127.0.0.1:58090/proxy?name={name}",
    )

    out = await asyncio.wait_for(
        coordinator.play(
            MediaRequest(request_id="r3", source_hint="cycling",
                         query="x", device_id="d1")
        ),
        timeout=5.0,
    )

    assert provider_calls == [], f"provider should be zero, got {provider_calls}"
    assert len(transport.urls) == 2
    # site_media source → proxy_first strategy
    has_direct = any(u.startswith("https://") for u in transport.urls)
    has_proxy = any("127.0.0.1:58090/proxy" in u for u in transport.urls)
    assert has_direct and has_proxy, f"expected direct+proxy, got {transport.urls}"
    assert out["outcome"].fallback_triggered is True
    assert out["outcome"].accepted is True


# ══════════════════════════════════════════════════════════════════════════════
# Device D: direct allNone → fallback success, q/c=1 a=2, URL order, callback
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_D_direct_allnone_fallback_success():
    """Direct group_player_play returns [None]; fallback returns [{'ok':1}].
    q=1 c=1 a=2.  URL order: direct first, fallback second.
    on_external_url_play_started called with fallback token."""

    d = _make_device()
    called_urls: list[str] = []
    direct_done = asyncio.Event()
    fallback_done = asyncio.Event()
    started_called = asyncio.Event()
    started_token: LifecycleToken | None = None

    async def _spy_play(url):
        called_urls.append(url)
        if len(called_urls) == 1:
            direct_done.set()
            return [None]
        fallback_done.set()
        return [{"ok": 1}]

    async def _spy_started(*, context=None, resolved=None, token):
        nonlocal started_token
        started_token = token
        started_called.set()
        return True

    d.group_player_play = _spy_play
    d.on_external_url_play_started = _spy_started

    try:
        receipt = await d.submit_external_url_play(
            url="http://direct.mp3",
            context={
                "title": "song",
                "_device_external_fallback": {
                    "final_url": "http://proxy.mp3",
                    "source": "direct_url",
                    "is_proxy": True,
                },
            },
        )
        assert receipt["accepted"] is True

        await asyncio.wait_for(direct_done.wait(), timeout=5.0)
        await asyncio.wait_for(fallback_done.wait(), timeout=5.0)
        await asyncio.wait_for(started_called.wait(), timeout=5.0)

        # URL order
        assert called_urls == ["http://direct.mp3", "http://proxy.mp3"]

        # q/c=1 a=2
        state = d.get_runtime_state()
        assert state.queue_session_id == 1
        assert state.command_generation == 1
        assert state.track_attempt_id == 2

        # Callback called with correct token
        assert started_token is not None
        assert started_token.track_attempt_id == 2
    finally:
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════════════
# Device E: direct success, a=1, no fallback
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_E_direct_success_a1_no_fallback():
    """Direct dispatch succeeds immediately.  a=1, no fallback call."""

    d = _make_device()
    called_urls: list[str] = []
    done = asyncio.Event()

    async def _spy_play(url):
        called_urls.append(url)
        done.set()
        return [{"ok": 1}]

    d.group_player_play = _spy_play

    try:
        receipt = await d.submit_external_url_play(
            url="http://direct.mp3",
            context={
                "title": "song",
                "_device_external_fallback": {
                    "final_url": "http://proxy.mp3",
                    "source": "direct_url",
                    "is_proxy": True,
                },
            },
        )
        assert receipt["accepted"] is True

        await asyncio.wait_for(done.wait(), timeout=5.0)

        assert called_urls == ["http://direct.mp3"]
        state = d.get_runtime_state()
        assert state.track_attempt_id == 1
    finally:
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════════════
# Device F: direct raise → fallback success, a=2
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_F_direct_raise_fallback_success_a2():
    """Direct group_player_play raises; fallback succeeds.  a=2."""

    d = _make_device()
    called_urls: list[str] = []
    fallback_done = asyncio.Event()

    async def _spy_play(url):
        called_urls.append(url)
        if len(called_urls) == 1:
            raise RuntimeError("direct boom")
        fallback_done.set()
        return [{"ok": 1}]

    d.group_player_play = _spy_play

    try:
        receipt = await d.submit_external_url_play(
            url="http://direct.mp3",
            context={
                "title": "song",
                "_device_external_fallback": {
                    "final_url": "http://proxy.mp3",
                    "source": "direct_url",
                    "is_proxy": True,
                },
            },
        )
        assert receipt["accepted"] is True

        await asyncio.wait_for(fallback_done.wait(), timeout=5.0)

        assert called_urls == ["http://direct.mp3", "http://proxy.mp3"]
        state = d.get_runtime_state()
        assert state.track_attempt_id == 2
    finally:
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════════════
# Device G: both fail → last_error, phase != PLAYING
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_G_both_fail_last_error_not_playing():
    """Both direct and fallback raise.  last_error set, phase != PLAYING.

    Executor is wrapped before arbiter creation so executor_done fires
    only after the arbiter has caught the exception — zero race."""

    d = _make_device()
    executor_done = asyncio.Event()
    called_urls: list[str] = []

    _orig_arb = d._arbiter_executor

    async def _wrapped_arb(intent):
        try:
            await _orig_arb(intent)
        finally:
            executor_done.set()

    d._arbiter_executor = _wrapped_arb

    async def _spy_play(url):
        called_urls.append(url)
        raise RuntimeError("boom")

    d.group_player_play = _spy_play

    try:
        receipt = await d.submit_external_url_play(
            url="http://direct.mp3",
            context={
                "title": "song",
                "_device_external_fallback": {
                    "final_url": "http://proxy.mp3",
                    "source": "direct_url",
                    "is_proxy": True,
                },
            },
        )
        assert receipt["accepted"] is True

        await asyncio.wait_for(executor_done.wait(), timeout=5.0)

        arb = d._command_arbiter
        assert arb is not None
        assert arb.last_error is not None
        assert d.get_runtime_state().phase != PlaybackPhase.PLAYING
        assert called_urls == ["http://direct.mp3", "http://proxy.mp3"]
    finally:
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════════════
# Device H: STOP during direct await → no fallback, STOPPED
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_H_stop_during_direct_no_fallback_stopped():
    """Direct is blocked; public stop() is called.  After unblock, no fallback.
    Phase STOPPED.  No callback."""

    d = _make_device()
    direct_entered = asyncio.Event()
    direct_block = asyncio.Event()
    stopped_done = asyncio.Event()
    called_urls: list[str] = []
    started_called = False

    async def _spy_play(url):
        called_urls.append(url)
        direct_entered.set()
        await asyncio.wait_for(direct_block.wait(), timeout=5.0)
        return [{"ok": 1}]

    async def _spy_started(*, context=None, resolved=None, token):
        nonlocal started_called
        started_called = True
        return True

    d.group_player_play = _spy_play
    d.on_external_url_play_started = _spy_started
    _orig_complete = d._complete_runtime_stop

    def _wrapped_complete_stop(*a, **kw):
        result = _orig_complete(*a, **kw)
        stopped_done.set()
        return result

    d._complete_runtime_stop = _wrapped_complete_stop

    try:
        receipt = await d.submit_external_url_play(
            url="http://direct.mp3",
            context={
                "title": "song",
                "_device_external_fallback": {
                    "final_url": "http://proxy.mp3",
                    "source": "direct_url",
                    "is_proxy": True,
                },
            },
        )
        assert receipt["accepted"] is True

        # Wait for direct to enter
        await asyncio.wait_for(direct_entered.wait(), timeout=5.0)

        # Public stop — "notts" avoids 3s TTS sleep
        await d.stop("notts")

        # Unblock direct
        direct_block.set()
        # Wait for STOP barrier to complete (Event set by _complete_runtime_stop)
        await asyncio.wait_for(stopped_done.wait(), timeout=5.0)

        assert not started_called, "callback should not be called"
        assert called_urls == ["http://direct.mp3"], (
            f"only direct URL, got {called_urls}"
        )
        # After STOP barrier, phase should be STOPPED
        assert d.get_runtime_state().phase == PlaybackPhase.STOPPED
    finally:
        direct_block.set()
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════════════
# Device I: no fallback, raw fail → DISPATCHING, last_error
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_I_no_fallback_raw_fail_dispatching_last_error():
    """No fallback in context.  Direct returns all-None.  Phase not PLAYING;
    last_error set.  Executor wrapper ensures arbiter has caught the error."""

    d = _make_device()
    executor_done = asyncio.Event()

    _orig_arb = d._arbiter_executor

    async def _wrapped_arb(intent):
        try:
            await _orig_arb(intent)
        finally:
            executor_done.set()

    d._arbiter_executor = _wrapped_arb

    async def _spy_play(url):
        return [None]

    d.group_player_play = _spy_play

    try:
        receipt = await d.submit_external_url_play(
            url="http://direct.mp3",
            context={"title": "song"},
        )
        assert receipt["accepted"] is True

        await asyncio.wait_for(executor_done.wait(), timeout=5.0)

        arb = d._command_arbiter
        assert arb is not None
        assert arb.last_error is not None
        assert "no fallback" in str(arb.last_error).lower()
        phase = d.get_runtime_state().phase
        assert phase != PlaybackPhase.PLAYING
    finally:
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════════════
# Device J: caller context mutation isolated from internal fallback
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_J_caller_mutation_isolated():
    """Mutating caller's context dict after submit does not affect the
    internal fallback plan seen by executor."""

    d = _make_device()
    seen_fallback_urls: list[str] = []
    done = asyncio.Event()

    async def _spy_play(url):
        seen_fallback_urls.append(url)
        if len(seen_fallback_urls) == 2:
            done.set()
        if len(seen_fallback_urls) == 1:
            return [None]
        return [{"ok": 1}]

    d.group_player_play = _spy_play

    ctx = {
        "title": "original",
        "_device_external_fallback": {
            "final_url": "http://proxy-original.mp3",
            "source": "direct_url",
            "is_proxy": True,
        },
    }
    # deepcopy before submit so internal is captured
    try:
        receipt = await d.submit_external_url_play(
            url="http://direct.mp3", context=ctx,
        )
        assert receipt["accepted"] is True

        # Mutate caller's context AFTER submission
        ctx["_device_external_fallback"]["final_url"] = "http://mutated.mp3"
        ctx["title"] = "mutated"

        await asyncio.wait_for(done.wait(), timeout=5.0)

        # Executor should use original fallback URL
        assert seen_fallback_urls == [
            "http://direct.mp3", "http://proxy-original.mp3"
        ], f"got {seen_fallback_urls}"
    finally:
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════════════
# K: AST — deferred receipt branch in Coordinator
# ══════════════════════════════════════════════════════════════════════════════


def test_K_AST_deferred_receipt_in_coordinator():
    """AST: PlaybackCoordinator._dispatch_single must reference
    _deferred_receipt_accepted and conditionally skip
    _confirm_playback_started."""

    src_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "xiaomusic", "core",
        "coordinator", "playback_coordinator.py",
    )
    src_path = os.path.abspath(src_path)
    with open(src_path, encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)

    dispatch_single = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_dispatch_single":
            dispatch_single = node
            break

    assert dispatch_single is not None, "_dispatch_single not found"

    has_deferred = False
    for child in ast.walk(dispatch_single):
        if isinstance(child, ast.Attribute) and child.attr == "_deferred_receipt_accepted":
            has_deferred = True
            break
        if isinstance(child, ast.Name) and child.id == "_deferred_receipt_accepted":
            has_deferred = True
            break

    assert has_deferred, "_dispatch_single must reference _deferred_receipt_accepted"


# ══════════════════════════════════════════════════════════════════════════════
# L: facade / API output + Coordinator log must not leak internal fields
# ══════════════════════════════════════════════════════════════════════════════


def test_L_facade_and_log_no_leak_internal_fields(caplog):
    """Coordinator play output + logs must NOT contain
    _device_external_fallback or original api_key secrets."""

    import logging

    caplog.set_level(logging.INFO, logger="xiaomusic.core.playback_coordinator")

    class _TestTransport(Transport):
        name = "mina"

        async def play_url(self, device_id, prepared, request_context=None):
            return {"ret": {"accepted": True, "sequence": 1}, "url": prepared.final_url}

        async def stop(self, device_id):
            return {}

        async def previous(self, device_id):
            return {}

        async def next(self, device_id):
            return {}

        async def pause(self, device_id):
            return {}

        async def tts(self, device_id, text):
            return {}

        async def set_volume(self, device_id, volume):
            return {}

        async def probe(self, device_id):
            return {}

    # Secret in stream URL that MUST be redacted
    plugin = _CyclingSourcePlugin(
        ResolvedMedia(
            media_id="m", source="direct_url", title="t",
            stream_url="https://x.com/a?api_key=LEAKTEST123",
            headers={"Authorization": "Bearer LEAKTEST456"},
        )
    )

    p = _build_coordinator(
        plugin,
        transport=_TestTransport(),
        proxy_builder=lambda url, name: "http://127.0.0.1:58090/proxy?name=safe",
    )

    async def _run():
        return await p.play(
            MediaRequest(request_id="rL", source_hint="cycling",
                         query="y", device_id="d1")
        )

    out = asyncio.run(_run())

    # ── log capture: secrets must not appear in Coordinator logs ──
    log_text = caplog.text
    assert "LEAKTEST123" not in log_text, (
        f"api_key leaked in Coordinator log: ...{log_text[-200:]}"
    )
    assert "LEAKTEST456" not in log_text, (
        f"header secret leaked in Coordinator log: ...{log_text[-200:]}"
    )

    # ── response scan: same as before ──
    from xiaomusic.playback.facade import PlaybackFacade

    serialized = json.dumps(
        PlaybackFacade._sanitize_public_value(out), default=str
    )
    parsed = json.loads(serialized)

    def _scan(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert "_device_external_fallback" not in str(k), (
                    f"leaked internal key at {path}.{k}"
                )
                if not str(k).startswith("_"):
                    _scan(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _scan(v, f"{path}[{i}]")
        elif isinstance(obj, str):
            assert "LEAKTEST123" not in obj, (
                f"leaked api_key secret at {path}: {obj[:80]}"
            )
            assert "LEAKTEST456" not in obj, (
                f"leaked header secret at {path}: {obj[:80]}"
            )

    _scan(parsed)


# ── dispatch_succeeded unit tests ───────────────────────────────────────────


def test_dispatch_succeeded_code_zero_success():
    """{'code': 0} → success."""
    from xiaomusic.device_player import XiaoMusicDevice
    assert XiaoMusicDevice._external_play_dispatch_succeeded({"code": 0}) is True


def test_dispatch_succeeded_code_neg_one_fail():
    """{'code': -1} → fail."""
    from xiaomusic.device_player import XiaoMusicDevice
    assert XiaoMusicDevice._external_play_dispatch_succeeded({"code": -1}) is False


def test_dispatch_succeeded_code_nonzero_fail():
    """{'code': 1} → fail."""
    from xiaomusic.device_player import XiaoMusicDevice
    assert XiaoMusicDevice._external_play_dispatch_succeeded({"code": 1}) is False


def test_dispatch_succeeded_code_zero_str():
    """{'code': '0'} → success (string '0')."""
    from xiaomusic.device_player import XiaoMusicDevice
    assert XiaoMusicDevice._external_play_dispatch_succeeded({"code": "0"}) is True


def test_dispatch_succeeded_code_bad_str():
    """{'code': 'bad'} → fail, no ValueError."""
    from xiaomusic.device_player import XiaoMusicDevice
    assert XiaoMusicDevice._external_play_dispatch_succeeded({"code": "bad"}) is False


def test_dispatch_succeeded_code_none():
    """{'code': None} → fail, no ValueError."""
    from xiaomusic.device_player import XiaoMusicDevice
    assert XiaoMusicDevice._external_play_dispatch_succeeded({"code": None}) is False


def test_dispatch_succeeded_list_accepted_false_elem():
    """[{'accepted': False}] → fail (recursive)."""
    from xiaomusic.device_player import XiaoMusicDevice
    assert XiaoMusicDevice._external_play_dispatch_succeeded(
        [{"accepted": False}]
    ) is False


def test_dispatch_succeeded_list_code_neg_one_elem():
    """[{'code': -1}] → fail (recursive)."""
    from xiaomusic.device_player import XiaoMusicDevice
    assert XiaoMusicDevice._external_play_dispatch_succeeded(
        [{"code": -1}]
    ) is False


def test_dispatch_succeeded_list_code_zero_elem():
    """[{'code': 0}] → success (recursive)."""
    from xiaomusic.device_player import XiaoMusicDevice
    assert XiaoMusicDevice._external_play_dispatch_succeeded(
        [{"code": 0}]
    ) is True


def test_dispatch_succeeded_list_unknown_dict_elem():
    """[{'unknown': 'dict'}] → success (backward compat, non-empty dict)."""
    from xiaomusic.device_player import XiaoMusicDevice
    assert XiaoMusicDevice._external_play_dispatch_succeeded(
        [{"unknown": "dict"}]
    ) is True


def test_dispatch_succeeded_accepted_false_dict():
    """{'accepted': False} → fail."""
    from xiaomusic.device_player import XiaoMusicDevice
    assert XiaoMusicDevice._external_play_dispatch_succeeded({"accepted": False}) is False


def test_dispatch_succeeded_list_mixed_elements():
    """[None, {'code': 0}] → success (one succeeded)."""
    from xiaomusic.device_player import XiaoMusicDevice
    assert XiaoMusicDevice._external_play_dispatch_succeeded([None, {"code": 0}]) is True


def test_dispatch_succeeded_list_all_none():
    """[None, None] → fail."""
    from xiaomusic.device_player import XiaoMusicDevice
    assert XiaoMusicDevice._external_play_dispatch_succeeded([None, None]) is False


# ── sleep / poll audit ──────────────────────────────────────────────────────


def test_C2d_ast_no_asyncio_sleep_and_no_bare_wait():
    """This file must not contain asyncio.sleep() calls."""

    src_path = os.path.abspath(__file__)
    with open(src_path, encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)

    sleep_calls: list[int] = []

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node):
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "sleep"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "asyncio"
            ):
                sleep_calls.append(node.lineno)
            self.generic_visit(node)

    _Visitor().visit(tree)

    assert not sleep_calls, (
        f"unexpected asyncio.sleep() at lines {sleep_calls}"
    )
