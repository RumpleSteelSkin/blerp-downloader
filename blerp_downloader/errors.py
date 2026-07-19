"""Exception types shared across the download pipeline."""

from __future__ import annotations


class BlerpError(Exception):
    """Recoverable error: ends the run in single mode, skips one bite in bulk mode."""
