import json

import pytest


class _FakeRunner:
    def __init__(self, result):
        self._result = result

    def run(self, url, timeout_seconds, command_override=None):  # noqa: ARG002
        return self._result


@pytest.mark.component
def test_ct1_resolver_mocked_success():
    from xiaomusic.network_audio.resolver import Resolver  # noqa: PLC0415
    from xiaomusic.network_audio.ytdlp_runner import RunnerResult  # noqa: PLC0415

    stdout = json.dumps(
        {
            "id": "iPnaF8Ngk3Q",
            "title": "sample",
            "is_live": False,
            "url": "https://cdn.example.local/audio.m4a",
            "ext": "m4a",
        }
    )
    runner = _FakeRunner(
        RunnerResult(exit_code=0, stdout=stdout, stderr="", timed_out=False, error_code=None)
    )
    result = Resolver(runner=runner).resolve(
        "https://www.youtube.com/watch?v=iPnaF8Ngk3Q",
        timeout_seconds=3,
    )
    assert result.ok is True
    assert result.error_code is None
    assert result.source_url.startswith("https://")


@pytest.mark.component
def test_ct1_resolver_mocked_timeout_error():
    from xiaomusic.network_audio.resolver import Resolver  # noqa: PLC0415
    from xiaomusic.network_audio.ytdlp_runner import RunnerResult  # noqa: PLC0415

    runner = _FakeRunner(
        RunnerResult(
            exit_code=None,
            stdout="",
            stderr="timeout",
            timed_out=True,
            error_code="E_RESOLVE_TIMEOUT",
        )
    )
    result = Resolver(runner=runner).resolve(
        "https://www.youtube.com/watch?v=iPnaF8Ngk3Q",
        timeout_seconds=1,
    )
    assert result.ok is False
    assert result.error_code == "E_RESOLVE_TIMEOUT"


@pytest.mark.component
def test_ct1_resolver_mocked_nonzero_exit_error():
    from xiaomusic.network_audio.resolver import Resolver  # noqa: PLC0415
    from xiaomusic.network_audio.ytdlp_runner import RunnerResult  # noqa: PLC0415

    runner = _FakeRunner(
        RunnerResult(
            exit_code=1,
            stdout="",
            stderr="network error",
            timed_out=False,
            error_code="E_RESOLVE_NONZERO_EXIT",
        )
    )
    result = Resolver(runner=runner).resolve(
        "https://www.youtube.com/watch?v=iPnaF8Ngk3Q",
        timeout_seconds=2,
    )
    assert result.ok is False
    assert result.error_code == "E_RESOLVE_NONZERO_EXIT"
