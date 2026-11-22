"""StreamGateway (ADR-0007 step 1): signed stable addresses, request folding,
and the speaker-facing /stream route."""

from __future__ import annotations

import logging
import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xiaomusic.stream_gateway import (
    SENTINEL_SECONDS,
    SOURCE_JELLYFIN,
    SOURCE_LOCAL,
    SOURCE_SILENCE,
    SOURCE_WEB,
    FetchTracker,
    StreamGateway,
    StreamRef,
    StreamSigner,
    range_start,
    silent_mp3,
)

BASE = "http://192.168.7.178:58090"
LOG = logging.getLogger("test-stream-gateway")


class _Library:
    def __init__(self, tokens=None, jellyfin_host="192.168.7.4:30013"):
        self.tokens = tokens or {}
        self.jellyfin_host = jellyfin_host

    def resolve_proxy_url_token(self, token):
        return self.tokens.get(token, "")

    def is_jellyfin_url(self, url):
        return urlparse(url).netloc == self.jellyfin_host


class _Jellyfin:
    def __init__(self, token="tok-now"):
        self.token = token

    def enabled(self):
        return True

    def _stream_url(self, item_id):
        return f"http://192.168.7.4:30013/Audio/{item_id}/stream.mp3?api_key={self.token}"


def _gateway(tmp_path, *, base=BASE, library=None, jellyfin=None):
    config = SimpleNamespace(
        conf_path=str(tmp_path / "conf"),
        get_public_base_url=lambda: base,
    )
    lib = library or _Library()
    jf = jellyfin or _Jellyfin()
    return StreamGateway(
        config, LOG, music_library_getter=lambda: lib, jellyfin_client_getter=lambda: jf
    )


def _parts(url):
    parsed = urlparse(url)
    segs = parsed.path.strip("/").split("/")
    assert segs[:2] == ["stream", "v1"]
    did, queue, index, source, item = segs[2:]
    sig = parsed.query.split("sig=", 1)[1]
    return did, queue, int(index), source, item, sig


# ── signer ──────────────────────────────────────────────────────────────


def test_key_is_created_private_and_reused_across_restarts(tmp_path):
    conf = tmp_path / "conf"
    s1 = StreamSigner(str(conf))
    sig = s1.sign("981257654", "7", 3, "local", "abc")
    mode = stat.S_IMODE(os.stat(s1.key_path).st_mode)
    assert mode == 0o600
    # A new process (new signer) with the same conf/ accepts old addresses.
    s2 = StreamSigner(str(conf))
    assert s2.verify("981257654", "7", 3, "local", "abc", sig)


def test_signature_covers_every_path_field(tmp_path):
    s = StreamSigner(str(tmp_path))
    sig = s.sign("did", "q", 1, "local", "item")
    assert s.verify("did", "q", 1, "local", "item", sig)
    for args in (
        ("other", "q", 1, "local", "item"),
        ("did", "q2", 1, "local", "item"),
        ("did", "q", 2, "local", "item"),
        ("did", "q", 1, "web", "item"),
        ("did", "q", 1, "local", "item2"),
    ):
        assert not s.verify(*args, sig)
    assert not s.verify("did", "q", 1, "local", "item", "")


# ── classification and wrapping ─────────────────────────────────────────


def test_local_music_url_becomes_local_item(tmp_path):
    gw = _gateway(tmp_path)
    assert gw.classify(f"{BASE}/music/_exp/a%2020.m4a") == (SOURCE_LOCAL, "_exp/a 20.m4a")


def test_jellyfin_url_keeps_only_the_item_id(tmp_path):
    gw = _gateway(tmp_path)
    url = "http://192.168.7.4:30013/Audio/96e8355a6c006afaeaa56249b53e21f1/stream.mp3?api_key=SECRET"
    assert gw.classify(url) == (SOURCE_JELLYFIN, "96e8355a6c006afaeaa56249b53e21f1")
    wrapped = gw.wrap(url, did="981257654", queue="5", index=2)
    assert "SECRET" not in wrapped
    assert "api_key" not in wrapped


def test_proxy_token_is_resolved_to_its_origin(tmp_path):
    lib = _Library(tokens={"abc": "http://cdn.example.com/x.mp3"})
    gw = _gateway(tmp_path, library=lib)
    assert gw.classify(f"{BASE}/proxy/music?urlb64=t.abc") == (SOURCE_WEB, "http://cdn.example.com/x.mp3")
    # Proxy URL around a Jellyfin stream still becomes a Jellyfin item.
    lib.tokens["jf"] = "http://192.168.7.4:30013/Audio/abc123/stream.mp3?api_key=K"
    assert gw.classify(f"{BASE}/proxy/music?urlb64=t.jf") == (SOURCE_JELLYFIN, "abc123")


@pytest.mark.parametrize(
    "url",
    [
        f"{BASE}/relay/stream/sid123",
        f"{BASE}/proxy/radio?urlb64=t.abc",
        f"{BASE}/proxy/music?urlb64=t.expired",
        "http://radio.example.com/live.m3u8",
        "self://music/x.mp3",
        "",
    ],
)
def test_unhandled_urls_pass_through(tmp_path, url):
    gw = _gateway(tmp_path)
    assert gw.wrap(url, did="d", queue="1", index=0) == url


def test_no_public_base_means_no_wrapping(tmp_path):
    gw = _gateway(tmp_path, base="")
    url = "http://cdn.example.com/x.mp3"
    assert gw.wrap(url, did="d", queue="1", index=0) == url


def test_wrap_round_trips_through_verify(tmp_path):
    gw = _gateway(tmp_path)
    url = gw.wrap("http://cdn.example.com/a b.mp3?x=1&y=2", did="981257654", queue="12", index=-1)
    assert url.startswith(f"{BASE}/stream/v1/981257654/12/0/web/")
    ref = gw.verify(*_parts(url))
    assert ref == StreamRef("981257654", "12", 0, SOURCE_WEB, "http://cdn.example.com/a b.mp3?x=1&y=2")


def test_jellyfin_resolves_with_the_current_token(tmp_path):
    jf = _Jellyfin(token="old")
    gw = _gateway(tmp_path, jellyfin=jf)
    url = gw.wrap("http://192.168.7.4:30013/Audio/abc/stream.mp3?api_key=old", did="d", queue="1", index=0)
    jf.token = "new"  # re-login after the address was issued
    target = gw.resolve(gw.verify(*_parts(url)))
    assert target.url.endswith("/Audio/abc/stream.mp3?api_key=new")


# ── request folding ─────────────────────────────────────────────────────


def test_range_start_parsing():
    assert range_start(None) is None
    assert range_start("bytes=0-") == 0
    assert range_start("bytes=480360-") == 480360
    assert range_start("bytes=-128") == -1
    assert range_start("garbage") is None


def test_probe_burst_counts_as_one_fetch():
    now = [100.0]
    tracker = FetchTracker(LOG, clock=lambda: now[0])
    ref = StreamRef("d", "1", 0, SOURCE_LOCAL, "a.mp3")
    # Observed OH2P burst: open+abort, tail ID3 read, full download.
    assert tracker.observe(ref, "bytes=0-") is True
    now[0] += 0.02
    assert tracker.observe(ref, "bytes=480360-") is False
    now[0] += 0.05
    assert tracker.observe(ref, "bytes=0-") is False
    assert tracker.last_fetch["d"]["index"] == 0


def test_next_track_and_replay_are_new_fetches():
    now = [100.0]
    tracker = FetchTracker(LOG, clock=lambda: now[0])
    a = StreamRef("d", "1", 0, SOURCE_LOCAL, "a.mp3")
    b = StreamRef("d", "1", 1, SOURCE_LOCAL, "b.mp3")
    assert tracker.observe(a, None) is True
    now[0] += 20
    assert tracker.observe(b, None) is True  # chunked source: single request
    now[0] += 20
    assert tracker.observe(a, "bytes=0-") is True  # list loop back to a
    assert tracker.last_fetch["d"]["index"] == 0


def test_mid_file_resume_is_not_a_fetch():
    tracker = FetchTracker(LOG, clock=lambda: 0.0)
    ref = StreamRef("d", "1", 0, SOURCE_WEB, "http://x/y.mp3")
    assert tracker.observe(ref, "bytes=1000-") is False
    assert "d" not in tracker.last_fetch


# ── HTTP route ──────────────────────────────────────────────────────────


class _Upstream(BaseHTTPRequestHandler):
    BODY = bytes(range(256)) * 40  # 10240 bytes
    seen_ranges: list = []

    def do_GET(self):  # noqa: N802
        rng = self.headers.get("Range")
        _Upstream.seen_ranges.append(rng)
        body = self.BODY
        if rng and rng.startswith("bytes="):
            start = int(rng[6:].split("-")[0] or 0)
            chunk = body[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
        else:
            chunk = body
            self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(chunk)

    def log_message(self, *args):
        pass


@pytest.fixture
def upstream():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _Upstream.seen_ranges = []
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def client(tmp_path, monkeypatch):
    from xiaomusic.api.routers import file as file_routes
    from xiaomusic.api.routers import stream as stream_routes

    music = tmp_path / "music"
    (music / "_exp").mkdir(parents=True)
    (music / "_exp" / "a20.mp3").write_bytes(b"ID3" + b"\x00" * 997)
    config = SimpleNamespace(
        music_path=str(music),
        conf_path=str(tmp_path / "conf"),
        remove_id3tag=False,
        convert_to_mp3=False,
        get_public_base_url=lambda: BASE,
    )
    gw = StreamGateway(config, LOG, music_library_getter=lambda: _Library())
    monkeypatch.setattr(file_routes, "config", config)
    monkeypatch.setattr(stream_routes, "xiaomusic", SimpleNamespace(stream_gateway=gw))
    app = FastAPI()
    app.include_router(stream_routes.router)
    return TestClient(app), gw


def _path(url):
    parsed = urlparse(url)
    return f"{parsed.path}?{parsed.query}"


def test_route_serves_local_file_with_range(client):
    tc, gw = client
    url = gw.wrap(f"{BASE}/music/_exp/a20.mp3", did="d", queue="1", index=0)
    full = tc.get(_path(url))
    assert full.status_code == 200
    assert len(full.content) == 1000
    part = tc.get(_path(url), headers={"Range": "bytes=872-"})
    assert part.status_code == 206
    assert len(part.content) == 128
    assert gw.tracker.last_fetch["d"]["source"] == SOURCE_LOCAL


def test_route_rejects_tampered_or_unsigned_addresses(client):
    tc, gw = client
    url = gw.wrap(f"{BASE}/music/_exp/a20.mp3", did="d", queue="1", index=0)
    path = _path(url)
    assert tc.get(path.replace("/1/0/", "/1/1/")).status_code == 404
    assert tc.get(path.split("?")[0]).status_code == 404
    assert tc.options(path).status_code == 200


def test_route_proxies_web_source_and_passes_range(client, upstream):
    tc, gw = client
    url = gw.wrap(f"{upstream}/song.mp3", did="d", queue="1", index=3)
    full = tc.get(_path(url))
    assert full.status_code == 200
    assert full.content == _Upstream.BODY
    assert full.headers["content-type"].startswith("audio/mpeg")
    tail = tc.get(_path(url), headers={"Range": "bytes=10112-"})
    assert tail.status_code == 206
    assert tail.headers["content-range"] == "bytes 10112-10239/10240"
    assert tail.content == _Upstream.BODY[10112:]
    assert _Upstream.seen_ranges == [None, "bytes=10112-"]


# ── dispatch hook ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_group_player_play_dispatches_gateway_address(tmp_path):
    from xiaomusic.device_player import XiaoMusicDevice

    gw = _gateway(tmp_path)
    sent = []
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = LOG
    d.device = SimpleNamespace(did="981257654")
    d.group_name = "g"
    d._play_session_id = 7
    d._current_index = 4
    d.xiaomusic = SimpleNamespace(
        stream_gateway=gw,
        device_manager=SimpleNamespace(get_group_device_id_list=lambda g: ["dev-1"]),
    )

    async def _play_one_url(device_id, url, name, **_kw):
        sent.append(url)
        return {"code": 0}

    d.play_one_url = _play_one_url
    await d.group_player_play(f"{BASE}/music/_exp/a20.mp3", "a20")
    assert sent[0].startswith(f"{BASE}/stream/v1/981257654/7/4/local/")
    ref = gw.verify(*_parts(sent[0]))
    assert ref.item == "_exp/a20.mp3"

    sent.clear()
    await d.group_player_play(f"{BASE}/relay/stream/sid1", "live")
    assert sent == [f"{BASE}/relay/stream/sid1"]


# ── sentinel and fetch listeners (step 3) ──────────────────────────────


def test_sentinel_address_carries_the_next_index(tmp_path):
    gw = _gateway(tmp_path)
    url = gw.sentinel_url("981257654", "12", 20)
    ref = gw.verify(*_parts(url))
    assert (ref.queue, ref.index, ref.source) == ("12", 20, SOURCE_SILENCE)
    target = gw.resolve(ref)
    assert target.seconds == SENTINEL_SECONDS


def test_silent_mp3_is_whole_mpeg_frames_of_the_requested_length():
    data = silent_mp3(60)
    assert data[:4] == bytes([0xFF, 0xFB, 0x10, 0xC4])
    assert len(data) % 104 == 0
    frames = len(data) // 104
    assert 60.0 <= frames * 1152 / 44100 < 60.1


def test_fetch_listener_gets_new_fetches_only_and_cannot_break_serving():
    seen = []
    tracker = FetchTracker(LOG, clock=lambda: 100.0)

    def boom(ref):
        raise RuntimeError("listener bug")

    tracker.add_listener(boom)
    tracker.add_listener(seen.append)
    ref = StreamRef("d", "3", 1, SOURCE_WEB, "http://x/a.mp3")
    assert tracker.observe(ref, None) is True
    assert tracker.observe(ref, "bytes=-128") is False
    assert tracker.observe(ref, None) is False  # same burst
    assert seen == [ref]


def test_route_serves_the_sentinel_with_range(client):
    tc, gw = client
    path = _path(gw.sentinel_url("d", "1", 5))
    full = tc.get(path)
    assert full.status_code == 200
    assert full.headers["content-type"] == "audio/mpeg"
    assert full.content == silent_mp3(SENTINEL_SECONDS)
    tail = tc.get(path, headers={"Range": "bytes=-128"})
    assert tail.status_code == 206
    assert len(tail.content) == 128
    total = len(full.content)
    assert tail.headers["content-range"] == f"bytes {total - 128}-{total - 1}/{total}"
    past = tc.get(path, headers={"Range": f"bytes={total}-"})
    assert past.status_code == 416
    assert gw.tracker.last_fetch["d"]["source"] == SOURCE_SILENCE
