"""Clipboard access + polling, guarded so a missing backend never crashes us.

``pyperclip`` is the only third-party dependency.  On a headless box (no X11 /
Wayland clipboard tool) ``pyperclip.paste()`` raises; every function here catches
that so the rest of the app -- browsing history, pinned snippets, manual add --
keeps working with no clipboard at all.

The GUI runs :func:`poll_clipboard` on a background thread and marshals new text
back to the store via ``root.after``.
"""

from __future__ import annotations

import time


def _pyperclip():
    """Import pyperclip lazily; return the module or ``None`` if unavailable."""
    try:
        import pyperclip
        return pyperclip
    except Exception:
        return None


def clipboard_available():
    """True if a working copy/paste backend is present (best-effort probe)."""
    pc = _pyperclip()
    if pc is None:
        return False
    try:
        pc.paste()
        return True
    except Exception:
        return False


def get_clipboard():
    """Return the current clipboard text, or ``None`` if unreadable/unavailable."""
    pc = _pyperclip()
    if pc is None:
        return None
    try:
        value = pc.paste()
        return value if isinstance(value, str) else None
    except Exception:
        return None


def set_clipboard(text):
    """Copy *text* to the clipboard. Returns True on success, False otherwise."""
    pc = _pyperclip()
    if pc is None:
        return False
    try:
        pc.copy("" if text is None else str(text))
        return True
    except Exception:
        return False


def poll_clipboard(on_new, interval=0.7, stop_event=None, on_unavailable=None):
    """Poll the clipboard, calling ``on_new(text)`` whenever the text changes.

    Runs until *stop_event* (a ``threading.Event``) is set, or -- if none is
    given -- forever (intended to run on a daemon thread).  Degrades gracefully:
    if the clipboard backend is unavailable it invokes ``on_unavailable`` (if
    given) once and returns instead of spinning or raising, so a headless host
    simply gets no capture while the rest of the GUI keeps working.

    The very first observed value is treated as the baseline and is *not*
    reported, so launching the app does not re-capture whatever was already on
    the clipboard.
    """
    pc = _pyperclip()
    if pc is None:
        if on_unavailable:
            on_unavailable()
        return

    last = None
    primed = False
    while stop_event is None or not stop_event.is_set():
        try:
            current = pc.paste()
        except Exception:
            # backend went away (or never worked) -> stop quietly.
            if on_unavailable:
                on_unavailable()
            return
        if isinstance(current, str):
            if not primed:
                last, primed = current, True
            elif current != last:
                last = current
                try:
                    on_new(current)
                except Exception:
                    pass  # a callback error must never kill the poll loop
        _sleep(interval, stop_event)


def _sleep(interval, stop_event):
    """Sleep *interval* seconds but wake early if the stop event fires."""
    if stop_event is not None:
        stop_event.wait(interval)
    else:
        time.sleep(interval)
