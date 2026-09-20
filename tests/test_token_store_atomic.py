import json
import threading

import pytest

from xiaomusic.security.token_store import TokenStore


def test_flush_atomic_no_partial_json(tmp_path):
    token_path = tmp_path / "auth.json"
    store = TokenStore(token_path)
    store.save({"userId": "u0", "serviceToken": "s0"})

    errors = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                text = token_path.read_text(encoding="utf-8")
                json.loads(text)
            except PermissionError:
                continue
            except json.JSONDecodeError as e:
                errors.append(e)
                stop.set()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    for i in range(30):
        store.update({"userId": f"u{i}", "serviceToken": f"s{i}"}, reason="test")
        store.flush()
    stop.set()
    t.join(timeout=2)
    assert errors == []


def test_update_flush_roundtrip(tmp_path):
    token_path = tmp_path / "auth.json"
    store = TokenStore(token_path)
    expected = {
        "userId": "u1",
        "serviceToken": "st1",
        "passToken": "pt1",
        "ssecurity": "sec1",
    }
    store.update(expected, reason="roundtrip")
    store.flush()

    store2 = TokenStore(token_path)
    loaded = store2.load().data
    assert loaded == expected


def test_concurrent_updates_serialized(tmp_path):
    token_path = tmp_path / "auth.json"
    store = TokenStore(token_path)

    count = 50

    def worker(i: int):
        payload = {
            "userId": f"u{i}",
            "serviceToken": f"s{i}",
            "seq": i,
        }
        store.save(payload)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    data = json.loads(token_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "seq" in data
    assert 0 <= int(data["seq"]) < count


def test_commit_failure_keeps_memory_mirror_unchanged(tmp_path, monkeypatch):
    token_path = tmp_path / "auth.json"
    store = TokenStore(token_path)
    store.save({"userId": "u0", "serviceToken": "s0"})
    before = (dict(store._token), store._dirty, store._loaded)

    def fail_write(_data):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_atomic_write_unlocked", fail_write)
    with pytest.raises(OSError):
        store.commit({"userId": "u1", "serviceToken": "s1", "saveTime": 2})

    assert store._token == before[0]
    assert store._dirty == before[1]
    assert store._loaded == before[2]
    assert store.get() == before[0]


def test_commit_persist_false_is_explicit_memory_only(tmp_path):
    class Config:
        persist_token = False
        auth_token_path = str(tmp_path / "auth.json")

    store = TokenStore(Config())
    candidate = {"userId": "u1", "serviceToken": "memory-only", "saveTime": 3}

    store.commit(candidate, reason="memory-only")

    assert store.get() == candidate
    assert store._dirty is False
    assert not (tmp_path / "auth.json").exists()
