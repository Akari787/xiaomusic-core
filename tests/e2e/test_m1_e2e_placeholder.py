import pytest


@pytest.mark.e2e
@pytest.mark.skip(reason="M1 e2e is optional and non-blocking")
def test_m1_e2e_placeholder():
    assert True
