"""SpeakerClient is the single seam to the Xiaomi cloud (ADR-0007 step 2)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from xiaomusic.speaker_client import SpeakerClient

ROOT = Path(__file__).resolve().parents[1]


class _Auth:
    def __init__(self):
        self.calls = []
        self.miio_service = object()

    async def mina_call(self, method, *args, retry=1, ctx="", **kwargs):
        self.calls.append((method, args, {"retry": retry, "ctx": ctx, **kwargs}))
        return {"code": 0}

    async def miio_call(self, fn, *, retry=1, ctx=""):
        self.calls.append(("miio", (), {"retry": retry, "ctx": ctx}))
        return "ok"


def test_only_auth_and_speaker_client_talk_to_the_cloud():
    pattern = re.compile(r"\.(mina_call|miio_call)\(")
    offenders = []
    for path in (ROOT / "xiaomusic").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel in ("xiaomusic/auth.py", "xiaomusic/speaker_client.py"):
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{rel}:{lineno}")
    assert offenders == []


@pytest.mark.asyncio
async def test_commands_keep_previous_mina_calls():
    auth = _Auth()
    sc = SpeakerClient(auth)
    await sc.play_music_url("dev", "http://u", audio_id="42")
    await sc.play_url("dev", "http://u")
    await sc.stop("dev", ctx="force_stop_xiaoai_fast")
    await sc.pause("dev", ctx="force_stop_xiaoai")
    await sc.set_volume("dev", 30)
    await sc.status_raw("dev", ctx="get_volume")
    await sc.tts("dev", "hi")
    await sc.ubus("dev", "player_play_music", "mediaplayer", {"a": 1}, ctx="x")
    await sc.device_list()
    assert auth.calls == [
        ("play_by_music_url", ("dev", "http://u"), {"retry": 1, "ctx": "play_one_url_music_api", "audio_id": "42"}),
        ("play_by_url", ("dev", "http://u"), {"retry": 1, "ctx": "play_one_url"}),
        ("player_stop", ("dev",), {"retry": 1, "ctx": "force_stop_xiaoai_fast"}),
        ("player_pause", ("dev",), {"retry": 1, "ctx": "force_stop_xiaoai"}),
        ("player_set_volume", ("dev", 30), {"retry": 1, "ctx": "set_volume"}),
        ("player_get_status", ("dev",), {"retry": 1, "ctx": "get_volume"}),
        ("text_to_speech", ("dev", "hi"), {"retry": 1, "ctx": "text_to_speech"}),
        ("ubus_request", ("dev", "player_play_music", "mediaplayer", {"a": 1}), {"retry": 1, "ctx": "x"}),
        ("device_list", (), {"retry": 1, "ctx": "getalldevices"}),
    ]


@pytest.mark.asyncio
async def test_miio_command_goes_through_miio_call():
    auth = _Auth()
    assert await SpeakerClient(auth).miio_command("did", "5-1 hi", ctx="text_to_speech_miio") == "ok"
    assert auth.calls == [("miio", (), {"retry": 1, "ctx": "text_to_speech_miio"})]


def test_parse_status():
    raw = {"data": {"info": '{"status": 1, "volume": 21, "loop_type": 1}'}}
    assert SpeakerClient.parse_status(raw) == {"status": 1, "volume": 21, "loop_type": 1}
    assert SpeakerClient.parse_status({}) == {}
