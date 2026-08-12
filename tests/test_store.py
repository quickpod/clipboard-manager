"""Store tests -- fully headless; never touches a real clipboard."""

import os

import pytest

from clipkit.store import Store, default_store_path


@pytest.fixture()
def store(tmp_path):
    return Store(path=str(tmp_path / "history.json"), cap=200)


def test_add_returns_id_and_lists_newest_first(store):
    a = store.add("first")
    b = store.add("second")
    c = store.add("third")
    assert [a, b, c] == [1, 2, 3]
    texts = [it["text"] for it in store.items()]
    assert texts == ["third", "second", "first"]


def test_add_ignores_empty_and_whitespace(store):
    assert store.add("") is None
    assert store.add("   \n\t ") is None
    assert store.add(None) is None
    assert store.items() == []


def test_add_dedupes_consecutive_only(store):
    store.add("dup")
    assert store.add("dup") is None          # consecutive -> skipped
    store.add("other")
    assert store.add("dup") == 3             # non-consecutive -> allowed
    assert [it["text"] for it in store.items()] == ["dup", "other", "dup"]


def test_cap_enforced_keeps_newest(tmp_path):
    store = Store(path=str(tmp_path / "h.json"), cap=5)
    for i in range(10):
        store.add(f"item-{i}")
    items = store.items()
    assert len(items) == 5
    assert items[0]["text"] == "item-9"
    assert items[-1]["text"] == "item-5"


def test_cap_never_drops_pinned(tmp_path):
    store = Store(path=str(tmp_path / "h.json"), cap=3)
    first = store.add("keep-me")
    store.pin(first)
    for i in range(10):
        store.add(f"item-{i}")
    texts = [it["text"] for it in store.items()]
    assert "keep-me" in texts
    assert len(store.items()) == 3  # pinned + 2 newest non-pinned


def test_pin_unpin_and_pinned_list(store):
    a = store.add("alpha")
    b = store.add("beta")
    assert store.pin(a) is True
    assert store.pin(999) is False          # missing id
    pinned_texts = [it["text"] for it in store.pinned()]
    assert pinned_texts == ["alpha"]
    assert store.unpin(a) is True
    assert store.pinned() == []
    assert b  # silence unused


def test_delete(store):
    a = store.add("gone")
    store.add("stay")
    assert store.delete(a) is True
    assert store.delete(a) is False
    assert [it["text"] for it in store.items()] == ["stay"]


def test_clear_keep_pinned_default(store):
    a = store.add("pinned")
    store.add("plain-1")
    store.add("plain-2")
    store.pin(a)
    removed = store.clear()                  # keep_pinned defaults True
    assert removed == 2
    assert [it["text"] for it in store.items()] == ["pinned"]


def test_clear_all(store):
    a = store.add("pinned")
    store.add("plain")
    store.pin(a)
    removed = store.clear(keep_pinned=False)
    assert removed == 2
    assert store.items() == []


def test_search_substring_case_insensitive(store):
    store.add("Hello World")
    store.add("goodbye")
    store.add("a WORLD apart")
    hits = [it["text"] for it in store.search("world")]
    assert hits == ["a WORLD apart", "Hello World"]  # newest-first
    assert store.search("zzz") == []
    # empty query returns everything
    assert len(store.search("")) == 3


def test_pause_blocks_capture_but_force_adds(store):
    store.pause()
    assert store.is_paused() is True
    assert store.add("captured") is None     # paused -> dropped
    assert store.add("manual", force=True) == 1
    store.resume()
    assert store.add("captured") == 2


def test_persistence_round_trip(tmp_path):
    path = str(tmp_path / "h.json")
    s1 = Store(path=path)
    a = s1.add("persist me")
    s1.pin(a)
    s1.add("also me")
    s1.set_theme("dark")

    s2 = Store(path=path)
    assert [it["text"] for it in s2.items()] == ["also me", "persist me"]
    assert [it["text"] for it in s2.pinned()] == ["persist me"]
    assert s2.get_theme() == "dark"
    # ids stay monotonic after reload
    assert s2.add("new one") == 3


def test_corrupt_store_starts_empty(tmp_path):
    path = tmp_path / "h.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = Store(path=str(path))
    assert store.items() == []
    assert store.add("fresh") == 1


def test_default_store_path_env_override(monkeypatch):
    monkeypatch.setenv("CLIPKIT_STORE_PATH", "/custom/history.json")
    assert default_store_path() == "/custom/history.json"


def test_default_store_path_fallback(monkeypatch):
    monkeypatch.delenv("CLIPKIT_STORE_PATH", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    path = default_store_path()
    assert path.endswith(os.path.join(".clipboardmanager", "history.json"))
