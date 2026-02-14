import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from xiaomusic.security.exec_plugin import ExecPluginEngine
from xiaomusic.security.errors import ExecDisabledError, ExecNotAllowedError


class DummyConfig:
    enable_exec_plugin = False
    allowed_exec_commands = []
    allowlist_domains = []


class DummyPluginManager:
    def __init__(self):
        self.called = False

    def get_func(self, name):
        def fn(*args, **kwargs):
            self.called = True
            return "ok"

        return fn


def make_app(engine: ExecPluginEngine):
    app = FastAPI()

    @app.post("/exec")
    async def _exec(payload: dict):
        code = payload.get("code", "")
        try:
            return {"ok": True, "result": await engine.execute(code)}
        except ExecDisabledError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except ExecNotAllowedError as e:
            raise HTTPException(status_code=403, detail=str(e))

    return app


def test_exec_disabled_blocks():
    cfg = DummyConfig()
    cfg.enable_exec_plugin = False
    cfg.allowed_exec_commands = ["code1"]
    plugin = DummyPluginManager()
    engine = ExecPluginEngine(cfg, logging.getLogger("t"), plugin_manager=plugin)
    app = make_app(engine)
    client = TestClient(app)

    r = client.post("/exec", json={"code": 'code1("hello")'})
    assert r.status_code == 403
    assert plugin.called is False


def test_exec_command_allowlist():
    cfg = DummyConfig()
    cfg.enable_exec_plugin = True
    cfg.allowed_exec_commands = []
    plugin = DummyPluginManager()
    engine = ExecPluginEngine(cfg, logging.getLogger("t"), plugin_manager=plugin)
    app = make_app(engine)
    client = TestClient(app)

    r = client.post("/exec", json={"code": 'code1("hello")'})
    assert r.status_code == 403
    assert plugin.called is False

    cfg.allowed_exec_commands = ["code1"]
    engine = ExecPluginEngine(cfg, logging.getLogger("t"), plugin_manager=plugin)
    app = make_app(engine)
    client = TestClient(app)
    r = client.post("/exec", json={"code": 'code1("hello")'})
    assert r.status_code == 200
    assert plugin.called is True
