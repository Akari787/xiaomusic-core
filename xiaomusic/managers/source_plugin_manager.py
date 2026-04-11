from __future__ import annotations

import importlib.util
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable
from uuid import uuid4

from xiaomusic.core.source import SourcePlugin, SourceRegistry


@dataclass(slots=True)
class SourcePluginRecord:
    name: str
    origin: str
    path: str
    state: str
    error: str = ""
    version: str | None = None


class SourcePluginManager:
    """Manage active source registry snapshots and basic source plugin operations."""

    def __init__(
        self,
        *,
        register_defaults: Callable[[SourceRegistry], None],
        plugins_dir: str | Path,
    ) -> None:
        self._register_defaults = register_defaults
        self.plugins_dir = Path(plugins_dir)
        self._lock = threading.Lock()
        self._active_registry = SourceRegistry()
        self._registry_version = 0
        self._plugin_records: list[SourcePluginRecord] = []
        self._disabled_plugins: set[str] = set()
        self.reload_plugins()

    @property
    def registry_version(self) -> int:
        return self._registry_version

    @property
    def disabled_plugins(self) -> set[str]:
        return set(self._disabled_plugins)

    def get_active_registry(self) -> SourceRegistry:
        return self._active_registry

    def get_plugin_records(self) -> list[SourcePluginRecord]:
        return list(self._plugin_records)

    def describe_plugins(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        final_records: dict[str, SourcePluginRecord] = {}
        for record in self._plugin_records:
            final_records[record.name] = record

        for name in sorted(final_records):
            record = final_records[name]
            items.append(
                {
                    "name": record.name,
                    "origin": record.origin,
                    "status": self._public_status(record.state),
                    "version": record.version,
                    "error": record.error,
                }
            )
        return items

    def get_plugin_info(self, name: str) -> dict[str, Any]:
        plugin_name = str(name or "").strip()
        if not plugin_name:
            raise FileNotFoundError("source plugin not found")
        for item in self.describe_plugins():
            if item["name"] == plugin_name:
                return item
        raise FileNotFoundError(f"source plugin not found: {plugin_name}")

    def reload_summary(self) -> dict[str, int | bool]:
        self.reload_plugins()
        items = self.describe_plugins()
        return {
            "reloaded": True,
            "registry_version": self.registry_version,
            "loaded_count": sum(1 for item in items if item.get("status") == "active"),
            "failed_count": sum(1 for item in items if item.get("status") == "failed"),
        }

    def upload_plugin(self, filename: str, content: bytes) -> dict[str, Any]:
        safe_name = os.path.basename(str(filename or "").strip())
        if not safe_name or not safe_name.endswith(".py"):
            raise ValueError("only .py source plugin file is allowed")

        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        plugin = self._validate_external_plugin_bytes(safe_name, content)
        builtin_names = self._builtin_plugin_names()
        if plugin.name in builtin_names:
            raise PermissionError(f"builtin source plugin cannot be overridden: {plugin.name}")

        target_path = self.plugins_dir / safe_name
        with open(target_path, "wb") as f:
            f.write(content)

        self.reload_plugins()
        return self.get_plugin_info(plugin.name)

    def uninstall_plugin(self, name: str) -> dict[str, Any]:
        record = self._find_record(name)
        if record is None:
            raise FileNotFoundError(f"source plugin not found: {name}")
        if record.origin != "external":
            raise PermissionError(f"builtin source plugin cannot be deleted: {name}")
        if not record.path:
            raise FileNotFoundError(f"source plugin file missing: {name}")
        path = Path(record.path)
        if not path.exists():
            raise FileNotFoundError(f"source plugin file missing: {name}")
        path.unlink()
        self._disabled_plugins.discard(record.name)
        self._disabled_plugins.discard(path.stem)
        self.reload_plugins()
        return {"success": True, "name": record.name}

    def enable_plugin(self, name: str) -> dict[str, Any]:
        record = self._find_record(name)
        if record is None:
            raise FileNotFoundError(f"source plugin not found: {name}")
        self._disabled_plugins.discard(record.name)
        if record.path:
            self._disabled_plugins.discard(Path(record.path).stem)
        self.reload_plugins()
        return self.get_plugin_info(record.name)

    def disable_plugin(self, name: str) -> dict[str, Any]:
        record = self._find_record(name)
        if record is None:
            raise FileNotFoundError(f"source plugin not found: {name}")
        self._disabled_plugins.add(record.name)
        if record.path:
            self._disabled_plugins.add(Path(record.path).stem)
        self.reload_plugins()
        return self.get_plugin_info(record.name)

    def reload_plugins(self) -> SourceRegistry:
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        registry = SourceRegistry()
        records: list[SourcePluginRecord] = []

        self._register_defaults(registry)
        builtin_plugins = getattr(registry, "_plugins", {})
        builtin_names = set(builtin_plugins.keys()) if isinstance(builtin_plugins, dict) else set()
        disabled_names = set(self._disabled_plugins)

        final_registry = SourceRegistry()
        if isinstance(builtin_plugins, dict):
            for name in sorted(builtin_plugins):
                plugin = builtin_plugins[name]
                if name in disabled_names:
                    records.append(
                        SourcePluginRecord(
                            name=name,
                            origin="builtin",
                            path="",
                            state="disabled",
                            version=self._plugin_version(plugin),
                        )
                    )
                else:
                    final_registry.register(plugin)
                    records.append(
                        SourcePluginRecord(
                            name=name,
                            origin="builtin",
                            path="",
                            state="active",
                            version=self._plugin_version(plugin),
                        )
                    )

        for plugin_path in sorted(self.plugins_dir.glob("*.py")):
            records.extend(self._load_external_plugin(plugin_path, final_registry, disabled_names))

        valid_disabled = set()
        for record in records:
            if record.name in disabled_names:
                valid_disabled.add(record.name)
            if record.path and Path(record.path).stem in disabled_names:
                valid_disabled.add(Path(record.path).stem)
        for builtin_name in builtin_names:
            if builtin_name in disabled_names:
                valid_disabled.add(builtin_name)

        with self._lock:
            self._active_registry = final_registry
            self._plugin_records = records
            self._disabled_plugins = valid_disabled
            self._registry_version += 1
            return self._active_registry

    def _find_record(self, name: str) -> SourcePluginRecord | None:
        target = str(name or "").strip()
        if not target:
            return None
        final_records: dict[str, SourcePluginRecord] = {}
        by_stem: dict[str, SourcePluginRecord] = {}
        for record in self._plugin_records:
            final_records[record.name] = record
            if record.path:
                by_stem[Path(record.path).stem] = record
        return final_records.get(target) or by_stem.get(target)

    def _load_external_plugin(
        self,
        plugin_path: Path,
        registry: SourceRegistry,
        disabled_names: set[str],
    ) -> list[SourcePluginRecord]:
        records: list[SourcePluginRecord] = []
        try:
            module = self._load_module_from_path(plugin_path)
            plugin = self._instantiate_plugin(module, plugin_path)
            if plugin.name in self._builtin_plugin_names():
                raise PermissionError(
                    f"external source plugin cannot override builtin plugin: {plugin.name}"
                )
            version = self._plugin_version(plugin)
            if plugin.name in disabled_names or plugin_path.stem in disabled_names:
                records.append(
                    SourcePluginRecord(
                        name=plugin.name,
                        origin="external",
                        path=str(plugin_path),
                        state="disabled",
                        version=version,
                    )
                )
            else:
                registry.register(plugin)
                records.append(
                    SourcePluginRecord(
                        name=plugin.name,
                        origin="external",
                        path=str(plugin_path),
                        state="active",
                        version=version,
                    )
                )
        except Exception as exc:
            records.append(
                SourcePluginRecord(
                    name=plugin_path.stem,
                    origin="external",
                    path=str(plugin_path),
                    state="failed",
                    error=str(exc),
                    version=None,
                )
            )
        return records

    def _builtin_plugin_names(self) -> set[str]:
        registry = SourceRegistry()
        self._register_defaults(registry)
        plugins = getattr(registry, "_plugins", {})
        if isinstance(plugins, dict):
            return set(plugins.keys())
        return set()

    def _validate_external_plugin_bytes(self, filename: str, content: bytes) -> SourcePlugin:
        suffix = Path(filename).suffix or ".py"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            temp_path = Path(tmp.name)
        try:
            module = self._load_module_from_path(temp_path)
            return self._instantiate_plugin(module, temp_path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

    @staticmethod
    def _public_status(state: str) -> str:
        if state in {"active", "failed", "disabled"}:
            return state
        return state

    @staticmethod
    def _plugin_version(plugin: SourcePlugin) -> str | None:
        version = getattr(plugin, "version", None)
        if version in {None, ""}:
            version = getattr(plugin, "VERSION", None)
        if version in {None, ""}:
            return None
        return str(version)

    @staticmethod
    def _load_module_from_path(plugin_path: Path) -> ModuleType:
        module_name = f"xiaomusic_external_source_plugin_{plugin_path.stem}_{uuid4().hex}"
        spec = importlib.util.spec_from_file_location(module_name, plugin_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"failed to create import spec for plugin: {plugin_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _instantiate_plugin(module: ModuleType, plugin_path: Path) -> SourcePlugin:
        create_plugin = getattr(module, "create_plugin", None)
        if callable(create_plugin):
            plugin = create_plugin()
        else:
            raise TypeError(
                f"external source plugin must expose create_plugin(): {plugin_path.name}"
            )
        if not isinstance(plugin, SourcePlugin):
            raise TypeError(
                f"create_plugin() must return SourcePlugin instance: {plugin_path.name}"
            )
        return plugin
