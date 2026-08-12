# Clipboard Manager

A fast, **offline**, **100% open-source** clipboard manager for Windows. Nothing is uploaded anywhere. Built entirely by AI with human testing and guidance, and published on [QuickOpen](https://quickopen.ai/projects/clipboard-manager).

> **100% AI-built and open source.** Apache-2.0.

## What it does

Keeps a searchable history of what you copy, lets you pin frequently-used snippets, and re-copy any past entry with a click. Includes quick text transforms (upper/lower/title case, trim, join/split lines, base64, slugify) applied on paste. History stays on your machine and can be cleared or excluded per app. Lightweight and always available.

## Install

Download **`ClipboardManager-Setup.exe`** from the [QuickOpen page](https://quickopen.ai/projects/clipboard-manager) or the [GitHub release](https://github.com/quickpod/clipboard-manager/releases/latest) and double-click it. It installs per-user, adds Desktop and Start Menu shortcuts, and can optionally trust the QuickOpen Root CA. Authenticode-signed by the QuickOpen Code Signing CA — verify at [quickopen.ai/trust](https://quickopen.ai/trust).

## Run from source

```sh
pip install -r requirements.txt
python clipboard_app.py          # GUI
python -m clipkit --help    # CLI
```


## Features

- **Searchable history** — every copy is captured into a capped, newest-first history (default 200 entries) with instant substring search.
- **Pinned snippets** — pin frequently-used text so it survives a Clear and stays one click away.
- **Re-copy anything** — copy any past entry back to the clipboard from the window or the CLI.
- **Text transforms** — apply on demand and copy the result: UPPER / lower / Title case, trim, collapse whitespace, join / split lines, remove line breaks, Base64 encode/decode, URL encode/decode, slugify, and a chars/words/lines count.
- **Pause / resume capture** — stop recording copies whenever you want; manual adds still work.
- **Fully offline & private** — history is a plain JSON file on your machine (`%LOCALAPPDATA%\ClipboardManager\history.json`, or `~/.clipboardmanager/` elsewhere). Nothing is ever uploaded.
- **Dark mode** — a one-click theme toggle, remembered between runs.
- **Graceful degradation** — with no clipboard backend (e.g. a headless box) you can still browse, pin, search and add manually.

## CLI examples

```sh
python -m clipkit add "text to remember"      # add an entry
python -m clipkit list -n 20                   # show recent history (newest first)
python -m clipkit search "invoice"             # find entries containing a substring
python -m clipkit get 3                         # print entry 3 (and copy it if possible)
python -m clipkit pin 3                          # pin entry 3 so Clear keeps it
python -m clipkit unpin 3                        # remove the pin
python -m clipkit delete 3                       # delete entry 3
python -m clipkit clear                          # clear history (pinned kept; --all wipes pinned too)
python -m clipkit transform slugify "Héllo, World!"   # -> hello-world
python -m clipkit transform base64-encode "hi" -c     # transform and copy the result
```

Entries are numbered and the newest is shown first. Any error prints to stderr and exits non-zero.

## License

Apache-2.0 — see [LICENSE](LICENSE). A 100% AI-built project published on QuickOpen.
