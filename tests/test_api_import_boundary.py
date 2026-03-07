from __future__ import annotations

import importlib
import sys


def _clear_api_modules() -> None:
    for name in list(sys.modules.keys()):
        if name == "xiaomusic.api" or name.startswith("xiaomusic.api."):
            sys.modules.pop(name, None)


def test_import_api_models_no_app_init_side_effect():
    _clear_api_modules()
    models = importlib.import_module("xiaomusic.api.models")
    assert hasattr(models, "PlayRequest")
    assert "xiaomusic.api.app" not in sys.modules
    assert "xiaomusic.api.dependencies" not in sys.modules


def test_import_api_package_no_bcrypt_dependency_load():
    _clear_api_modules()
    bcrypt_loaded_before = "bcrypt" in sys.modules
    importlib.import_module("xiaomusic.api")
    if not bcrypt_loaded_before:
        assert "bcrypt" not in sys.modules
    assert "xiaomusic.api.app" not in sys.modules
