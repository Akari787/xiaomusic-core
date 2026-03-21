from __future__ import annotations

import importlib


def test_import_runtime_and_v1_router_without_cycle():
    runtime_mod = importlib.import_module("xiaomusic.relay.runtime")
    v1_mod = importlib.import_module("xiaomusic.api.routers.v1")
    assert hasattr(runtime_mod, "RelayRuntime")
    assert hasattr(v1_mod, "router")
