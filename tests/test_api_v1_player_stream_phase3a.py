from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xiaomusic.api.routers import v1


class _Bus:
    def __init__(self) -> None:
        self.listeners = []
        self.unsubscribed = []

    def subscribe(self, event_type, callback):
        self.listeners.append((event_type, callback))

    def unsubscribe(self, event_type, callback):
        self.unsubscribed.append((event_type, callback))
        self.listeners.remove((event_type, callback))

    def publish(self, device_id):
        for _, callback in tuple(self.listeners):
            callback(device_id=device_id)


@pytest_asyncio.fixture(autouse=True)
async def clean_channels():
    v1._player_stream_channels.clear()
    yield
    for channel in tuple(v1._player_stream_channels.values()):
        await channel.close()
    v1._player_stream_channels.clear()


def _snapshot(
    revision: int,
    epoch: str | None = None,
    device_id: str = "did-1",
    snapshot_at_ms: int | None = None,
) -> dict:
    snapshot = {
        "device_id": device_id,
        "revision": revision,
        "server_instance_id": epoch or v1.SERVER_INSTANCE_ID,
    }
    if snapshot_at_ms is not None:
        snapshot["snapshot_at_ms"] = snapshot_at_ms
    return snapshot


@pytest.mark.asyncio
async def test_one_listener_and_one_build_fan_out_to_three_subscribers(monkeypatch):
    bus = _Bus()
    builds = 0

    class Facade:
        async def build_player_state_snapshot(self, device_id):
            nonlocal builds
            builds += 1
            return _snapshot(6 + builds)

    monkeypatch.setattr(v1, "_get_facade", lambda: Facade())
    channel = await v1._get_player_stream_channel("did-1", bus)
    subscribers = [v1._PlayerStreamSubscriber() for _ in range(3)]
    for subscriber in subscribers:
        channel.add(subscriber)
    await asyncio.wait_for(channel.initial_ready.wait(), 1)
    assert len(bus.listeners) == 1
    assert builds == 1
    assert all(subscriber.queue.empty() for subscriber in subscribers)

    bus.publish("did-1")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert builds == 2
    for subscriber in subscribers:
        assert await subscriber.queue.get() == _snapshot(8)


def test_sse_event_id_and_snapshot_epoch_are_wire_contracts():
    encoded = v1._encode_player_state_event(_snapshot(4))
    assert f"id: {v1.SERVER_INSTANCE_ID}:4\n".encode() in encoded
    assert b"id: other-epoch:4\n" in v1._encode_player_state_event(
        _snapshot(4, epoch="other-epoch")
    )
    assert f'"server_instance_id": "{v1.SERVER_INSTANCE_ID}"'.encode() in encoded
    assert v1.SSE_HEARTBEAT == b": heartbeat\n\n"
    assert b"heartbeat" not in encoded


@pytest.mark.asyncio
async def test_latest_wins_queue_and_epoch_revision_selection():
    queue = asyncio.Queue(maxsize=1)
    v1._queue_latest(queue, _snapshot(2))
    v1._queue_latest(queue, _snapshot(3))
    assert queue.maxsize == 1
    assert await queue.get() == _snapshot(3)
    assert not v1._snapshot_is_newer(_snapshot(3), _snapshot(3))
    assert not v1._snapshot_is_newer(_snapshot(2), _snapshot(3))
    assert v1._snapshot_is_newer(_snapshot(1, "new-epoch"), _snapshot(99))


@pytest.mark.asyncio
async def test_initial_build_chooses_snapshot_built_after_dirty_event(monkeypatch):
    bus = _Bus()
    initial_started = asyncio.Event()
    release_initial = asyncio.Event()
    builds = 0

    class Facade:
        async def build_player_state_snapshot(self, device_id):
            nonlocal builds
            builds += 1
            if builds == 1:
                initial_started.set()
                await release_initial.wait()
                return _snapshot(1)
            return _snapshot(2)

    monkeypatch.setattr(v1, "_get_facade", lambda: Facade())
    channel = await v1._get_player_stream_channel("did-1", bus)
    subscriber = v1._PlayerStreamSubscriber()
    channel.add(subscriber)
    await initial_started.wait()
    bus.publish("did-1")
    release_initial.set()
    initial = await asyncio.wait_for(channel.initial_for(subscriber), 1)
    assert initial["revision"] == 2
    assert builds == 2


@pytest.mark.asyncio
async def test_initial_failure_is_reported_once_and_does_not_hot_loop(monkeypatch):
    bus = _Bus()
    builds = 0

    class Facade:
        async def build_player_state_snapshot(self, device_id):
            nonlocal builds
            builds += 1
            raise RuntimeError("initial unavailable")

    monkeypatch.setattr(v1, "_get_facade", lambda: Facade())
    channel = await v1._get_player_stream_channel("did-1", bus)
    subscriber = v1._PlayerStreamSubscriber()
    channel.add(subscriber)
    with pytest.raises(RuntimeError, match="initial unavailable"):
        await asyncio.wait_for(channel.initial_for(subscriber), 1)
    await asyncio.sleep(0.1)
    assert builds == 1
    await v1._release_player_stream_subscriber("did-1", channel, subscriber)
    assert not bus.listeners
    assert channel.build_task is None


@pytest.mark.asyncio
async def test_failed_followup_build_recovers_on_next_event(monkeypatch):
    bus = _Bus()
    builds = 0

    class Facade:
        async def build_player_state_snapshot(self, device_id):
            nonlocal builds
            builds += 1
            if builds == 2:
                raise RuntimeError("transient")
            return _snapshot(builds)

    monkeypatch.setattr(v1, "_get_facade", lambda: Facade())
    channel = await v1._get_player_stream_channel("did-1", bus)
    subscriber = v1._PlayerStreamSubscriber()
    channel.add(subscriber)
    await asyncio.wait_for(channel.initial_ready.wait(), 1)
    bus.publish("did-1")
    await asyncio.sleep(0.05)
    assert builds == 2
    bus.publish("did-1")
    await asyncio.sleep(0.05)
    assert builds == 3
    assert (await subscriber.queue.get())["revision"] == 3


@pytest.mark.asyncio
async def test_tail_window_dirty_event_starts_next_build(monkeypatch):
    bus = _Bus()
    builds = 0
    hook_fired = False

    class Facade:
        async def build_player_state_snapshot(self, device_id):
            nonlocal builds
            builds += 1
            return _snapshot(builds)

    monkeypatch.setattr(v1, "_get_facade", lambda: Facade())
    channel = await v1._get_player_stream_channel("did-1", bus)
    subscriber = v1._PlayerStreamSubscriber()
    channel.add(subscriber)
    await asyncio.wait_for(channel.initial_ready.wait(), 1)

    async def tail_hook():
        nonlocal hook_fired
        if builds == 2 and not hook_fired:
            hook_fired = True
            channel.mark_dirty()

    channel._before_task_exit = tail_hook
    bus.publish("did-1")
    await asyncio.sleep(0.1)
    assert builds == 3
    assert (await subscriber.queue.get())["revision"] == 3


@pytest.mark.asyncio
async def test_existing_channel_new_subscriber_gets_shared_fresh_refresh(monkeypatch):
    bus = _Bus()
    builds = 0

    class Facade:
        async def build_player_state_snapshot(self, device_id):
            nonlocal builds
            builds += 1
            return _snapshot(5, snapshot_at_ms=100 if builds == 1 else 200)

    monkeypatch.setattr(v1, "_get_facade", lambda: Facade())
    channel = await v1._get_player_stream_channel("did-1", bus)
    old = v1._PlayerStreamSubscriber()
    channel.add(old)
    assert (await channel.initial_for(old))["snapshot_at_ms"] == 100

    new = v1._PlayerStreamSubscriber()
    channel.add(new)
    fresh = await asyncio.wait_for(channel.initial_for(new), 1)
    assert fresh["snapshot_at_ms"] == 200
    assert old.queue.empty()
    assert builds == 2


@pytest.mark.asyncio
async def test_refresh_event_overlap_fanouts_new_revision_to_old_and_new(monkeypatch):
    bus = _Bus()
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()
    builds = 0

    class Facade:
        async def build_player_state_snapshot(self, device_id):
            nonlocal builds
            builds += 1
            if builds == 2:
                refresh_started.set()
                await release_refresh.wait()
                return _snapshot(2)
            return _snapshot(1 if builds == 1 else 2)

    monkeypatch.setattr(v1, "_get_facade", lambda: Facade())
    channel = await v1._get_player_stream_channel("did-1", bus)
    old = v1._PlayerStreamSubscriber()
    channel.add(old)
    assert (await channel.initial_for(old))["revision"] == 1

    new = v1._PlayerStreamSubscriber()
    channel.add(new)
    await refresh_started.wait()
    bus.publish("did-1")
    release_refresh.set()

    assert (await asyncio.wait_for(channel.initial_for(new), 1))["revision"] == 2
    assert (await asyncio.wait_for(old.queue.get(), 1))["revision"] == 2
    assert builds == 3


@pytest.mark.asyncio
async def test_different_devices_have_independent_single_builds(monkeypatch):
    bus = _Bus()
    builds = {"did-1": 0, "did-2": 0}

    class Facade:
        async def build_player_state_snapshot(self, device_id):
            builds[device_id] += 1
            return _snapshot(builds[device_id], device_id=device_id)

    monkeypatch.setattr(v1, "_get_facade", lambda: Facade())
    channels = {}
    for did in builds:
        channels[did] = await v1._get_player_stream_channel(did, bus)
        subscriber = v1._PlayerStreamSubscriber()
        channels[did].add(subscriber)
        await channels[did].initial_ready.wait()
    bus.publish("did-1")
    bus.publish("did-2")
    await asyncio.sleep(0.05)
    assert builds == {"did-1": 2, "did-2": 2}


@pytest.mark.asyncio
async def test_one_disconnect_keeps_channel_until_last_subscriber(monkeypatch):
    bus = _Bus()

    class Facade:
        async def build_player_state_snapshot(self, device_id):
            return _snapshot(1)

    monkeypatch.setattr(v1, "_get_facade", lambda: Facade())
    channel = await v1._get_player_stream_channel("did-1", bus)
    subscribers = [v1._PlayerStreamSubscriber() for _ in range(3)]
    for subscriber in subscribers:
        channel.add(subscriber)
    await channel.initial_ready.wait()
    await v1._release_player_stream_subscriber("did-1", channel, subscribers[0])
    assert len(bus.listeners) == 1
    assert v1._player_stream_channels["did-1"] is channel
    await v1._release_player_stream_subscriber("did-1", channel, subscribers[1])
    assert len(bus.listeners) == 1
    await v1._release_player_stream_subscriber("did-1", channel, subscribers[2])
    assert not bus.listeners
    assert not v1._player_stream_channels
    assert channel.build_task is None


@pytest.mark.asyncio
async def test_burst_has_one_build_at_a_time_and_delivers_latest(monkeypatch):
    bus = _Bus()
    builds = 0
    active = 0
    peak = 0
    second_started = asyncio.Event()
    release_second = asyncio.Event()

    class Facade:
        async def build_player_state_snapshot(self, device_id):
            nonlocal builds, active, peak
            builds += 1
            active += 1
            peak = max(peak, active)
            try:
                if builds == 2:
                    second_started.set()
                    await release_second.wait()
                return _snapshot(builds)
            finally:
                active -= 1

    monkeypatch.setattr(v1, "_get_facade", lambda: Facade())
    channel = await v1._get_player_stream_channel("did-1", bus)
    subscriber = v1._PlayerStreamSubscriber()
    channel.add(subscriber)
    await channel.initial_ready.wait()
    bus.publish("did-1")
    await second_started.wait()
    bus.publish("did-1")
    bus.publish("did-1")
    release_second.set()
    await asyncio.sleep(0.1)
    assert peak == 1
    assert builds == 3
    assert (await subscriber.queue.get())["revision"] == 3


@pytest.mark.asyncio
async def test_atomic_register_replaces_closed_channel_during_slow_release(monkeypatch):
    bus = _Bus()

    class Facade:
        async def build_player_state_snapshot(self, device_id):
            return _snapshot(1, device_id=device_id)

    monkeypatch.setattr(v1, "_get_facade", lambda: Facade())
    old_channel, old_subscriber = await v1._register_player_stream_subscriber("did-1", bus)
    await asyncio.wait_for(old_channel.initial_ready.wait(), 1)
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    original_close = old_channel.close

    async def slow_close():
        close_started.set()
        await release_close.wait()
        await original_close()

    old_channel.close = slow_close
    release_task = asyncio.create_task(
        v1._release_player_stream_subscriber("did-1", old_channel, old_subscriber)
    )
    await close_started.wait()
    new_channel, new_subscriber = await v1._register_player_stream_subscriber(
        "did-1", bus
    )
    assert new_channel is not old_channel
    assert new_channel.closed is False
    assert v1._player_stream_channels["did-1"] is new_channel
    assert (await asyncio.wait_for(new_channel.initial_for(new_subscriber), 1))["revision"] == 1
    release_close.set()
    await release_task
    assert old_channel.closed
    assert old_channel.build_task is None
    assert old_channel.listener is None
    await v1._release_player_stream_subscriber("did-1", new_channel, new_subscriber)


@pytest.mark.asyncio
async def test_slow_close_for_one_device_does_not_block_other_register(monkeypatch):
    bus = _Bus()

    class Facade:
        async def build_player_state_snapshot(self, device_id):
            return _snapshot(1, device_id=device_id)

    monkeypatch.setattr(v1, "_get_facade", lambda: Facade())
    old_channel, old_subscriber = await v1._register_player_stream_subscriber("did-1", bus)
    await old_channel.initial_ready.wait()
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    original_close = old_channel.close

    async def slow_close():
        close_started.set()
        await release_close.wait()
        await original_close()

    old_channel.close = slow_close
    release_task = asyncio.create_task(
        v1._release_player_stream_subscriber("did-1", old_channel, old_subscriber)
    )
    await close_started.wait()
    other_channel, other_subscriber = await asyncio.wait_for(
        v1._register_player_stream_subscriber("did-2", bus), 1
    )
    assert (await asyncio.wait_for(other_channel.initial_for(other_subscriber), 1))["device_id"] == "did-2"
    release_close.set()
    await release_task
    await v1._release_player_stream_subscriber("did-2", other_channel, other_subscriber)


@pytest.mark.asyncio
async def test_stream_endpoint_does_not_register_before_first_iteration(monkeypatch):
    bus = _Bus()

    class XM:
        event_bus = bus

        @staticmethod
        def did_exist(device_id):
            return True

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: XM())
    response = await v1.api_v1_player_stream(None, device_id="did-1")
    assert v1._player_stream_channels == {}
    assert bus.listeners == []
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_stream_initial_cancel_cleans_registration(monkeypatch):
    bus = _Bus()
    build_started = asyncio.Event()
    release_build = asyncio.Event()

    class XM:
        event_bus = bus

        @staticmethod
        def did_exist(device_id):
            return True

    class Facade:
        async def build_player_state_snapshot(self, device_id):
            build_started.set()
            await release_build.wait()
            return _snapshot(1)

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: XM())
    monkeypatch.setattr(v1, "_get_facade", lambda: Facade())
    response = await v1.api_v1_player_stream(None, device_id="did-1")
    generator = response.body_iterator
    first = asyncio.create_task(generator.__anext__())
    await build_started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release_build.set()
    assert v1._player_stream_channels == {}
    assert bus.listeners == []


@pytest.mark.asyncio
async def test_stream_normal_first_frame_and_cleanup_remain_intact(monkeypatch):
    bus = _Bus()

    class XM:
        event_bus = bus

        @staticmethod
        def did_exist(device_id):
            return True

    class Facade:
        async def build_player_state_snapshot(self, device_id):
            return _snapshot(1)

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: XM())
    monkeypatch.setattr(v1, "_get_facade", lambda: Facade())
    response = await v1.api_v1_player_stream(None, device_id="did-1")
    first = await response.body_iterator.__anext__()
    assert f"id: {v1.SERVER_INSTANCE_ID}:1".encode() in first
    await response.body_iterator.aclose()
    assert not v1._player_stream_channels
    assert not bus.listeners


def test_stream_404_shape_is_unchanged(monkeypatch):
    class XM:
        event_bus = None

        @staticmethod
        def did_exist(device_id):
            return False

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: XM())
    app = FastAPI()
    app.include_router(v1.router)
    response = TestClient(app).get("/api/v1/player/stream", params={"device_id": "missing"})
    assert response.status_code == 404
    body = json.loads(response.content)
    assert set(body) == {"code", "message", "data", "request_id"}
    assert body["code"] == 40004
    assert body["data"] == {
        "error_code": "E_DEVICE_NOT_FOUND",
        "stage": "request",
    }


@pytest.mark.asyncio
async def test_stream_generator_cleanup_on_initial_error(monkeypatch):
    bus = _Bus()

    class XM:
        event_bus = bus

        @staticmethod
        def did_exist(device_id):
            return True

    class Facade:
        async def build_player_state_snapshot(self, device_id):
            raise RuntimeError("boom")

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: XM())
    monkeypatch.setattr(v1, "_get_facade", lambda: Facade())
    response = await v1.api_v1_player_stream(None, device_id="did-1")
    generator = response.body_iterator
    with pytest.raises(RuntimeError, match="boom"):
        await generator.__anext__()
    assert not bus.listeners
    assert not v1._player_stream_channels
