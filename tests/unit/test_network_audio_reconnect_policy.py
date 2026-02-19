import pytest


@pytest.mark.unit
def test_reconnect_policy_exponential_with_cap_and_terminate():
    from xiaomusic.network_audio.reconnect_policy import ReconnectPolicy  # noqa: PLC0415

    policy = ReconnectPolicy(base_delay_seconds=1, max_delay_seconds=8, max_retries=5)

    assert policy.delay_for_attempt(1) == 1
    assert policy.delay_for_attempt(2) == 2
    assert policy.delay_for_attempt(3) == 4
    assert policy.delay_for_attempt(4) == 8
    assert policy.delay_for_attempt(5) == 8
    assert policy.delay_for_attempt(6) is None


@pytest.mark.unit
def test_reconnect_policy_rejects_invalid_attempt():
    from xiaomusic.network_audio.reconnect_policy import ReconnectPolicy  # noqa: PLC0415

    policy = ReconnectPolicy(base_delay_seconds=1, max_delay_seconds=8, max_retries=2)
    with pytest.raises(ValueError):
        policy.delay_for_attempt(0)
