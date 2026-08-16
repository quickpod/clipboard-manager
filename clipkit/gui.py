#!/usr/bin/env python3
r"""Clipboard Manager -- an Aura (QuickOpen design system) GUI on the clipkit store.

Layout per branding/aura-design-system/APP-LAYOUT-LANGUAGE.md, benchmarked
against **Ditto** (and the Windows Win+V clipboard history), adopting their
daily-use layout and refusing the pro tail (no sync, no networks, no macros):

  * **Sidebar** (AuraApp) -- History / About nav, plus a *Filters* library in
    ``sidebar_body``: All clips, Pinned, Links, Multi-line and Today, each
    with a live count.  Collapsible with Ctrl+\.
  * **Toolbar** -- "+ Add snippet" (primary), with the debounced search on
    the right (Ctrl+F).
  * **Content** -- the Ditto shape: a **list-detail splitter**.  Left: the
    clip list (preview + copied-ago, pinned rows starred and accented,
    newest first).  Right: the full clip, its stats, one-click Copy /
    Pin / Delete, and the text transforms (apply -> the result is copied
    AND added as the newest clip).  **Double-click any row to copy it** --
    the Ditto signature.  Empty history shows an Aura illustration.
  * **Status bar** -- clip counts; Clear (keeps pinned) lives here; errors
    surface here, never as raw dialogs.

Capture Pause/Resume stays in the header (window-global).  A background
thread runs :func:`clipkit.monitor.poll_clipboard` to capture new copies
into the store; results are marshalled back onto the Tk thread with
``after``.  Everything degrades gracefully: with no display (or without
customtkinter installed) the app prints a friendly note and returns 0, and
with no clipboard backend it still browses, pins and adds -- it just cannot
auto-capture or copy.

Design goals baked in here (mirrors the QuickOpen house style):
  * built on the vendored ``clipkit/aura.py`` design system (CustomTkinter).
  * importing this module does nothing -- only :func:`main` builds a root
    window.
  * frozen-exe safe assets via ``sys._MEIPASS`` / the exe dir, never
    ``__file__``.
  * background work stays off the Tk thread; failures show in the Aura
    status bar, never a raw traceback.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys
import threading
import time

# NOTE: tkinter/customtkinter are imported lazily inside build_app()/main() so
# that merely importing this module (packaging, headless CI) never fails.

APP_NAME = "Clipboard Manager"
APP_VERSION = "1.1.0"
WINDOW_TITLE = "Clipboard Manager — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai"
POLL_INTERVAL = 0.7
ACCENT = "#5b86f7"      # Aura brand accent

FILTERS = (("all", "All clips"), ("pinned", "Pinned"), ("links", "Links"),
           ("multiline", "Multi-line"), ("today", "Today"))


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


def _one_line(text, width=64):
    """Collapse an entry to a single truncated line for row previews."""
    flat = " ".join(text.split())
    if len(flat) > width:
        flat = flat[: width - 1] + "…"
    return flat or "(blank)"


def rel_date(ts, now=None):
    """A compact human 'copied' stamp: 'now', '5m', '2h', 'Yesterday', '12 Aug'."""
    if not ts:
        return ""
    now = now if now is not None else time.time()
    diff = max(0, now - ts)
    if diff < 90:
        return "now"
    if diff < 3600:
        return "%dm" % (diff // 60)
    if diff < 86400 and time.localtime(ts).tm_mday == time.localtime(now).tm_mday:
        return "%dh" % (diff // 3600)
    if diff < 2 * 86400:
        return "Yesterday"
    st, sn = time.localtime(ts), time.localtime(now)
    if st.tm_year == sn.tm_year:
        return time.strftime("%d %b", st)
    return time.strftime("%b %Y", st)


def is_link(text):
    """True for a single-line http(s)/ftp URL — the Links filter."""
    t = (text or "").strip()
    return ("\n" not in t and " " not in t
            and t.lower().startswith(("http://", "https://", "ftp://")))


def is_today(ts, now=None):
    lt, ln = time.localtime(ts), time.localtime(now or time.time())
    return (lt.tm_year, lt.tm_yday) == (ln.tm_year, ln.tm_yday)


def matches_filter(item, mode, now=None):
    """Does a store item pass the named sidebar filter?"""
    if mode == "pinned":
        return item["pinned"]
    if mode == "links":
        return is_link(item["text"])
    if mode == "multiline":
        return "\n" in item["text"].strip()
    if mode == "today":
        return is_today(item["ts"], now)
    return True


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
    from .store import Store, CAP_CHOICES
    from .monitor import poll_clipboard, set_clipboard, clipboard_available

    UI_FAMILY = "Segoe UI" if os.name == "nt" else "DejaVu Sans"
    MONO_FAMILY = "Consolas" if os.name == "nt" else "DejaVu Sans Mono"

    pair = aura._pair

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
                size=(1180, 720), min_size=(920, 560))

            self.store = store
            self._selected_id = None
            self._filter = "all"
            self._filter_rows = {}
            self._stop = threading.Event()
            self._poller = None
            self._clip_ok = True
            self._img_refs_gui = []

            self._tf_labels = [lbl for lbl, _ in transforms.TRANSFORMS.values()]
            self._tf_by_label = {lbl: name for name, (lbl, _)
                                 in transforms.TRANSFORMS.items()}

            self._set_icon()
            self._build_menu()
            self.add_section("history", "History", "⧉", self._build_history)
            self.add_section("about", "About", "ℹ", self._build_about)
            self._build_filter_sidebar()
            self.show("history")

            # window-global: capture pause/resume chip in the header
            self._pause_btn = aura.AuraButton(
                self.header_actions, "Pause capture", kind="secondary",
                height=30, command=self._toggle_pause)
            self._pause_btn.pack(side="left")
            aura.AuraButton(self.statusbar.actions, "Clear (keep pinned)",
                            kind="secondary", height=30,
                            command=self._clear_history).pack(side="left")

            self.protocol("WM_DELETE_WINDOW", self._on_close)
            self.after(120, self._start_poll)
            self.refresh()

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

        # ---- menu + keyboard baseline (§7/§9)
        def _build_menu(self):
            bar = tk.Menu(self)
            filem = tk.Menu(bar, tearoff=0)
            filem.add_command(label="Add snippet…", accelerator="Ctrl+N",
                              command=self._add_snippet_dialog)
            filem.add_command(label="Clear history (keep pinned)",
                              command=self._clear_history)
            filem.add_separator()
            filem.add_command(label="Settings…", accelerator="Ctrl+,",
                              command=self._open_settings)
            filem.add_command(label="Exit", command=self._on_close)
            bar.add_cascade(label="File", menu=filem)

            editm = tk.Menu(bar, tearoff=0)
            editm.add_command(label="Copy selected clip", accelerator="Enter",
                              command=self._copy_selected)
            editm.add_command(label="Pin / unpin selected",
                              command=self._toggle_pin_selected)
            editm.add_command(label="Delete selected", accelerator="Delete",
                              command=self._delete_selected)
            bar.add_cascade(label="Edit", menu=editm)

            viewm = tk.Menu(bar, tearoff=0)
            viewm.add_command(label="Toggle sidebar", accelerator="Ctrl+\\",
                              command=self.toggle_sidebar)
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

            self.bind_all("<Control-n>",
                          lambda e: (self._add_snippet_dialog(), "break")[1])
            self.bind_all("<Control-f>",
                          lambda e: (self._focus_search(), "break")[1])
            self.bind_all("<Control-comma>",
                          lambda e: (self._open_settings(), "break")[1])

        # =================================================================
        # Sidebar filter library (sidebar_body)
        # =================================================================
        def _build_filter_sidebar(self):
            aura.SectionLabel(self.sidebar_body, "Filters").pack(
                anchor="w", padx=6, pady=(0, 4))
            self._filter_frame = ctk.CTkFrame(self.sidebar_body,
                                              fg_color="transparent")
            self._filter_frame.pack(fill="x")
            self._refresh_filter_sidebar()

        def _refresh_filter_sidebar(self):
            for w in list(self._filter_frame.winfo_children()):
                try:
                    w.destroy()
                except Exception:
                    pass
            self._filter_rows.clear()
            items = self.store.items()
            now = time.time()
            for mode, label in FILTERS:
                n = sum(1 for it in items if matches_filter(it, mode, now))
                active = (mode == self._filter)
                btn = ctk.CTkButton(
                    self._filter_frame, text=f"{label}   ·  {n}",
                    anchor="w", height=30,
                    corner_radius=aura.TOKENS["geometry"]["radius_button"],
                    fg_color=pair("accent_soft") if active else "transparent",
                    hover_color=(aura._pal["light"]["surface2"],
                                 aura._pal["dark"]["surface2"]),
                    text_color=pair("text") if active else pair("muted"),
                    font=aura.font(role="body"),
                    command=lambda m=mode: self._set_filter(m))
                btn.pack(fill="x", pady=1)
                self._filter_rows[mode] = btn

        def _set_filter(self, mode):
            self._filter = mode
            self.refresh()

        # =================================================================
        # History section — toolbar + list-detail splitter
        # =================================================================
        def _build_history(self, frame):
            frame.grid_columnconfigure(0, weight=1)
            frame.grid_rowconfigure(1, weight=1)

            tb = aura.Toolbar(frame)
            tb.grid(row=0, column=0, sticky="ew", pady=(0, 10))
            tb.add_button("＋ Add snippet", self._add_snippet_dialog,
                          kind="primary")
            self.search = tb.add_search("Search clips…  (Ctrl+F)",
                                        on_change=lambda _t: self.refresh(),
                                        width=250)

            self.panes = ttk.Panedwindow(frame, orient="horizontal")
            self.panes.grid(row=1, column=0, sticky="nsew")

            # ---- left: the clip list
            listwrap = ctk.CTkFrame(self.panes, fg_color=pair("surface"),
                                    corner_radius=10, border_width=1,
                                    border_color=pair("border"))
            self.panes.add(listwrap, weight=2)
            self.tree = ttk.Treeview(listwrap, columns=("clip", "when"),
                                     show="headings", selectmode="browse")
            self.tree.heading("clip", text="Clip")
            self.tree.heading("when", text="Copied")
            self.tree.column("clip", width=330, stretch=True)
            self.tree.column("when", width=84, minwidth=70, stretch=False,
                             anchor="e")
            tsb = aura.AuraScrollbar(listwrap, command=self.tree.yview)
            self.tree.configure(yscrollcommand=tsb.set)
            tsb.pack(side="right", fill="y", padx=(0, 4), pady=6)
            self.tree.pack(side="left", fill="both", expand=True,
                           padx=(6, 0), pady=6)
            self.tree.bind("<<TreeviewSelect>>", self._on_select)
            self.tree.bind("<Double-Button-1>",
                           lambda e: self._copy_selected())
            self.tree.bind("<Return>", lambda e: self._copy_selected())
            self.tree.bind("<Delete>", lambda e: self._delete_selected())
            self.tree.bind("<Button-3>", self._show_row_menu)
            self._row_menu = tk.Menu(self, tearoff=0)
            aura.track(self._row_menu, "menu")

            # ---- right: the detail pane
            right = ctk.CTkFrame(self.panes, fg_color=pair("bg"),
                                 corner_radius=0)
            self.panes.add(right, weight=3)
            right.grid_columnconfigure(0, weight=1)
            right.grid_rowconfigure(2, weight=1)

            meta = ctk.CTkFrame(right, fg_color="transparent")
            meta.grid(row=0, column=0, sticky="ew", padx=(10, 0),
                      pady=(0, 6))
            self.detail_head = aura.Heading(meta, "")
            self.detail_head.pack(side="left")
            self.detail_meta = aura.Caption(meta, "")
            self.detail_meta.pack(side="left", padx=(10, 0))

            act = ctk.CTkFrame(right, fg_color="transparent")
            act.grid(row=1, column=0, sticky="ew", padx=(10, 0),
                     pady=(0, 8))
            self._copy_btn = aura.AuraButton(act, "Copy", kind="primary",
                                             height=30, width=84,
                                             command=self._copy_selected)
            self._copy_btn.pack(side="left")
            self._pin_btn = aura.AuraButton(act, "Pin", kind="secondary",
                                            height=30, width=84,
                                            command=self._toggle_pin_selected)
            self._pin_btn.pack(side="left", padx=(8, 0))
            aura.AuraButton(act, "Delete", kind="ghost", height=30, width=84,
                            command=self._delete_selected).pack(
                side="left", padx=(8, 0))

            txtwrap = ctk.CTkFrame(right, fg_color=pair("field"),
                                   corner_radius=10, border_width=1,
                                   border_color=pair("border"))
            txtwrap.grid(row=2, column=0, sticky="nsew", padx=(10, 0))
            self.detail_text = tk.Text(txtwrap, wrap="word", state="disabled",
                                       relief="flat", padx=10, pady=8,
                                       font=(MONO_FAMILY, 11))
            dsb = aura.AuraScrollbar(txtwrap, command=self.detail_text.yview)
            self.detail_text.configure(yscrollcommand=dsb.set)
            dsb.pack(side="right", fill="y", padx=(0, 4), pady=4)
            self.detail_text.pack(side="left", fill="both", expand=True,
                                  padx=(4, 0), pady=4)
            aura.track(self.detail_text, "text")

            tfrow = ctk.CTkFrame(right, fg_color="transparent")
            tfrow.grid(row=3, column=0, sticky="ew", padx=(10, 0),
                       pady=(8, 0))
            aura.Caption(tfrow, "Transform").pack(side="left", padx=(0, 8))
            self.transform_var = tk.StringVar(value=self._tf_labels[0])
            aura.AuraOption(tfrow, variable=self.transform_var,
                            values=self._tf_labels, width=220,
                            height=30).pack(side="left", padx=(0, 8))
            aura.AuraButton(tfrow, "Apply → copy", kind="secondary",
                            height=30,
                            command=self._apply_transform).pack(side="left")

            # ---- empty states
            self.empty_all = aura.EmptyState(
                frame, title="Nothing captured yet",
                caption="Copy text anywhere and it appears here — or add a "
                        "snippet by hand. Pinned clips survive Clear.",
                action_text="＋ Add snippet", action=self._add_snippet_dialog,
                image=(asset_path("assets/clips-empty-light.png"),
                       asset_path("assets/clips-empty-dark.png")))
            self.empty_detail = aura.EmptyState(
                right, glyph="⧉", title="No clip selected",
                caption="Choose a clip on the left — double-click any row "
                        "to copy it straight to the clipboard.")
            self.after(250, self._init_sash)

        def _init_sash(self):
            try:
                if self.panes.winfo_width() > 700:
                    self.panes.sashpos(0, 430)
            except Exception:
                pass

        def _focus_search(self):
            try:
                self.show("history")
                self.search.focus_set()
            except Exception:
                pass

        # ---- refresh pipeline
        def _visible_items(self):
            query = self.search.get().strip() if hasattr(self, "search") else ""
            items = self.store.search(query) if query else self.store.items()
            now = time.time()
            return [it for it in items
                    if matches_filter(it, self._filter, now)]

        def refresh(self):
            if not hasattr(self, "tree"):
                return  # History section not built yet
            items = self._visible_items()
            sel_kept = False
            self.tree.delete(*self.tree.get_children())
            for it in items:
                star = "★ " if it["pinned"] else ""
                iid = str(it["id"])
                self.tree.insert("", "end", iid=iid,
                                 values=(star + _one_line(it["text"]),
                                         rel_date(it["ts"])),
                                 tags=("pinned",) if it["pinned"] else ())
                if it["id"] == self._selected_id:
                    self.tree.selection_set(iid)
                    sel_kept = True
            if not sel_kept:
                self._selected_id = None
            self._style_tree_tags()
            self._refresh_filter_sidebar()
            self._update_empty_states(items)
            self._update_detail()
            n = len(self.store.items())
            p = len(self.store.pinned())
            shown = len(items)
            base = f"{n} clip{'s' if n != 1 else ''} · {p} pinned"
            if shown != n:
                base += f" · showing {shown}"
            self.set_status(base)

        def _style_tree_tags(self):
            try:
                self.tree.tag_configure("pinned",
                                        foreground=aura.P("accent"))
            except Exception:
                pass

        def _update_empty_states(self, visible):
            has_any = bool(self.store.items())
            if has_any or not hasattr(self, "empty_all"):
                self.empty_all.place_forget()
                self.panes.grid()
            else:
                self.panes.grid_remove()
                self.empty_all.place(relx=0, rely=0.1, relwidth=1,
                                     relheight=0.88)
                self.empty_all.lift()

        def _update_detail(self):
            item = self._selected_item()
            if item is None:
                self.empty_detail.place(x=0, y=0, relwidth=1, relheight=1)
                self.empty_detail.lift()
                self.detail_head.configure(text="")
                self.detail_meta.configure(text="")
                self._set_detail_text("")
                return
            self.empty_detail.place_forget()
            lines = len(item["text"].splitlines()) or 1
            chars = len(item["text"])
            self.detail_head.configure(
                text="Pinned clip" if item["pinned"] else "Clip")
            self.detail_meta.configure(
                text=f"copied {rel_date(item['ts'])} · {lines} "
                     f"line{'s' if lines != 1 else ''} · {chars} chars")
            self._pin_btn.configure(
                text="Unpin" if item["pinned"] else "Pin")
            self._set_detail_text(item["text"])

        def _set_detail_text(self, text):
            self.detail_text.configure(state="normal")
            self.detail_text.delete("1.0", "end")
            self.detail_text.insert("1.0", text)
            self.detail_text.configure(state="disabled")

        # ---- selection / row actions
        def _on_select(self, _e=None):
            sel = self.tree.selection()
            self._selected_id = int(sel[0]) if sel else None
            self._update_detail()

        def _selected_item(self):
            if self._selected_id is None:
                return None
            return self.store.get(self._selected_id)

        def _show_row_menu(self, event):
            iid = self.tree.identify_row(event.y)
            if not iid:
                return
            self.tree.selection_set(iid)
            self._on_select()
            item = self._selected_item()
            if item is None:
                return
            m = self._row_menu
            m.delete(0, "end")
            m.add_command(label="Copy", command=self._copy_selected)
            m.add_command(label="Unpin" if item["pinned"] else "Pin",
                          command=self._toggle_pin_selected)
            m.add_separator()
            m.add_command(label="Delete", command=self._delete_selected)
            aura.style_menu(m)
            try:
                m.tk_popup(event.x_root, event.y_root)
            finally:
                m.grab_release()

        def _copy_selected(self):
            item = self._selected_item()
            if item is None:
                self.set_status("Select a clip first")
                return
            if set_clipboard(item["text"]):
                self.set_success("Copied to clipboard")
            else:
                self.set_error("Clipboard unavailable — could not copy")

        def _toggle_pin_selected(self):
            item = self._selected_item()
            if item is None:
                self.set_status("Select a clip first")
                return
            if item["pinned"]:
                self.store.unpin(item["id"])
                self.set_status("Unpinned")
            else:
                self.store.pin(item["id"])
                self.set_status("Pinned — survives Clear")
            self.refresh()

        def _delete_selected(self):
            item = self._selected_item()
            if item is None:
                self.set_status("Select a clip first")
                return
            self.store.delete(item["id"])
            self._selected_id = None
            self.set_status("Deleted clip")
            self.refresh()

        # ---- add snippet dialog (toolbar primary, Ctrl+N)
        def _add_snippet_dialog(self):
            dlg = aura.Dialog(self, title="Add snippet", size=(520, 340))
            aura.Caption(dlg.body,
                         "Saved into the history like any captured copy — "
                         "pin it to keep it forever.").pack(anchor="w")
            box = aura.AuraTextbox(dlg.body, height=170)
            box.pack(fill="both", expand=True, pady=(8, 0))

            def add():
                text = box.get("1.0", "end-1c")
                if not text.strip():
                    self.set_status("Nothing to add")
                    return
                new_id = self.store.add(text, force=True)
                dlg.close()
                if new_id is None:
                    self.set_status("Not added (duplicate of newest)")
                else:
                    self._selected_id = new_id
                    self.set_success("Snippet added")
                self.refresh()

            dlg.add_button("Add snippet", add)
            box.focus_set()

        # ---- transforms (result -> clipboard AND newest clip)
        def _apply_transform(self):
            item = self._selected_item()
            if item is None:
                self.set_error("Select a clip first (click a row).")
                return
            label = self.transform_var.get()
            name = self._tf_by_label.get(label)
            try:
                result = transforms.apply(name, item["text"])
            except Exception as exc:
                self.set_error(str(exc))
                return
            new_id = self.store.add(result, force=True)
            if new_id is not None:
                self._selected_id = new_id
            copied = set_clipboard(result)
            self.refresh()
            if copied:
                self.set_success(f"Applied “{label}” — result copied and "
                                 f"saved as the newest clip")
            else:
                self.set_status(f"Applied “{label}” — saved as the newest "
                                f"clip (clipboard unavailable)")

        def _clear_history(self):
            removed = self.store.clear(keep_pinned=True)
            self._selected_id = None
            self.refresh()
            self.set_status(f"Cleared {removed} entr"
                            f"{'y' if removed == 1 else 'ies'} (pinned kept)")

        def _toggle_pause(self):
            if self.store.is_paused():
                self.store.resume()
                self._pause_btn.configure(text="Pause capture")
                self.set_status("Capture resumed")
            else:
                self.store.pause()
                self._pause_btn.configure(text="Resume capture")
                self.set_status("Capture paused — copies are not recorded",
                                kind="working")

        # =================================================================
        # Settings dialog (Ctrl+,)
        # =================================================================
        def _open_settings(self):
            dlg = aura.Dialog(self, title="Settings", size=(520, 360))

            aura.SectionLabel(dlg.body, "History").pack(anchor="w",
                                                        pady=(0, 2))
            crow = ctk.CTkFrame(dlg.body, fg_color="transparent")
            crow.pack(anchor="w", pady=(4, 4))
            aura.Caption(crow, "Keep up to").pack(side="left", padx=(0, 10))
            cap = aura.AuraOption(
                crow, values=[str(c) for c in CAP_CHOICES],
                width=100, height=30, command=self._set_cap)
            cap.set(str(self.store.cap)
                    if self.store.cap in CAP_CHOICES else str(CAP_CHOICES[1]))
            cap.pack(side="left")
            aura.Caption(crow, "clips (pinned always kept)").pack(
                side="left", padx=(10, 0))

            aura.SectionLabel(dlg.body, "Appearance").pack(anchor="w",
                                                           pady=(14, 2))
            trow = ctk.CTkFrame(dlg.body, fg_color="transparent")
            trow.pack(anchor="w", pady=(4, 0))
            aura.Caption(trow, "Theme").pack(side="left", padx=(0, 10))
            cur = self.store.get_theme()
            th = aura.AuraOption(trow, values=["System", "Light", "Dark"],
                                 width=110, height=30,
                                 command=self._set_theme_pref)
            th.set(cur.capitalize() if cur in ("light", "dark") else "System")
            th.pack(side="left")
            aura.Caption(dlg.body,
                         "System follows the OS Aura Dark/Light live.").pack(
                anchor="w", pady=(6, 0))
            aura.Caption(dlg.body,
                         "Only what you copy is stored — on this device, "
                         "never uploaded.").pack(anchor="w", pady=(14, 0))

            dlg.add_button("Close")

        def _set_cap(self, value):
            try:
                self.store.set_cap(int(value))
            except (TypeError, ValueError):
                return
            self.refresh()

        def _set_theme_pref(self, choice):
            pref = str(choice).lower()
            if pref == "system":
                self.store.set_theme("system")
                self._follow_system = True
                if getattr(self, "_sys_listener", None) is None:
                    self._start_system_listener()
                self.set_theme(aura._system_theme(), _system=True)
            elif pref in ("light", "dark"):
                self.set_theme(pref)     # persists via on_theme_change

        # ---- theme: restyle raw-tk surfaces with the flip
        def set_theme(self, theme, _system=False):
            super().set_theme(theme, _system=_system)
            try:
                self._style_tree_tags()
                self._refresh_filter_sidebar()
            except Exception:
                pass

        # =================================================================
        # About section
        # =================================================================
        def _build_about(self, frame):
            card = aura.Card(frame, title=APP_NAME)
            card.pack(anchor="nw", fill="x")
            aura.Caption(card.body, f"Version {APP_VERSION}").pack(anchor="w")
            ctk.CTkLabel(
                card.body, font=aura.font(role="body"), justify="left",
                anchor="w", wraplength=680,
                text="A fast, fully-offline clipboard history in the spirit "
                     "of Ditto: every copy lands in a searchable list, "
                     "double-click re-copies it, pins survive Clear, and "
                     "quick filters (links, multi-line, today) find the "
                     "right clip fast.  Fourteen text transforms (case, "
                     "whitespace, base64, URL-encode, slugify…) turn a clip "
                     "into what you actually need.\n\n"
                     "100% AI-built, open source, published on QuickOpen. "
                     "Only what you copy is stored — on this device, never "
                     "uploaded.").pack(anchor="w")
            aura.Caption(card.body,
                         "Licensed under Apache-2.0. Built on the standard "
                         "library plus pyperclip and CustomTkinter (MIT).").pack(
                anchor="w", pady=(10, 4))
            aura.AuraButton(card.body, "Project page: quickopen.ai",
                            kind="ghost",
                            command=lambda: open_with_default_app(
                                PROJECT_URL)).pack(anchor="w", pady=(6, 0))

        # ---- background clipboard poll
        def _start_poll(self):
            if not clipboard_available():
                self._clip_ok = False
                self.set_status(
                    "No clipboard backend detected — browse, pin and Add "
                    "still work; auto-capture and Copy are disabled.",
                    kind="err")
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
                self.set_status("Captured a new copy")

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
              f"GUI here ({exc}). This app is intended for the desktop.")
        return 0
    except Exception as exc:
        print(f"{APP_NAME}: could not start the GUI ({exc}).")
        return 1

    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
