import json

from xiaomusic.config_manager import ConfigManager


class _DummyConfig:
    def __init__(self, path):
        self._path = str(path)
        self.devices = {}

    def getsettingfile(self):
        return self._path


class _DummyLog:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


def test_do_saveconfig_atomic_multiple_writes(tmp_path):
    setting_path = tmp_path / "setting.json"
    mgr = ConfigManager(_DummyConfig(setting_path), _DummyLog())

    for i in range(40):
        mgr.do_saveconfig({"n": i, "items": [1, 2, 3]})
        data = json.loads(setting_path.read_text(encoding="utf-8"))
        assert data["n"] == i
