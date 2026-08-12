"""Command-line interface: ``python -m clipkit <command> ...``.

Talks to the same JSON store the GUI uses, so history added on the command line
shows up in the window and vice-versa.  Clipboard access is optional and always
guarded: ``get`` copies the entry to the clipboard when a backend is available
and simply notes it if not.  Any :class:`ClipKitError` is printed to stderr and
turns into a non-zero exit code.
"""

from __future__ import annotations

import argparse
import sys

from . import transforms
from .errors import ClipKitError
from .monitor import set_clipboard
from .store import Store


def _preview(text, width=70):
    """One-line preview of a possibly multi-line entry."""
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def _store():
    return Store()


# --- command handlers -------------------------------------------------------
def cmd_list(a):
    store = _store()
    items = store.items()
    if a.limit and a.limit > 0:
        items = items[: a.limit]
    if not items:
        print("(history is empty)")
        return
    for it in items:
        pin = "*" if it["pinned"] else " "
        print(f"{pin} [{it['id']}] {_preview(it['text'])}")


def cmd_add(a):
    store = _store()
    new_id = store.add(a.text, force=True)
    if new_id is None:
        print("Nothing added (empty or duplicate of the newest entry).")
    else:
        print(f"Added entry [{new_id}].")


def cmd_get(a):
    store = _store()
    item = store.get(a.id)
    if item is None:
        raise ClipKitError(f"No entry with id {a.id}.")
    sys.stdout.write(item["text"])
    if not item["text"].endswith("\n"):
        sys.stdout.write("\n")
    if set_clipboard(item["text"]):
        print(f"(copied entry [{a.id}] to the clipboard)", file=sys.stderr)
    else:
        print("(clipboard unavailable — printed only)", file=sys.stderr)


def cmd_pin(a):
    store = _store()
    if not store.pin(a.id):
        raise ClipKitError(f"No entry with id {a.id}.")
    print(f"Pinned entry [{a.id}].")


def cmd_unpin(a):
    store = _store()
    if not store.unpin(a.id):
        raise ClipKitError(f"No entry with id {a.id}.")
    print(f"Unpinned entry [{a.id}].")


def cmd_delete(a):
    store = _store()
    if not store.delete(a.id):
        raise ClipKitError(f"No entry with id {a.id}.")
    print(f"Deleted entry [{a.id}].")


def cmd_search(a):
    store = _store()
    hits = store.search(a.query)
    if not hits:
        print("(no matches)")
        return
    for it in hits:
        pin = "*" if it["pinned"] else " "
        print(f"{pin} [{it['id']}] {_preview(it['text'])}")


def cmd_clear(a):
    store = _store()
    removed = store.clear(keep_pinned=not a.all)
    kept = "" if a.all else " (pinned kept)"
    print(f"Cleared {removed} entr{'y' if removed == 1 else 'ies'}{kept}.")


def cmd_transform(a):
    result = transforms.apply(a.name, a.text)
    sys.stdout.write(result)
    if not result.endswith("\n"):
        sys.stdout.write("\n")
    if a.copy:
        if set_clipboard(result):
            print("(copied result to the clipboard)", file=sys.stderr)
        else:
            print("(clipboard unavailable — printed only)", file=sys.stderr)


# --- parser -----------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="clipkit",
        description="Offline clipboard-history manager. Entries are numbered; "
        "the newest is first.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, help, handler):
        sp = sub.add_parser(name, help=help)
        sp.set_defaults(func=handler)
        return sp

    s = add("list", "Show recent history (newest first)", cmd_list)
    s.add_argument("-n", "--limit", type=int, default=20,
                   help="max entries to show (default 20; 0 = all)")

    s = add("add", "Add text as the newest entry", cmd_add)
    s.add_argument("text")

    s = add("get", "Print an entry (and copy it if possible)", cmd_get)
    s.add_argument("id", type=int)

    s = add("pin", "Pin an entry so Clear keeps it", cmd_pin)
    s.add_argument("id", type=int)

    s = add("unpin", "Remove an entry's pin", cmd_unpin)
    s.add_argument("id", type=int)

    s = add("delete", "Delete an entry", cmd_delete)
    s.add_argument("id", type=int)

    s = add("search", "Search history for a substring", cmd_search)
    s.add_argument("query")

    s = add("clear", "Clear history (pinned kept unless --all)", cmd_clear)
    s.add_argument("--all", action="store_true",
                   help="also remove pinned entries")

    s = add("transform", "Apply a text transform and print the result",
            cmd_transform)
    s.add_argument("name", choices=transforms.names(), metavar="name",
                   help="one of: " + ", ".join(transforms.names()))
    s.add_argument("text")
    s.add_argument("-c", "--copy", action="store_true",
                   help="also copy the result to the clipboard")

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ClipKitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
