"""clipkit -- a permissively-licensed clipboard-history library.

Public pieces:

    from clipkit.store import Store
    from clipkit import transforms
    from clipkit.monitor import poll_clipboard, get_clipboard, set_clipboard

``Store`` is a pure-stdlib, JSON-backed clipboard history + pinned-snippet store
that is fully testable headless (it never touches a real clipboard).  The GUI
(:mod:`clipkit.gui`) and CLI (:mod:`clipkit.__main__`) build on top of it.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

from .errors import ClipKitError

__all__ = ["ClipKitError", "__version__"]

__version__ = "1.0.0"
