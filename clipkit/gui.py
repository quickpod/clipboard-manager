#!/usr/bin/env python3
r"""Clipboard Manager -- an Aura (QuickOpen design system) GUI on the clipkit store.

A single Aura window with a sidebar of sections:

  * **History** -- a search box that filters live, a manual Add box, a
    **Pinned** card (snippets that survive Clear) and a **History** card
    (newest first); each row has a one-line preview and per-row Pin/Unpin,
    Copy and Delete buttons; click a row to select it.
  * **Transforms** -- apply a text transform to the selected entry and copy
    the result to the clipboard.
  * **About** -- version / licence info.

Pause/Resume-capture lives in the header, Clear (keeps pinned) in the status
bar, and the dark-mode toggle is the Aura sidebar switch (the choice is
persisted in the store).

A background thread runs :func:`clipkit.monitor.poll_clipboard` to capture new
copies into the store; results are marshalled back onto the Tk thread with
``after``.  Everything degrades gracefully: with no display (or without
customtkinter installed) the app prints a friendly note and returns 0, and
with no clipboard backend it still browses, pins and manually adds -- it just
cannot auto-capture or copy.

Design goals baked in here (mirrors the QuickOpen house style):
  * built on the vendored ``clipkit/aura.py`` design system, which layers the
    quickopen.ai look (deep space + light) over CustomTkinter.  Runtime deps:
    ``customtkinter`` (+ ``darkdetect``) — declared in requirements.txt; the
    PyInstaller build adds ``--collect-all customtkinter``.
  * importing this module does nothing -- only :func:`main` builds a root window.
  * frozen-exe safe assets via ``sys._MEIPASS`` / the exe dir, never ``__file__``.
  * background work stays off the Tk thread and errors surface in the Aura
    status bar, never as a traceback.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys
import threading

# NOTE: tkinter/customtkinter are imported lazily inside build_app()/main() so
# that merely importing this module (packaging, headless CI) never fails.

APP_NAME = "Clipboard Manager"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "Clipboard Manager — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai"
POLL_INTERVAL = 0.7
ACCENT = "#b0700a"      # publish/specs/clipboard-manager.json "accent": [176, 112, 10]


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
# The app (built lazily; tkinter/customtkinter imported only inside build_app)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to live GUI imports.

    Kept inside a function so this module imports cleanly without a display
    (and without customtkinter installed).
    """
    import tkinter as tk
    from tkinter import ttk
    import customtkinter as ctk

    from . import aura, transforms
    from .store import Store
    from .monitor import poll_clipboard, set_clipboard, clipboard_available

    class ScrollList(ttk.Frame):
        """A vertically scrollable column of row widgets (Canvas + inner frame)."""

        def __init__(self, master, height=200):
            super().__init__(master, style="Surface.TFrame")
            self.canvas = tk.Canvas(self, height=height, highlightthickness=0,
                                    borderwidth=0)
            self.sb = ttk.Scrollbar(self, orient="vertical",
                                    command=self.canvas.yview)
            self.canvas.configure(yscrollcommand=self.sb.set)
            self.sb.pack(side="right", fill="y")
            self.canvas.pack(side="left", fill="both", expand=True)
            self.inner = ttk.Frame(self.canvas, style="Surface.TFrame")
            self._win = self.canvas.create_window((0, 0), window=self.inner,
                                                  anchor="nw")
            self.inner.bind("<Configure>", lambda e: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")))
            self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
                self._win, width=e.width))
            self.canvas.bind("<Enter>", self._bind_wheel)
            self.canvas.bind("<Leave>", self._unbind_wheel)
            aura.track(self.canvas, "canvas")

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

    class App(aura.AuraApp):
        def __init__(self):
            store = Store()          # pure python; safe before Tk exists
            super().__init__(
                title=WINDOW_TITLE, app_name=APP_NAME, accent=ACCENT,
                theme=store.get_theme(),
                icon_png=asset_path("clipboard-manager.png"),
                version=APP_VERSION,
                tagline="offline clipboard history",
                on_theme_change=store.set_theme,
                size=(1080, 680), min_size=(880, 560))

            self.store = store
            self._selected_id = None
            self._stop = threading.Event()
            self._poller = None
            self._clip_ok = True
            self._img_refs_gui = []

            self._tf_labels = [lbl for lbl, _ in transforms.TRANSFORMS.values()]
            self._tf_by_label = {lbl: name for name, (lbl, _)
                                 in transforms.TRANSFORMS.items()}

            self._set_icon()
            self._build_menu()
            self._style_rows()
            self.add_section("history", "History", "↻", self._build_history)
            self.add_section("transforms", "Transforms", "⇄",
                             self._build_transforms)
            self.add_section("about", "About", "ℹ", self._build_about)
            self.show("history")

            # global actions: pause/resume in the header, Clear in the status bar
            self._pause_btn = aura.AuraButton(
                self.header_actions, "Pause capture", kind="secondary",
                height=30, command=self._toggle_pause)
            self._pause_btn.pack(side="left")
            aura.AuraButton(self.statusbar.actions, "Clear (keep pinned)",
                            kind="secondary", height=30,
                            command=self._clear_history).pack(side="left")

            self.set_status("Ready")
            self.protocol("WM_DELETE_WINDOW", self._on_close)
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
                    self._img_refs_gui.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass  # icon is cosmetic; never block launch

        # ---- row styles (ttk rows on the list canvases; colors from aura)
        def _style_rows(self):
            p = aura.P()
            sel = p["accent_soft"]
            style = ttk.Style(self)
            style.configure("Row.TFrame", background=p["surface"])
            style.configure("RowSel.TFrame", background=sel)
            style.configure("Row.TLabel", background=p["surface"],
                            foreground=p["text"])
            style.configure("RowSel.TLabel", background=sel,
                            foreground=p["text"])
            style.configure("RowPin.TLabel", background=p["surface"],
                            foreground=p["accent"])
            style.configure("RowSelPin.TLabel", background=sel,
                            foreground=p["accent"])
            style.configure("RowMuted.TLabel", background=p["surface"],
                            foreground=p["muted"])
            style.configure("Row.TButton", background=p["surface"],
                            foreground=p["text"], bordercolor=p["border"],
                            focuscolor=p["accent"], padding=(6, 2))
            style.map("Row.TButton",
                      background=[("pressed", p["surface3"]),
                                  ("active", p["surface2"])])

        def set_theme(self, theme):
            """Aura flips ctk + ttk; re-apply our row styles and re-render."""
            super().set_theme(theme)
            self._style_rows()
            self.refresh()

        # ---- menu (native menus stay; theme lives in the sidebar toggle too)
        def _build_menu(self):
            bar = tk.Menu(self)
            filem = tk.Menu(bar, tearoff=0)
            filem.add_command(label="Clear history (keep pinned)",
                              command=self._clear_history)
            filem.add_command(label="Exit", command=self._on_close)
            bar.add_cascade(label="File", menu=filem)

            viewm = tk.Menu(bar, tearoff=0)
            viewm.add_command(
                label="Toggle dark mode",
                command=lambda: self.set_theme(
                    "light" if self.theme == "dark" else "dark"))
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="About",
                              command=lambda: self.show("about"))
            helpm.add_command(label="Open project page (quickopen.ai)",
                              command=lambda: open_with_default_app(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)

        # =================================================================
        # History section (search + add, Pinned card, History card)
        # =================================================================
        def _build_history(self, frame):
            bar = ctk.CTkFrame(frame, fg_color="transparent")
            bar.pack(fill="x", pady=(0, 12))
            # no textvariable: CTkEntry placeholders only work without one
            self.search_entry = aura.AuraEntry(
                bar, placeholder="Search history and pinned…")
            self.search_entry.pack(side="left", fill="x", expand=True)
            self.search_entry.bind("<KeyRelease>", lambda _e: self.refresh())
            self.add_entry = aura.AuraEntry(bar, placeholder="Add a snippet…",
                                            width=230)
            self.add_entry.pack(side="left", padx=(10, 8))
            self.add_entry.bind("<Return>", lambda _e: self._manual_add())
            aura.AuraButton(bar, "Add", width=70,
                            command=self._manual_add).pack(side="left")

            pinned = aura.Card(frame, title="Pinned — survives Clear")
            pinned.pack(fill="x", pady=(0, 14))
            self.pinned_list = ScrollList(pinned.body, height=104)
            self.pinned_list.pack(fill="x")

            hist = aura.Card(frame, title="History — newest first")
            hist.pack(fill="both", expand=True)
            self.history_list = ScrollList(hist.body, height=220)
            self.history_list.pack(fill="both", expand=True)

            self.refresh()

        # ---- rows
        def _add_row(self, parent, item):
            selected = (item["id"] == self._selected_id)
            fstyle = "RowSel.TFrame" if selected else "Row.TFrame"
            lstyle = "RowSel.TLabel" if selected else "Row.TLabel"
            row = ttk.Frame(parent, style=fstyle, padding=(6, 3))
            row.pack(fill="x", pady=1)

            if item["pinned"]:
                pstyle = "RowSelPin.TLabel" if selected else "RowPin.TLabel"
                ttk.Label(row, text="★", style=pstyle).pack(side="left")
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
            if not hasattr(self, "history_list"):
                return  # History section not built yet
            query = self.search_entry.get().strip()
            history = self.store.search(query) if query else self.store.items()
            pinned = [it for it in self.store.pinned()
                      if not query or query.lower() in it["text"].lower()]

            self.pinned_list.clear_rows()
            if not pinned:
                ttk.Label(self.pinned_list.inner, style="RowMuted.TLabel",
                          text="No pinned snippets yet — pin an entry to keep it "
                               "through Clear.").pack(anchor="w", padx=6, pady=4)
            else:
                for it in pinned:
                    self._add_row(self.pinned_list.inner, it)

            self.history_list.clear_rows()
            if not history:
                msg = ("No matches." if query
                       else "History is empty — copy something, or use Add.")
                ttk.Label(self.history_list.inner, style="RowMuted.TLabel",
                          text=msg).pack(anchor="w", padx=6, pady=4)
            else:
                for it in history:
                    self._add_row(self.history_list.inner, it)

            self._update_sel_preview()

        # =================================================================
        # Transforms section
        # =================================================================
        def _build_transforms(self, frame):
            card = aura.Card(frame, title="Transform the selected entry")
            card.pack(fill="x")
            self._sel_lbl = ctk.CTkLabel(card.body, font=aura.font(),
                                         justify="left", anchor="w",
                                         wraplength=640, text="")
            self._sel_lbl.pack(fill="x", pady=(0, 10))
            row = ctk.CTkFrame(card.body, fg_color="transparent")
            row.pack(fill="x")
            self.transform_var = tk.StringVar(value=self._tf_labels[0])
            aura.AuraCombo(row, variable=self.transform_var,
                           values=self._tf_labels, state="readonly",
                           width=250).pack(side="left", padx=(0, 10))
            aura.AuraButton(row, "Apply → copy",
                            command=self._apply_transform).pack(side="left")
            aura.Caption(card.body,
                         "Pick an entry in “History” first (click a row); the "
                         "transformed text is copied to the clipboard.").pack(
                anchor="w", pady=(12, 0))
            self._update_sel_preview()

        def _update_sel_preview(self):
            if not hasattr(self, "_sel_lbl"):
                return  # Transforms section not built yet
            item = self._selected_item()
            self._sel_lbl.configure(
                text=("Selected:  " + _one_line(item["text"])) if item
                else "No entry selected.")

        # =================================================================
        # About section
        # =================================================================
        def _build_about(self, frame):
            card = aura.Card(frame, title="About Clipboard Manager")
            card.pack(fill="x")
            aura.Heading(card.body, APP_NAME).pack(anchor="w")
            aura.Caption(card.body, f"Version {APP_VERSION}").pack(
                anchor="w", pady=(0, 10))
            ctk.CTkLabel(
                card.body, font=aura.font(), justify="left", anchor="w",
                wraplength=520,
                text="A fast, fully-offline clipboard history manager — "
                     "search past copies, pin snippets, re-copy any entry "
                     "and apply quick text transforms.\n\n"
                     "100% AI-built, open source, published on QuickOpen. "
                     "Nothing is ever uploaded anywhere.").pack(anchor="w")
            aura.Caption(card.body,
                         "Licensed under Apache-2.0. Built on the standard "
                         "library plus pyperclip and CustomTkinter (MIT).").pack(
                anchor="w", pady=(10, 4))
            aura.AuraButton(card.body, "Project page: quickopen.ai",
                            kind="ghost",
                            command=lambda: open_with_default_app(
                                PROJECT_URL)).pack(anchor="w", pady=(6, 0))

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
                self.set_status("Copied to clipboard.", kind="ok")
            else:
                self.set_status("Clipboard unavailable — could not copy.",
                                kind="err")

        def _toggle_pin(self, item_id, currently_pinned):
            if currently_pinned:
                self.store.unpin(item_id)
                self.set_status("Unpinned.", kind="ok")
            else:
                self.store.pin(item_id)
                self.set_status("Pinned.", kind="ok")
            self.refresh()

        def _delete(self, item_id):
            self.store.delete(item_id)
            if self._selected_id == item_id:
                self._selected_id = None
            self.set_status("Deleted entry.", kind="ok")
            self.refresh()

        def _manual_add(self):
            text = self.add_entry.get()
            if not text.strip():
                self.set_status("Nothing to add.", kind="err")
                return
            new_id = self.store.add(text, force=True)
            self.add_entry.delete(0, "end")
            if new_id is None:
                self.set_status("Not added (duplicate of newest).", kind="err")
            else:
                self.set_status("Added entry.", kind="ok")
            self.refresh()

        def _apply_transform(self):
            item = self._selected_item()
            if item is None:
                self.set_error("Select an entry first (click a row in "
                               "History).")
                return
            label = self.transform_var.get()
            name = self._tf_by_label.get(label)
            try:
                result = transforms.apply(name, item["text"])
            except Exception as exc:
                self.set_error(str(exc))
                return
            if set_clipboard(result):
                self.set_success(
                    f"Applied “{label}” and copied the result.")
            else:
                self.set_error(
                    f"Applied “{label}” — clipboard unavailable, not copied.")

        def _clear_history(self):
            removed = self.store.clear(keep_pinned=True)
            self._selected_id = None
            self.set_status(f"Cleared {removed} entr"
                            f"{'y' if removed == 1 else 'ies'} (pinned kept).",
                            kind="ok")
            self.refresh()

        def _toggle_pause(self):
            if self.store.is_paused():
                self.store.resume()
                self._pause_btn.configure(text="Pause capture")
                self.set_status("Capture resumed.", kind="ok")
            else:
                self.store.pause()
                self._pause_btn.configure(text="Resume capture")
                self.set_status("Capture paused — copies are not recorded.",
                                kind="working")

        # ---- background clipboard poll
        def _start_poll(self):
            if not clipboard_available():
                self._clip_ok = False
                self.set_status(
                    "No clipboard backend detected — browse, pin and Add still "
                    "work; auto-capture and Copy are disabled.", kind="err")
                return

            def on_new(text):
                # marshal onto the Tk thread
                self.after(0, lambda: self._captured(text))

            def on_unavailable():
                self.after(0, lambda: self.set_status(
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
                self.set_status("Captured a new copy.", kind="ok")

        # ---- shutdown
        def _on_close(self):
            self._stop.set()
            self.destroy()

    return App


def main():
    """Entry point: build the root window and run. Degrades on headless hosts.

    Importing this module does nothing; only this function creates a Tk root.
    With no display (or without customtkinter installed) it prints a friendly
    note and returns 0 instead of raising, and a missing clipboard backend is
    handled inside the running app.
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
    except ImportError as exc:
        print(f"{APP_NAME}: the GUI needs the 'customtkinter' package "
              f"({exc}). Install it with:  pip install customtkinter")
        return 0
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
