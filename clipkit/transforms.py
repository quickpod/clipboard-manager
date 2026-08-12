"""On-demand text transforms -- pure functions, no side effects.

Every transform takes a ``str`` and returns a ``str``.  They power both the CLI
(``clipkit transform <name> "text"``) and the GUI transforms toolbar (apply to
the selected entry, then copy the result).  The :data:`TRANSFORMS` registry maps
a stable command-line name to ``(label, func)`` so callers can enumerate them.
"""

from __future__ import annotations

import base64 as _base64
import binascii
import re
import unicodedata
import urllib.parse

from .errors import ClipKitError


# --- case ------------------------------------------------------------------
def upper(text):
    """Uppercase the whole string."""
    return text.upper()


def lower(text):
    """Lowercase the whole string."""
    return text.lower()


def title(text):
    """Title-case the string (first letter of each word capitalised)."""
    return text.title()


# --- whitespace ------------------------------------------------------------
def trim(text):
    """Strip leading/trailing whitespace from the whole string."""
    return text.strip()


def collapse_whitespace(text):
    """Collapse every run of whitespace to a single space and strip the ends."""
    return re.sub(r"\s+", " ", text).strip()


def join_lines(text):
    """Join all lines into one, separating former line breaks with a space."""
    parts = [ln.strip() for ln in text.splitlines()]
    return " ".join(p for p in parts if p)


def split_lines(text):
    """Split on commas / semicolons / whitespace into one item per line."""
    parts = [p for p in re.split(r"[,\s;]+", text.strip()) if p]
    return "\n".join(parts)


def remove_newlines(text):
    """Remove all line breaks, concatenating the lines with no separator."""
    return text.replace("\r\n", "").replace("\r", "").replace("\n", "")


# --- encoding --------------------------------------------------------------
def base64_encode(text):
    """Base64-encode the UTF-8 bytes of the text."""
    return _base64.b64encode(text.encode("utf-8")).decode("ascii")


def base64_decode(text):
    """Base64-decode to UTF-8 text. Raises ClipKitError on invalid input."""
    try:
        raw = _base64.b64decode(text.strip().encode("ascii"), validate=True)
        return raw.decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ClipKitError(f"Not valid base64 text: {exc}")


def url_encode(text):
    """Percent-encode the text (spaces become %20)."""
    return urllib.parse.quote(text, safe="")


def url_decode(text):
    """Decode a percent-encoded string. Raises ClipKitError on bad input."""
    try:
        return urllib.parse.unquote(text, errors="strict")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ClipKitError(f"Not valid URL-encoded text: {exc}")


# --- misc ------------------------------------------------------------------
def slugify(text):
    """Make a URL/file-friendly slug: ascii, lowercase, hyphen-separated."""
    norm = unicodedata.normalize("NFKD", text)
    ascii_text = norm.encode("ascii", "ignore").decode("ascii")
    ascii_text = ascii_text.lower()
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text)
    return ascii_text.strip("-")


def count(text):
    """Return a human-readable count summary (characters / words / lines)."""
    chars = len(text)
    words = len(text.split())
    lines = len(text.splitlines()) or (1 if text else 0)
    return f"characters={chars} words={words} lines={lines}"


# Stable command name -> (human label, function).  Order = display order.
TRANSFORMS = {
    "upper": ("UPPERCASE", upper),
    "lower": ("lowercase", lower),
    "title": ("Title Case", title),
    "trim": ("Trim whitespace", trim),
    "collapse": ("Collapse whitespace", collapse_whitespace),
    "join-lines": ("Join lines", join_lines),
    "split-lines": ("Split into lines", split_lines),
    "remove-newlines": ("Remove line breaks", remove_newlines),
    "base64-encode": ("Base64 encode", base64_encode),
    "base64-decode": ("Base64 decode", base64_decode),
    "url-encode": ("URL encode", url_encode),
    "url-decode": ("URL decode", url_decode),
    "slugify": ("Slugify", slugify),
    "count": ("Count chars/words/lines", count),
}


def names():
    """The list of available transform command names (in display order)."""
    return list(TRANSFORMS.keys())


def apply(name, text):
    """Apply the named transform to *text*.

    Raises :class:`ClipKitError` for an unknown transform name (individual
    transforms may also raise it, e.g. invalid base64).
    """
    entry = TRANSFORMS.get(name)
    if entry is None:
        raise ClipKitError(
            f"Unknown transform '{name}'. Available: {', '.join(names())}")
    return entry[1](text)
