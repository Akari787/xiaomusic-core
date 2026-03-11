from __future__ import annotations

from xiaomusic.core.models.media import DeliveryPlan, PreparedStream
from xiaomusic.playback.facade import PlaybackFacade


def test_playback_facade_serialize_dataclass() -> None:
    plan = DeliveryPlan(
        primary=PreparedStream(final_url="http://example.com/stream", source="site_media"),
        strategy="direct_only",
        decision_reason="pre_streamed_source",
    )

    out = PlaybackFacade._serialize(plan)

    assert out["primary"]["final_url"] == "http://example.com/stream"
    assert out["strategy"] == "direct_only"
    assert out["decision_reason"] == "pre_streamed_source"


def test_playback_facade_records_playback_capability_when_auth_manager_supports_it() -> None:
    captured = {}

    class _Auth:
        @staticmethod
        def record_playback_capability_verify(**kwargs):
            captured.update(kwargs)

    class _XM:
        auth_manager = _Auth()

    facade = PlaybackFacade(_XM())
    facade._record_playback_capability_verify(
        result="failed",
        verify_method="playback_dispatch",
        playback_capability_level="actual_playback_path",
        transport="mina",
        error_code="E_XIAOMI_PLAY_FAILED",
        error_message="transport dispatch failed",
    )

    assert captured["result"] == "failed"
    assert captured["transport"] == "mina"
