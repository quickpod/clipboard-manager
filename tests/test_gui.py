"""GUI tests for the 1.1.0 Aura layout-language rework (Ditto benchmark).

Pure helpers run anywhere; the App tests need a display (run the suite under
``xvfb-run -a python3 -m pytest``) and are skipped headless, mirroring the
house pattern.  Everything is hermetic: CLIPKIT_STORE_PATH lives in the
test's tmp dir, so nothing touches the real history.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clipkit import gui  # noqa: E402
from clipkit.store import Store  # noqa: E402


# ---------------------------------------------------------------------------
# pure helpers (no display needed)
# ---------------------------------------------------------------------------
def test_rel_date_buckets():
    now = time.time()
    assert gui.rel_date(None) == ""
    assert gui.rel_date(now - 10, now) == "now"
    assert gui.rel_date(now - 300, now) == "5m"
    assert gui.rel_date(now - 400 * 86400, now)      # a year form


def test_is_link():
    assert gui.is_link("https://example.com/x")
    assert gui.is_link("  http://a.b  ")
    assert not gui.is_link("https://a b")            # embedded space
    assert not gui.is_link("hello https://a.b")
    assert not gui.is_link("https://a.b\nsecond")


def test_matches_filter():
    now = time.time()
    fresh = {"text": "https://x.y", "pinned": False, "ts": now}
    multi = {"text": "a\nb", "pinned": True, "ts": now - 5 * 86400}
    assert gui.matches_filter(fresh, "all", now)
    assert gui.matches_filter(fresh, "links", now)
    assert gui.matches_filter(fresh, "today", now)
    assert not gui.matches_filter(fresh, "pinned", now)
    assert gui.matches_filter(multi, "pinned", now)
    assert gui.matches_filter(multi, "multiline", now)
    assert not gui.matches_filter(multi, "today", now)


def test_one_line_truncates():
    assert gui._one_line("a  b\nc") == "a b c"
    assert gui._one_line("x" * 200).endswith("…")
    assert gui._one_line("   ") == "(blank)"


# ---------------------------------------------------------------------------
# the App under Xvfb
# ---------------------------------------------------------------------------
def _display():
    return bool(os.environ.get("DISPLAY")) and os.name != "nt"


needs_display = pytest.mark.skipif(not _display(),
                                   reason="needs a display (xvfb-run)")


@pytest.fixture()
def app(tmp_path, monkeypatch):
    path = str(tmp_path / "history.json")
    monkeypatch.setenv("CLIPKIT_STORE_PATH", path)
    seed = Store(path=path)
    seed.add("first clip")
    seed.add("https://quickopen.ai/page")
    b = seed.add("multi\nline\nclip")
    seed.pin(b)
    seed.set_theme("dark")   # deterministic; no OS follow in tests
    App = gui.build_app()
    a = App()
    a.update()
    yield a
    try:
        a._on_close()
    except Exception:
        pass


@needs_display
def test_list_shows_seeded_clips_newest_first(app):
    rows = app.tree.get_children()
    assert len(rows) == 3
    first = app.tree.item(rows[0])["values"][0]
    assert "multi line clip" in first and first.startswith("★")


@needs_display
def test_filters(app):
    app._set_filter("links")
    app.update()
    assert len(app.tree.get_children()) == 1
    app._set_filter("pinned")
    app.update()
    assert len(app.tree.get_children()) == 1
    app._set_filter("multiline")
    app.update()
    assert len(app.tree.get_children()) == 1
    app._set_filter("all")
    app.update()
    assert len(app.tree.get_children()) == 3


@needs_display
def test_search_filters_live(app):
    app.search.set("quickopen")
    app.refresh()
    assert len(app.tree.get_children()) == 1
    app.search.set("")
    app.refresh()
    assert len(app.tree.get_children()) == 3


@needs_display
def test_select_shows_detail_and_pin_toggle(app):
    rows = app.tree.get_children()
    app.tree.selection_set(rows[-1])       # oldest: "first clip"
    app._on_select()
    assert app.detail_text.get("1.0", "end-1c") == "first clip"
    assert app._pin_btn.cget("text") == "Pin"
    app._toggle_pin_selected()
    item = app._selected_item()
    assert item["pinned"]
    assert app._pin_btn.cget("text") == "Unpin"


@needs_display
def test_delete_and_clear_keep_pinned(app):
    rows = app.tree.get_children()
    app.tree.selection_set(rows[-1])
    app._on_select()
    app._delete_selected()
    assert len(app.store.items()) == 2
    app._clear_history()                    # pinned multi-line clip survives
    texts = [it["text"] for it in app.store.items()]
    assert texts == ["multi\nline\nclip"]


@needs_display
def test_transform_saves_result_as_newest_clip(app):
    rows = app.tree.get_children()
    app.tree.selection_set(rows[-1])       # "first clip"
    app._on_select()
    app.transform_var.set("UPPERCASE")
    app._apply_transform()
    newest = app.store.items()[0]["text"]
    assert newest == "FIRST CLIP"
    # the new clip is selected and shown in the detail pane
    assert app.detail_text.get("1.0", "end-1c") == "FIRST CLIP"


@needs_display
def test_empty_state_when_store_empty(app):
    app.store.clear(keep_pinned=False)
    app.refresh()
    app.update()
    assert app.empty_all.winfo_manager() == "place"
    app.store.add("back again")
    app.refresh()
    app.update()
    assert app.empty_all.winfo_manager() == ""


@needs_display
def test_pause_resume_capture(app):
    app._toggle_pause()
    assert app.store.is_paused()
    assert app._pause_btn.cget("text") == "Resume capture"
    app._toggle_pause()
    assert not app.store.is_paused()


@needs_display
def test_theme_flip_smoke(app):
    app.set_theme("light")
    app.update()
    app.set_theme("dark")
    app.update()
    assert app.theme == "dark"


@needs_display
def test_layout_language_surfaces_exist(app):
    assert set(app._sections) >= {"history", "about"}
    assert app._filter_rows and "all" in app._filter_rows
    assert app.search is not None
