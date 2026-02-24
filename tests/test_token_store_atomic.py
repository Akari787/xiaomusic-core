import json
import threading

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
                # Windows can transiently deny reads during os.replace.
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
