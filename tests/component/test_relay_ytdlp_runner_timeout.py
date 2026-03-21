import sys

import pytest


@pytest.mark.component
def test_ct1_1_ytdlp_runner_kills_process_on_timeout():
    from xiaomusic.relay.ytdlp_runner import YtdlpRunner  # noqa: PLC0415

    runner = YtdlpRunner()
    result = runner.run(
        url="https://www.youtube.com/watch?v=iPnaF8Ngk3Q",
        timeout_seconds=0.5,
        command_override=[sys.executable, "-c", "import time; time.sleep(5)"],
    )

    assert result.timed_out is True
    assert result.error_code == "E_RESOLVE_TIMEOUT"
    assert result.exit_code is None
