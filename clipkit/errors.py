"""Error types for clipkit."""


class ClipKitError(Exception):
    """Raised for any recoverable failure in a clipkit operation.

    All public code paths raise this (and only this) on an expected failure so
    callers -- the CLI and the GUI -- have a single exception type to catch and
    can show a clean message instead of a raw traceback.
    """
