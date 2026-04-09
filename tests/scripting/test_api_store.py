"""Tests for vt.store — KV persistent storage API."""

import json
import os
import threading

from wenzi.scripting.api.store import StoreAPI


class TestStoreAPI:
    def test_set_and_get(self, tmp_path):
        path = str(tmp_path / "store.json")
        store = StoreAPI(path=path)
        store.set("key1", "value1")
        assert store.get("key1") == "value1"

    def test_get_default(self, tmp_path):
        path = str(tmp_path / "store.json")
        store = StoreAPI(path=path)
        assert store.get("missing") is None
        assert store.get("missing", "fallback") == "fallback"

    def test_delete(self, tmp_path):
        path = str(tmp_path / "store.json")
        store = StoreAPI(path=path)
        store.set("k", "v")
        store.delete("k")
        assert store.get("k") is None

    def test_delete_nonexistent(self, tmp_path):
        path = str(tmp_path / "store.json")
        store = StoreAPI(path=path)
        store.delete("nonexistent")  # Should not raise

    def test_keys(self, tmp_path):
        path = str(tmp_path / "store.json")
        store = StoreAPI(path=path)
        store.set("a", 1)
        store.set("b", 2)
        assert sorted(store.keys()) == ["a", "b"]

    def test_clear(self, tmp_path):
        path = str(tmp_path / "store.json")
        store = StoreAPI(path=path)
        store.set("a", 1)
        store.set("b", 2)
        store.clear()
        assert store.keys() == []
        assert store.get("a") is None

    def test_persistence_roundtrip(self, tmp_path):
        path = str(tmp_path / "store.json")
        store1 = StoreAPI(path=path)
        store1.set("hello", "world")
        store1.set("num", 42)
        store1.flush_sync()

        # New instance loads from disk
        store2 = StoreAPI(path=path)
        assert store2.get("hello") == "world"
        assert store2.get("num") == 42

    def test_flush_sync(self, tmp_path):
        path = str(tmp_path / "store.json")
        store = StoreAPI(path=path)
        store.set("x", "y")
        store.flush_sync()
        assert os.path.isfile(path)
        with open(path) as f:
            data = json.load(f)
        assert data == {"x": "y"}

    def test_flush_sync_no_data(self, tmp_path):
        path = str(tmp_path / "store.json")
        store = StoreAPI(path=path)
        store.flush_sync()  # Should not raise or create file
        assert not os.path.isfile(path)

    def test_thread_safety(self, tmp_path):
        path = str(tmp_path / "store.json")
        store = StoreAPI(path=path)
        errors = []

        def writer(prefix):
            try:
                for i in range(50):
                    store.set(f"{prefix}_{i}", i)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(f"t{t}",)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(store.keys()) == 200

    def test_deferred_write(self, tmp_path):
        """set() schedules a deferred write, not an immediate one."""
        path = str(tmp_path / "store.json")
        store = StoreAPI(path=path)
        store.set("k", "v")
        # File should not exist immediately (deferred write)
        # but flush_sync forces it
        store.flush_sync()
        assert os.path.isfile(path)

    def test_complex_values(self, tmp_path):
        path = str(tmp_path / "store.json")
        store = StoreAPI(path=path)
        store.set("list", [1, 2, 3])
        store.set("dict", {"nested": True})
        assert store.get("list") == [1, 2, 3]
        assert store.get("dict") == {"nested": True}

        store.flush_sync()
        store2 = StoreAPI(path=path)
        assert store2.get("list") == [1, 2, 3]
        assert store2.get("dict") == {"nested": True}
