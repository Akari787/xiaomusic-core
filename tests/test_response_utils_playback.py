from xiaomusic.api.response_utils import playback_response
from xiaomusic.network_audio.contracts import ERROR_CODES


def test_playback_response_error_uses_error_code_mapping():
    out = playback_response(
        ok=False,
        error_code="E_STREAM_NOT_FOUND",
    )
    assert out["code"] != 0
    assert out["message"] == ERROR_CODES["E_STREAM_NOT_FOUND"]
    assert out["data"]["ok"] is False
    assert out["data"]["success"] is False
    assert out["data"]["error_code"] == "E_STREAM_NOT_FOUND"


def test_playback_response_error_prefers_explicit_message():
    out = playback_response(
        ok=False,
        error_code="E_STREAM_NOT_FOUND",
        message="custom not found",
    )
    assert out["data"]["error_code"] == "E_STREAM_NOT_FOUND"
    assert out["message"] == "custom not found"
