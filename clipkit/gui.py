#!/usr/bin/env python3
r"""Clipboard Manager -- a pure-stdlib + pyperclip tkinter GUI on the clipkit store.

A single main window:

  * a search box on top that filters the lists live;
  * a scrollable **History** list (newest first) -- each row has a one-line
    preview and per-row Pin/Unpin, Copy and Delete buttons; click a row to
    select it;
  * a separate **Pinned** section for snippets that survive Clear;
  * a **Transforms** toolbar that applies a transform to the selected entry and
    copies the result to the clipboard;
  * Clear (keeps pinned) and Pause/Resume-capture buttons, plus a dark-mode
    toggle whose choice is persisted in the store.

A background thread runs :func:`clipkit.monitor.poll_clipboard` to capture new
copies into the store; results are marshalled back onto the Tk thread with
``after``.  Everything degrades gracefully: with no display the app prints a
friendly note and returns 0, and with no clipboard backend it still browses,
pins and manually adds -- it just cannot auto-capture or copy.

Design goals mirror the QuickOpen house style (see pdf-toolkit/gui.py):
  * pure standard-library tkinter/ttk; the ONLY third-party dep is pyperclip,
    imported lazily and always guarded.
  * importing this module does nothing -- only :func:`main` builds a root window.
  * frozen-exe safe assets via ``sys._MEIPASS`` / the exe dir, never ``__file__``.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys
import threading

# NOTE: tkinter is imported lazily inside build_app()/main() so that merely
# importing this module (packaging, headless CI) never fails.

APP_NAME = "Clipboard Manager"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "Clipboard Manager — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai"
POLL_INTERVAL = 0.7

# ---- colour palettes (mirror the QuickOpen palette) -------------------------
PALETTES = {
    "light": {
        "bg": "#f5f7fa", "surface": "#ffffff", "text": "#141820",
        "muted": "#5b6472", "primary": "#2f5fe0", "primary_hi": "#2450c8",
        "entry": "#ffffff", "border": "#d5dae2", "sel": "#2f5fe0",
        "sel_fg": "#ffffff", "trough": "#e2e7ef", "ok": "#1f7a3d",
        "err": "#c0392b", "rowsel": "#e7edfb",
    },
    "dark": {
        "bg": "#0f1115", "surface": "#1a1e24", "text": "#f1f3f7",
        "muted": "#9aa4b2", "primary": "#5b86f7", "primary_hi": "#7098ff",
        "entry": "#1a1e24", "border": "#2a2f38", "sel": "#5b86f7",
        "sel_fg": "#0f1115", "trough": "#2a2f38", "ok": "#5bd68a",
        "err": "#ff6b5e", "rowsel": "#232a36",
    },
}


# ---------------------------------------------------------------------------
# Asset / frozen handling
# ---------------------------------------------------------------------------
def asset_path(name):
    """Locate a bundled asset from source OR a PyInstaller one-file build.

    For a frozen exe we look only at ``sys._MEIPASS`` and the executable's own
    directory (never ``__file__``).  From source we also consult the package
    dir, the repo root and the CWD.  Returns an absolute path or ``None``.
    """
    roots = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(meipass)
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        roots += [here, os.path.dirname(here), os.getcwd()]
    for root in roots:
        candidate = os.path.join(root, name)
        if os.path.exists(candidate):
            return candidate
    return None


def open_with_default_app(path):
    """Open a file/URL with the OS default application, guarded."""
    try:
        if hasattr(os, "startfile"):
            os.startfile(path)                # noqa: S606 - intended
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


def _one_line(text, width=90):
    """Collapse an entry to a single truncated line for row previews."""
    flat = " ".join(text.split())
    if len(flat) > width:
        flat = flat[: width - 1] + "…"
    return flat or "(blank)"


# ---------------------------------------------------------------------------
# The app (built lazily; tkinter imported only inside build_app/main)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to a live tkinter import.

    Kept inside a function so this module imports cleanly without a display.
    """
    import tkinter as tk
    from tkinter import ttk

    from . import transforms
    from .store import Store
    from .monitor import poll_clipboard, set_clipboard, clipboard_available

    FONT = "Segoe UI"

    class ScrollList(ttk.Frame):
        """A vertically scrollable column of row widgets (Canvas + inner frame)."""

        def __init__(self, master, app, height=200):
            super().__init__(master, style="TFrame")
            self.app = app
            self.canvas = tk.Canvas(self, height=height, highlightthickness=0,
                                    borderwidth=0)
            self.sb = ttk.Scrollbar(self, orient="vertical",
                                    command=self.canvas.yview)
            self.canvas.configure(yscrollcommand=self.sb.set)
            self.sb.pack(side="right", fill="y")
            self.canvas.pack(side="left", fill="both", expand=True)
            self.inner = ttk.Frame(self.canvas, style="TFrame")
            self._win = self.canvas.create_window((0, 0), window=self.inner,
                                                  anchor="nw")
            self.inner.bind("<Configure>", lambda e: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")))
            self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
                self._win, width=e.width))
            self.canvas.bind("<Enter>", self._bind_wheel)
            self.canvas.bind("<Leave>", self._unbind_wheel)
            app.track(self.canvas, "canvas")

        def _bind_wheel(self, _e=None):
            self.canvas.bind_all("<MouseWheel>", self._on_wheel)
            self.canvas.bind_all("<Button-4>", self._on_wheel)
            self.canvas.bind_all("<Button-5>", self._on_wheel)

        def _unbind_wheel(self, _e=None):
            self.canvas.unbind_all("<MouseWheel>")
            self.canvas.unbind_all("<Button-4>")
            self.canvas.unbind_all("<Button-5>")

        def _on_wheel(self, event):
            if getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            else:
                delta = -1 if event.delta > 0 else 1
            self.canvas.yview_scroll(delta, "units")

        def clear_rows(self):
            for child in self.inner.winfo_children():
                child.destroy()

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(WINDOW_TITLE)
            self.geometry("760x680")
            self.minsize(560, 480)

            self.store = Store()
            self.theme = self.store.get_theme()
            self._tracked = []           # (tk_widget, role) for manual re-theme
            self._img_refs = []          # keep PhotoImage refs alive
            self._selected_id = None
            self._stop = threading.Event()
            self._poller = None
            self._clip_ok = True

            self.search_var = tk.StringVar()
            self.add_var = tk.StringVar()
            self.transform_var = tk.StringVar()

            self._set_icon()
            self._build_menu()
            self._build_layout()
            self._apply_theme()
            self.protocol("WM_DELETE_WINDOW", self._on_close)

            self.search_var.trace_add("write", lambda *_: self.refresh())
            self.refresh()
            self.after(120, self._start_poll)

        # ---- assets / icon
        def _set_icon(self):
            try:
                ico = asset_path("clipboard-manager.ico")
                if ico and os.name == "nt":
                    self.iconbitmap(ico)
                    return
            except Exception:
                pass
            try:
                png = asset_path("clipboard-manager.png")
                if png:
                    img = tk.PhotoImage(file=png)
                    self._img_refs.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass  # icon is cosmetic; never block launch

        # ---- theming
        def track(self, widget, role):
            self._tracked.append((widget, role))

        def _pal(self):
            return PALETTES[self.theme]

        def _apply_theme(self):
            p = self._pal()
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except Exception:
                pass
            self.configure(bg=p["bg"])
            style.configure(".", background=p["bg"], foreground=p["text"],
                            fieldbackground=p["entry"], bordercolor=p["border"],
                            font=(FONT, 10))
            style.configure("TFrame", background=p["bg"])
            style.configure("Surface.TFrame", background=p["surface"])
            style.configure("Row.TFrame", background=p["surface"])
            style.configure("RowSel.TFrame", background=p["rowsel"])
            style.configure("TLabel", background=p["bg"], foreground=p["text"])
            style.configure("Muted.TLabel", background=p["bg"],
                            foreground=p["muted"])
            style.configure("Header.TLabel", background=p["bg"],
                            foreground=p["text"], font=(FONT, 13, "bold"))
            style.configure("Brand.TLabel", background=p["surface"],
                            foreground=p["text"], font=(FONT, 12, "bold"))
            style.configure("Status.TLabel", background=p["surface"],
                            foreground=p["muted"])
            style.configure("Row.TLabel", background=p["surface"],
                            foreground=p["text"])
            style.configure("RowSel.TLabel", background=p["rowsel"],
                            foreground=p["text"])
            style.configure("RowPin.TLabel", background=p["surface"],
                            foreground=p["primary"])
            style.configure("TButton", background=p["surface"],
                            foreground=p["text"], bordercolor=p["border"],
                            focuscolor=p["surface"], padding=(8, 4))
            style.map("TButton",
                      background=[("active", p["trough"]),
                                  ("disabled", p["bg"])],
                      foreground=[("disabled", p["muted"])])
            style.configure("Accent.TButton", background=p["primary"],
                            foreground="#ffffff", padding=(10, 5))
            style.map("Accent.TButton",
                      background=[("active", p["primary_hi"]),
                                  ("disabled", p["border"])],
                      foreground=[("disabled", p["muted"])])
            style.configure("Row.TButton", background=p["surface"],
                            foreground=p["text"], padding=(6, 2))
            style.map("Row.TButton", background=[("active", p["trough"])])
            style.configure("Toggle.TButton", background=p["surface"],
                            foreground=p["text"], padding=(8, 4))
            style.configure("TEntry", fieldbackground=p["entry"],
                            foreground=p["text"], insertcolor=p["text"],
                            bordercolor=p["border"])
            style.configure("TCombobox", fieldbackground=p["entry"],
                            foreground=p["text"], background=p["surface"],
                            arrowcolor=p["text"])
            style.map("TCombobox", fieldbackground=[("readonly", p["entry"])],
                      foreground=[("readonly", p["text"])])
            style.configure("TLabelframe", background=p["bg"],
                            foreground=p["text"], bordercolor=p["border"])
            style.configure("TLabelframe.Label", background=p["bg"],
                            foreground=p["muted"])
            style.configure("TScrollbar", background=p["surface"],
                            troughcolor=p["bg"], bordercolor=p["border"],
                            arrowcolor=p["text"])
            style.configure("TSeparator", background=p["border"])

            for widget, role in list(self._tracked):
                try:
                    if role == "canvas":
                        widget.configure(bg=p["surface"], highlightthickness=1,
                                         highlightbackground=p["border"])
                except Exception:
                    pass

        def toggle_theme(self):
            self.theme = "dark" if self.theme == "light" else "light"
            self.store.set_theme(self.theme)
            self._apply_theme()
            self._theme_btn.configure(
                text="☀ Light mode" if self.theme == "dark" else "🌙 Dark mode")
            self.refresh()

        # ---- menu
        def _build_menu(self):
            bar = tk.Menu(self)
            filem = tk.Menu(bar, tearoff=0)
            filem.add_command(label="Clear history (keep pinned)",
                              command=self._clear_history)
            filem.add_command(label="Exit", command=self._on_close)
            bar.add_cascade(label="File", menu=filem)

            viewm = tk.Menu(bar, tearoff=0)
            viewm.add_command(label="Toggle dark mode", command=self.toggle_theme)
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="About", command=self._about)
            helpm.add_command(label="Open project page (quickopen.ai)",
                              command=lambda: open_with_default_app(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)

        # ---- layout
        def _build_layout(self):
            # top brand bar
            top = ttk.Frame(self, style="Surface.TFrame", padding=(12, 8))
            top.pack(fill="x", side="top")
            ttk.Label(top, text="Clipboard Manager",
                      style="Brand.TLabel").pack(side="left")
            ttk.Label(top, style="Status.TLabel",
                      text="  offline · open source · by QuickOpen").pack(
                side="left")
            self._theme_btn = ttk.Button(
                top, style="Toggle.TButton", command=self.toggle_theme,
                text="☀ Light mode" if self.theme == "dark" else "🌙 Dark mode")
            self._theme_btn.pack(side="right")
            self._pause_btn = ttk.Button(top, style="Toggle.TButton",
                                         command=self._toggle_pause,
                                         text="⏸ Pause capture")
            self._pause_btn.pack(side="right", padx=(0, 6))

            # search + manual add
            tools = ttk.Frame(self, style="TFrame", padding=(12, 8))
            tools.pack(fill="x")
            ttk.Label(tools, text="Search").pack(side="left")
            ttk.Entry(tools, textvariable=self.search_var).pack(
                side="left", fill="x", expand=True, padx=(6, 12))
            ttk.Label(tools, text="Add").pack(side="left")
            add_entry = ttk.Entry(tools, textvariable=self.add_var, width=24)
            add_entry.pack(side="left", padx=6)
            add_entry.bind("<Return>", lambda e: self._manual_add())
            ttk.Button(tools, text="Add", style="Accent.TButton",
                       command=self._manual_add).pack(side="left")

            body = ttk.Frame(self, style="TFrame", padding=(12, 0))
            body.pack(fill="both", expand=True)

            # Pinned section
            ttk.Label(body, text="Pinned", style="Header.TLabel").pack(
                anchor="w", pady=(4, 2))
            self.pinned_list = ScrollList(body, self, height=110)
            self.pinned_list.pack(fill="x")

            # History section
            ttk.Label(body, text="History (newest first)",
                      style="Header.TLabel").pack(anchor="w", pady=(10, 2))
            self.history_list = ScrollList(body, self, height=220)
            self.history_list.pack(fill="both", expand=True)

            # Transforms toolbar
            tf = ttk.Frame(self, style="TFrame", padding=(12, 8))
            tf.pack(fill="x")
            ttk.Label(tf, text="Transform selected").pack(side="left")
            self._tf_labels = [lbl for lbl, _ in transforms.TRANSFORMS.values()]
            self._tf_by_label = {lbl: name for name, (lbl, _)
                                 in transforms.TRANSFORMS.items()}
            self.transform_var.set(self._tf_labels[0])
            ttk.Combobox(tf, textvariable=self.transform_var, state="readonly",
                         width=26, values=self._tf_labels).pack(
                side="left", padx=6)
            ttk.Button(tf, text="Apply → copy", style="Accent.TButton",
                       command=self._apply_transform).pack(side="left")
            ttk.Button(tf, text="Clear (keep pinned)",
                       command=self._clear_history).pack(side="right")

            # status bar
            bar = ttk.Frame(self, style="Surface.TFrame", padding=(12, 6))
            bar.pack(fill="x", side="bottom")
            self.status_lbl = ttk.Label(bar, text="Ready", style="Status.TLabel",
                                        anchor="w")
            self.status_lbl.pack(side="left", fill="x", expand=True)

        # ---- status helpers
        def _set_status(self, text, kind="idle"):
            p = self._pal()
            color = {"ok": p["ok"], "err": p["err"], "work": p["primary"]}.get(
                kind, p["muted"])
            self.status_lbl.configure(text=text, foreground=color)

        # ---- rows
        def _add_row(self, parent, item):
            selected = (item["id"] == self._selected_id)
            fstyle = "RowSel.TFrame" if selected else "Row.TFrame"
            lstyle = "RowSel.TLabel" if selected else "Row.TLabel"
            row = ttk.Frame(parent, style=fstyle, padding=(6, 3))
            row.pack(fill="x", pady=1)

            if item["pinned"]:
                ttk.Label(row, text="★", style="RowPin.TLabel").pack(side="left")
            lbl = ttk.Label(row, text=_one_line(item["text"]), style=lstyle,
                            anchor="w")
            lbl.pack(side="left", fill="x", expand=True, padx=(4, 6))
            iid = item["id"]
            lbl.bind("<Button-1>", lambda e, i=iid: self._select(i))
            row.bind("<Button-1>", lambda e, i=iid: self._select(i))

            ttk.Button(row, text="✕", style="Row.TButton", width=2,
                       command=lambda i=iid: self._delete(i)).pack(side="right")
            pin_text = "Unpin" if item["pinned"] else "Pin"
            ttk.Button(row, text=pin_text, style="Row.TButton",
                       command=lambda i=iid, p=item["pinned"]:
                       self._toggle_pin(i, p)).pack(side="right", padx=2)
            ttk.Button(row, text="Copy", style="Row.TButton",
                       command=lambda t=item["text"]: self._copy(t)).pack(
                side="right", padx=2)

        def refresh(self):
            query = self.search_var.get().strip()
            history = self.store.search(query) if query else self.store.items()
            pinned = [it for it in self.store.pinned()
                      if not query or query.lower() in it["text"].lower()]

            self.pinned_list.clear_rows()
            if not pinned:
                ttk.Label(self.pinned_list.inner, style="Muted.TLabel",
                          text="No pinned snippets yet — pin an entry to keep it "
                               "through Clear.").pack(anchor="w", padx=6, pady=4)
            else:
                for it in pinned:
                    self._add_row(self.pinned_list.inner, it)

            self.history_list.clear_rows()
            if not history:
                msg = ("No matches." if query
                       else "History is empty — copy something, or use Add.")
                ttk.Label(self.history_list.inner, style="Muted.TLabel",
                          text=msg).pack(anchor="w", padx=6, pady=4)
            else:
                for it in history:
                    self._add_row(self.history_list.inner, it)

        # ---- actions
        def _select(self, item_id):
            self._selected_id = item_id
            self.refresh()

        def _selected_item(self):
            if self._selected_id is None:
                return None
            return self.store.get(self._selected_id)

        def _copy(self, text):
            if set_clipboard(text):
                self._set_status("Copied to clipboard.", kind="ok")
            else:
                self._set_status("Clipboard unavailable — could not copy.",
                                 kind="err")

        def _toggle_pin(self, item_id, currently_pinned):
            if currently_pinned:
                self.store.unpin(item_id)
                self._set_status("Unpinned.", kind="ok")
            else:
                self.store.pin(item_id)
                self._set_status("Pinned.", kind="ok")
            self.refresh()

        def _delete(self, item_id):
            self.store.delete(item_id)
            if self._selected_id == item_id:
                self._selected_id = None
            self._set_status("Deleted entry.", kind="ok")
            self.refresh()

        def _manual_add(self):
            text = self.add_var.get()
            if not text.strip():
                self._set_status("Nothing to add.", kind="err")
                return
            new_id = self.store.add(text, force=True)
            self.add_var.set("")
            if new_id is None:
                self._set_status("Not added (duplicate of newest).", kind="err")
            else:
                self._set_status("Added entry.", kind="ok")
            self.refresh()

        def _apply_transform(self):
            item = self._selected_item()
            if item is None:
                self._set_status("Select an entry first (click a row).",
                                 kind="err")
                return
            name = self._tf_by_label.get(self.transform_var.get())
            try:
                result = transforms.apply(name, item["text"])
            except Exception as exc:
                self._set_status(str(exc), kind="err")
                return
            if set_clipboard(result):
                self._set_status(
                    f"Applied “{self.transform_var.get()}” and copied the result.",
                    kind="ok")
            else:
                self._set_status(
                    f"Applied “{self.transform_var.get()}” — clipboard "
                    "unavailable, not copied.", kind="err")

        def _clear_history(self):
            removed = self.store.clear(keep_pinned=True)
            self._selected_id = None
            self._set_status(f"Cleared {removed} entr"
                             f"{'y' if removed == 1 else 'ies'} (pinned kept).",
                             kind="ok")
            self.refresh()

        def _toggle_pause(self):
            if self.store.is_paused():
                self.store.resume()
                self._pause_btn.configure(text="⏸ Pause capture")
                self._set_status("Capture resumed.", kind="ok")
            else:
                self.store.pause()
                self._pause_btn.configure(text="▶ Resume capture")
                self._set_status("Capture paused — copies are not recorded.",
                                 kind="work")

        # ---- background clipboard poll
        def _start_poll(self):
            if not clipboard_available():
                self._clip_ok = False
                self._set_status(
                    "No clipboard backend detected — browse, pin and Add still "
                    "work; auto-capture and Copy are disabled.", kind="err")
                return

            def on_new(text):
                # marshal onto the Tk thread
                self.after(0, lambda: self._captured(text))

            def on_unavailable():
                self.after(0, lambda: self._set_status(
                    "Clipboard backend went away — auto-capture stopped.",
                    kind="err"))

            self._poller = threading.Thread(
                target=poll_clipboard,
                kwargs=dict(on_new=on_new, interval=POLL_INTERVAL,
                            stop_event=self._stop, on_unavailable=on_unavailable),
                daemon=True)
            self._poller.start()

        def _captured(self, text):
            new_id = self.store.add(text)  # respects the pause flag
            if new_id is not None:
                self.refresh()
                self._set_status("Captured a new copy.", kind="ok")

        # ---- About
        def _about(self):
            win = tk.Toplevel(self)
            win.title("About Clipboard Manager")
            win.configure(bg=self._pal()["bg"])
            win.resizable(False, False)
            frm = ttk.Frame(win, style="TFrame", padding=18)
            frm.pack(fill="both", expand=True)
            ttk.Label(frm, text="Clipboard Manager",
                      style="Header.TLabel").pack(anchor="w")
            ttk.Label(frm, text=f"Version {APP_VERSION}",
                      style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
            ttk.Label(frm, style="TLabel", justify="left", wraplength=360,
                      text="A fast, fully-offline clipboard history manager — "
                           "search past copies, pin snippets, re-copy any entry "
                           "and apply quick text transforms.\n\n"
                           "100% AI-built, open source, published on QuickOpen.\n"
                           "Nothing is ever uploaded anywhere.").pack(anchor="w")
            ttk.Label(frm, style="Muted.TLabel", justify="left", wraplength=360,
                      text="Licensed under Apache-2.0. Built on the standard "
                           "library plus pyperclip.").pack(anchor="w",
                                                            pady=(8, 4))
            link = ttk.Label(frm, text="Project page: quickopen.ai",
                             style="Row.TLabel", cursor="hand2",
                             foreground=self._pal()["primary"])
            link.pack(anchor="w", pady=(4, 10))
            link.bind("<Button-1>", lambda e: open_with_default_app(PROJECT_URL))
            ttk.Button(frm, text="Close", command=win.destroy).pack(anchor="e")
            win.transient(self)
            win.grab_set()

        # ---- shutdown
        def _on_close(self):
            self._stop.set()
            self.destroy()

    return App


def main():
    """Entry point: build the root window and run. Degrades on headless hosts.

    Importing this module does nothing; only this function creates a Tk root.
    With no display it prints a friendly note and returns 0 instead of raising,
    and a missing clipboard backend is handled inside the running app.
    """
    try:
        import tkinter as tk
    except Exception as exc:  # tkinter missing entirely
        print(f"{APP_NAME}: a graphical environment with tkinter is required "
              f"to run the GUI ({exc}).")
        return 0

    try:
        App = build_app()
        app = App()
    except tk.TclError as exc:
        print(f"{APP_NAME}: no graphical display available — cannot start the "
              f"GUI here ({exc}). This app is intended for the Windows desktop.")
        return 0
    except Exception as exc:
        print(f"{APP_NAME}: could not start the GUI ({exc}).")
        return 1

    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
