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
