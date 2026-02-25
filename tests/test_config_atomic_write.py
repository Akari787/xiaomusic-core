import json
import threading

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


def test_config_atomic_write_with_threads(tmp_path):
    path = tmp_path / "setting.json"
    mgr = ConfigManager(_DummyConfig(path), _DummyLog())

    def writer(i):
        mgr.do_saveconfig({"value": i})

    threads = []
    for i in range(20):
        t = threading.Thread(target=writer, args=(i,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    assert "value" in data
