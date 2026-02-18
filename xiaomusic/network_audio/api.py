"""Minimal network-audio API endpoints for health and session observation."""

from __future__ import annotations

import time
from dataclasses import asdict

from fastapi import FastAPI

from xiaomusic.network_audio.session_manager import StreamSessionManager


def build_network_audio_app(play_service, session_manager: StreamSessionManager) -> FastAPI:
    app = FastAPI(title="Network Audio Minimal API")
    started_at = time.monotonic()

    @app.get("/healthz")
    def healthz() -> dict:
        return {
            "status": "ok",
            "uptime_seconds": int(time.monotonic() - started_at),
        }

    @app.get("/sessions")
    def sessions() -> dict:
        rows = [asdict(s) for s in session_manager.list_sessions()]
        return {"sessions": rows}

    @app.post("/play_url")
    def play_url(payload: dict) -> dict:
        url = payload.get("url", "")
        return play_service.play_url(url)

    return app
