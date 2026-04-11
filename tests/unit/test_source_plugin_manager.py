from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from xiaomusic.adapters.sources.direct_url_source_plugin import DirectUrlSourcePlugin
from xiaomusic.core.models.media import MediaRequest
from xiaomusic.managers.source_plugin_manager import SourcePluginManager
from xiaomusic.playback.facade import PlaybackFacade


PLUGIN_SOURCE = (
    "from xiaomusic.core.source import SourcePlugin\n"
    "from xiaomusic.core.models.media import ResolvedMedia\n"
    "\n"
    "class MockExternalPlugin(SourcePlugin):\n"
    "    name = 'mock_external'\n"
    "    version = '1.0.0'\n"
    "\n"
    "    def can_resolve(self, request):\n"
    "        return str(request.query).startswith('mock://')\n"
    "\n"
    "    async def resolve(self, request):\n"
    "        return ResolvedMedia(\n"
    "            media_id=request.request_id,\n"
    "            source=self.name,\n"
    "            title='mock',\n"
    "            stream_url='https://example.com/mock.mp3',\n"
    "            headers={},\n"
    "            expires_at=None,\n"
    "            is_live=False,\n"
    "        )\n"
    "\n"
    "def create_plugin():\n"
    "    return MockExternalPlugin()\n"
)

BUILTIN_OVERRIDE_SOURCE = (
    "from xiaomusic.core.source import SourcePlugin\n"
    "from xiaomusic.core.models.media import ResolvedMedia\n"
    "\n"
    "class OverrideBuiltinPlugin(SourcePlugin):\n"
    "    name = 'direct_url'\n"
    "\n"
    "    def can_resolve(self, request):\n"
    "        return True\n"
    "\n"
    "    async def resolve(self, request):\n"
    "        return ResolvedMedia(\n"
    "            media_id=request.request_id,\n"
    "            source=self.name,\n"
    "            title='override',\n"
    "            stream_url='https://example.com/override.mp3',\n"
    "            headers={},\n"
    "            expires_at=None,\n"
    "            is_live=False,\n"
    "        )\n"
    "\n"
    "def create_plugin():\n"
    "    return OverrideBuiltinPlugin()\n"
)


def _manager(plugins_dir: Path) -> SourcePluginManager:
    return SourcePluginManager(
        register_defaults=lambda registry: registry.register(DirectUrlSourcePlugin()),
        plugins_dir=plugins_dir,
    )


@pytest.mark.unit
def test_source_plugin_manager_builds_active_registry_with_builtins():
    with TemporaryDirectory() as tmp_dir:
        manager = _manager(Path(tmp_dir))

        registry = manager.get_active_registry()
        picked = registry.get_plugin(
            "direct_url",
            MediaRequest(
                request_id="r1",
                source_hint="direct_url",
                query="https://example.com/a.mp3",
            ),
        )

        assert manager.registry_version >= 1
        assert picked.name == "direct_url"
        assert any(
            record.name == "direct_url" and record.state == "active"
            for record in manager.get_plugin_records()
        )


@pytest.mark.unit
def test_source_plugin_manager_reload_discovers_external_plugin_and_preserves_old_snapshot():
    with TemporaryDirectory() as tmp_dir:
        plugins_dir = Path(tmp_dir)
        manager = _manager(plugins_dir)
        old_registry = manager.get_active_registry()
        old_version = manager.registry_version

        plugin_file = plugins_dir / "mock_external.py"
        plugin_file.write_text(PLUGIN_SOURCE, encoding="utf-8")

        new_registry = manager.reload_plugins()
        req = MediaRequest(request_id="r2", source_hint="mock_external", query="mock://song")

        assert manager.registry_version == old_version + 1
        assert new_registry is manager.get_active_registry()
        assert new_registry is not old_registry
        assert new_registry.get_plugin(req.source_hint, req).name == "mock_external"
        with pytest.raises(Exception):
            old_registry.get_plugin(req.source_hint, req)
        assert any(
            record.name == "mock_external" and record.state == "active"
            for record in manager.get_plugin_records()
        )


@pytest.mark.unit
def test_source_plugin_manager_disable_enable_and_uninstall_external_plugin():
    with TemporaryDirectory() as tmp_dir:
        plugins_dir = Path(tmp_dir)
        manager = _manager(plugins_dir)

        uploaded = manager.upload_plugin("mock_external.py", PLUGIN_SOURCE.encode("utf-8"))
        req = MediaRequest(request_id="r3", source_hint="mock_external", query="mock://song")

        assert uploaded == {
            "name": "mock_external",
            "origin": "external",
            "status": "active",
            "version": "1.0.0",
            "error": "",
        }
        assert manager.get_active_registry().get_plugin(req.source_hint, req).name == "mock_external"

        disabled = manager.disable_plugin("mock_external")
        assert disabled["status"] == "disabled"
        assert "mock_external" in manager.disabled_plugins
        with pytest.raises(Exception):
            manager.get_active_registry().get_plugin(req.source_hint, req)

        enabled = manager.enable_plugin("mock_external")
        assert enabled["status"] == "active"
        assert "mock_external" not in manager.disabled_plugins
        assert manager.get_active_registry().get_plugin(req.source_hint, req).name == "mock_external"

        removed = manager.uninstall_plugin("mock_external")
        assert removed == {"success": True, "name": "mock_external"}
        assert not (plugins_dir / "mock_external.py").exists()
        with pytest.raises(Exception):
            manager.get_active_registry().get_plugin(req.source_hint, req)


@pytest.mark.unit
def test_source_plugin_manager_protects_builtin_from_upload_override_and_delete():
    with TemporaryDirectory() as tmp_dir:
        manager = _manager(Path(tmp_dir))

        with pytest.raises(PermissionError):
            manager.upload_plugin("override_direct_url.py", BUILTIN_OVERRIDE_SOURCE.encode("utf-8"))

        with pytest.raises(PermissionError):
            manager.uninstall_plugin("direct_url")


@pytest.mark.unit
def test_source_plugin_manager_can_disable_and_enable_builtin_plugin():
    with TemporaryDirectory() as tmp_dir:
        manager = _manager(Path(tmp_dir))
        req = MediaRequest(
            request_id="r4",
            source_hint="direct_url",
            query="https://example.com/a.mp3",
        )

        disabled = manager.disable_plugin("direct_url")
        assert disabled["status"] == "disabled"
        with pytest.raises(Exception):
            manager.get_active_registry().get_plugin(req.source_hint, req)

        enabled = manager.enable_plugin("direct_url")
        assert enabled["status"] == "active"
        assert manager.get_active_registry().get_plugin(req.source_hint, req).name == "direct_url"


@pytest.mark.unit
def test_playback_facade_rebuilds_core_when_source_registry_version_changes(monkeypatch):
    created_registries: list[object] = []

    class _FakeManager:
        def __init__(self):
            self.registry_version = 1
            self._registry = object()

        def get_active_registry(self):
            return self._registry

    class _FakeCoordinator:
        def __init__(self, **kwargs):
            created_registries.append(kwargs["source_registry"])
            self._source_registry = kwargs["source_registry"]

    class _FakeTransport:
        def __init__(self, *args, **kwargs):
            pass

    class _FakeRouter:
        def __init__(self, policy=None):
            self.policy = policy

        def register_transport(self, transport):
            return None

    class _XM:
        def __init__(self):
            self.config = type("C", (), {"conf_path": "."})()
            self.music_library = type(
                "ML",
                (),
                {"get_proxy_url": staticmethod(lambda origin_url, name=None: origin_url)},
            )()
            self.online_music_service = object()

        async def get_player_status(self, did: str):
            _ = did
            return {"status": 1}

    monkeypatch.setattr("xiaomusic.playback.facade.PlaybackCoordinator", _FakeCoordinator)
    monkeypatch.setattr("xiaomusic.playback.facade.MinaTransport", _FakeTransport)
    monkeypatch.setattr("xiaomusic.playback.facade.MiioTransport", _FakeTransport)
    monkeypatch.setattr("xiaomusic.playback.facade.TransportRouter", _FakeRouter)

    manager = _FakeManager()
    facade = PlaybackFacade(_XM(), source_plugin_manager=manager)

    core_v1 = facade._core()
    assert created_registries == [manager.get_active_registry()]

    manager._registry = object()
    manager.registry_version = 2
    core_v2 = facade._core()

    assert core_v2 is not core_v1
    assert created_registries == [created_registries[0], manager.get_active_registry()]
