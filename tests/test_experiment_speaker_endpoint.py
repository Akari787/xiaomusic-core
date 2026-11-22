"""ADR-0007 experiment tooling: off by default, payload shape verified on OH2P."""

from __future__ import annotations

import json
import types

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xiaomusic.api.dependencies import verification
from xiaomusic.api.routers import system
from xiaomusic.device_player import XiaoMusicDevice


def test_experiment_endpoint_is_404_when_disabled(monkeypatch):
    monkeypatch.setattr(system, "config", types.SimpleNamespace(enable_playback_experiments=False))
    app = FastAPI()
    app.include_router(system.router)
    app.dependency_overrides[verification] = lambda: True
    resp = TestClient(app).post("/api/experiment/speaker", json={"op": "status"})
    assert resp.status_code == 404


def test_music_queue_payload_numbers_items_and_selects_start():
    payload = XiaoMusicDevice._build_music_queue_payload(
        [("a", "http://h/a.mp3"), ("b", "http://h/b.mp3"), ("c", "http://h/c.mp3")],
        "1000",
        start_index=1,
    )
    music = json.loads(payload["music"])
    items = music["payload"]["audio_items"]
    assert [i["item_id"]["audio_id"] for i in items] == ["1000", "1001", "1002"]
    assert [i["stream"]["url"] for i in items] == ["http://h/a.mp3", "http://h/b.mp3", "http://h/c.mp3"]
    assert payload["startaudioid"] == "1001"
    assert music["play_behavior"] == "REPLACE_ALL"


def test_music_queue_payload_clamps_start_index():
    payload = XiaoMusicDevice._build_music_queue_payload([("a", "u")], "5", start_index=9)
    assert payload["startaudioid"] == "5"


def test_music_queue_payload_carries_play_behavior():
    payload = XiaoMusicDevice._build_music_queue_payload([("a", "u")], "5", play_behavior="APPEND")
    assert json.loads(payload["music"])["play_behavior"] == "APPEND"
