import importlib.util
import pathlib
import sys
import types

import pytest

qrcode_login_mod = types.ModuleType("xiaomusic.qrcode_login")
qrcode_login_mod.MiJiaAPI = type("MiJiaAPI", (), {})
sys.modules.setdefault("xiaomusic.qrcode_login", qrcode_login_mod)

qrcode_main_mod = types.ModuleType("qrcode.main")
qrcode_main_mod.QRCode = type("QRCode", (), {})
qrcode_pkg = types.ModuleType("qrcode")
qrcode_pkg.main = qrcode_main_mod
sys.modules.setdefault("qrcode", qrcode_pkg)
sys.modules.setdefault("qrcode.main", qrcode_main_mod)

module_path = (
    pathlib.Path(__file__).resolve().parents[1]
    / "xiaomusic"
    / "api"
    / "routers"
    / "system.py"
)
spec = importlib.util.spec_from_file_location("system_module_for_test", module_path)
system_mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(system_mod)


@pytest.mark.asyncio
async def test_root_redirects_to_webui():
    response = await system_mod.read_index()

    assert response.status_code == 302
    assert response.headers["location"] == "/webui/"
