"""Exception types shared across the app."""

from __future__ import annotations


class BlerpError(Exception):
    """Recoverable error: ends the run in single mode, skips one bite in bulk mode."""


class UpdateError(BlerpError):
    """A self-update step failed. Subclasses BlerpError so existing handlers still catch it."""
