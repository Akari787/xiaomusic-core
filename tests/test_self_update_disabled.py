import pytest

from xiaomusic.security.errors import SelfUpdateDisabledError
from xiaomusic.utils.system_utils import update_version


class DummyConfig:
    enable_self_update = False
    outbound_allowlist_domains = ["example.com"]


@pytest.mark.asyncio
async def test_self_update_disabled_blocks_even_outside_docker():
    cfg = DummyConfig()
    with pytest.raises(SelfUpdateDisabledError):
        await update_version(cfg, "main", lite=True)
