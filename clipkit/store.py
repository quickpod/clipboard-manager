r"""Persistent clipboard-history + pinned-snippets store (pure stdlib / JSON).

The store keeps a capped, newest-first list of clipboard entries plus a set of
"pinned" snippets that survive a Clear.  It is deliberately independent of any
real clipboard so it can be imported and unit-tested on a headless box -- the
GUI feeds it captured text, tests feed it strings directly.

On Windows the JSON file lives at ``%LOCALAPPDATA%\ClipboardManager\history.json``;
elsewhere it falls back to ``~/.clipboardmanager/history.json``.  The path can be
overridden per-instance (``Store(path=...)``) or via the ``CLIPKIT_STORE_PATH``
environment variable -- both used by the test-suite to stay off the real store.

Design rules baked in here:
  * Only what the user actually copied is ever written to disk -- nothing else.
  * Capture can be paused (``pause()``/``resume()``); while paused, ``add()``
    from the monitor is ignored.  A deliberate user/manual add can force through.
  * Every write is atomic (tmp file + ``os.replace``) and best-effort: a corrupt
    or unwritable store never raises out of a normal operation.
"""

from __future__ import annotations

import json
import os
import time

APP_DIRNAME = "ClipboardManager"
STORE_NAME = "history.json"
DEFAULT_CAP = 200
ENV_PATH = "CLIPKIT_STORE_PATH"
VALID_THEMES = ("light", "dark")


def default_store_path():
    r"""Return the on-disk path for the history store.

    ``CLIPKIT_STORE_PATH`` wins if set.  Otherwise
    ``%LOCALAPPDATA%\ClipboardManager\history.json`` on Windows, and
    ``~/.clipboardmanager/history.json`` everywhere else.
    """
    override = os.environ.get(ENV_PATH)
    if override:
        return override
    local = os.environ.get("LOCALAPPDATA")
    if local and os.name == "nt":
        return os.path.join(local, APP_DIRNAME, STORE_NAME)
    return os.path.join(os.path.expanduser("~"), "." + APP_DIRNAME.lower(),
                        STORE_NAME)


class Store:
    """A JSON-backed clipboard history + pinned snippets.

    Items are dicts ``{"id": int, "text": str, "ts": float, "pinned": bool}``
    held newest-first.  All mutating methods persist immediately (best-effort).
    """

    def __init__(self, path=None, cap=DEFAULT_CAP):
        self.path = path or default_store_path()
        self.cap = max(1, int(cap))
        self._items = []          # newest-first
        self._seq = 0             # last assigned id
        self._paused = False      # capture flag (in-memory; capture on at start)
        self._theme = "light"
        self._load()

    # ---- persistence ---------------------------------------------------
    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return
        except Exception:
            return  # corrupt/unreadable -> start empty, never fatal
        if not isinstance(data, dict):
            return
        items = []
        for raw in data.get("items", []) or []:
            if not isinstance(raw, dict):
                continue
            text = raw.get("text")
            if not isinstance(text, str) or text == "":
                continue
            try:
                iid = int(raw.get("id"))
            except (TypeError, ValueError):
                continue
            items.append({
                "id": iid,
                "text": text,
                "ts": float(raw.get("ts") or 0.0),
                "pinned": bool(raw.get("pinned", False)),
            })
        self._items = items
        seq = data.get("seq")
        self._seq = int(seq) if isinstance(seq, int) else 0
        # keep the sequence monotonic even if the file was hand-edited
        for it in items:
            if it["id"] > self._seq:
                self._seq = it["id"]
        theme = data.get("theme")
        if theme in VALID_THEMES:
            self._theme = theme

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            payload = {
                "seq": self._seq,
                "theme": self._theme,
                "items": self._items,
            }
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            pass  # best-effort; a read-only disk must not break the app

    # ---- capture flags -------------------------------------------------
    def pause(self):
        """Stop capturing new clipboard entries (manual adds can still force)."""
        self._paused = True

    def resume(self):
        """Resume capturing new clipboard entries."""
        self._paused = False

    def is_paused(self):
        return self._paused

    # ---- theme (persisted; used by the GUI) ----------------------------
    def get_theme(self):
        return self._theme

    def set_theme(self, theme):
        if theme in VALID_THEMES and theme != self._theme:
            self._theme = theme
            self._save()

    # ---- core operations -----------------------------------------------
    def add(self, text, force=False):
        """Add *text* as the newest entry.

        Returns the new item's id, or ``None`` when nothing was stored.  Empty /
        whitespace-only text is ignored, a value identical to the current newest
        entry is de-duplicated (consecutive dedupe), and while capture is paused
        a non-forced add is dropped.  ``force=True`` (a deliberate user/manual
        add) bypasses the pause gate only.
        """
        if not isinstance(text, str):
            return None
        if text.strip() == "":
            return None
        if self._paused and not force:
            return None
        if self._items and self._items[0]["text"] == text:
            return None  # consecutive duplicate
        self._seq += 1
        item = {"id": self._seq, "text": text, "ts": time.time(),
                "pinned": False}
        self._items.insert(0, item)
        self._trim()
        self._save()
        return item["id"]

    def _trim(self):
        """Drop the oldest *non-pinned* items beyond the cap (pinned survive)."""
        if len(self._items) <= self.cap:
            return
        kept = []
        overflow = len(self._items) - self.cap
        # walk oldest-first so the oldest non-pinned go first
        for item in reversed(self._items):
            if overflow > 0 and not item["pinned"]:
                overflow -= 1
                continue
            kept.append(item)
        kept.reverse()
        self._items = kept

    def items(self):
        """All entries, newest-first (a shallow copy safe for callers)."""
        return [dict(it) for it in self._items]

    def pinned(self):
        """Pinned entries only, newest-first."""
        return [dict(it) for it in self._items if it["pinned"]]

    def get(self, item_id):
        """Return the entry dict for *item_id*, or ``None``."""
        for it in self._items:
            if it["id"] == item_id:
                return dict(it)
        return None

    def _find(self, item_id):
        for it in self._items:
            if it["id"] == item_id:
                return it
        return None

    def pin(self, item_id):
        """Mark an entry pinned. Returns True if it existed."""
        it = self._find(item_id)
        if it is None:
            return False
        if not it["pinned"]:
            it["pinned"] = True
            self._save()
        return True

    def unpin(self, item_id):
        """Clear an entry's pinned flag. Returns True if it existed."""
        it = self._find(item_id)
        if it is None:
            return False
        if it["pinned"]:
            it["pinned"] = False
            self._save()
        return True

    def delete(self, item_id):
        """Remove an entry by id. Returns True if something was removed."""
        before = len(self._items)
        self._items = [it for it in self._items if it["id"] != item_id]
        if len(self._items) != before:
            self._save()
            return True
        return False

    def clear(self, keep_pinned=True):
        """Remove entries. With ``keep_pinned`` (default) pinned entries stay.

        Returns the number of entries removed.
        """
        before = len(self._items)
        if keep_pinned:
            self._items = [it for it in self._items if it["pinned"]]
        else:
            self._items = []
        removed = before - len(self._items)
        if removed:
            self._save()
        return removed

    def search(self, query):
        """Case-insensitive substring search over entry text (newest-first)."""
        q = (query or "").strip().lower()
        if not q:
            return self.items()
        return [dict(it) for it in self._items if q in it["text"].lower()]
