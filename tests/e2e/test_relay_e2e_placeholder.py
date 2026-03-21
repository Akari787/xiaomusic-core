import pytest


@pytest.mark.e2e
@pytest.mark.skip(reason="Network audio e2e is optional and non-blocking")
def test_relay_e2e_placeholder():
    assert True
