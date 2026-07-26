"""Small layout helpers shared by the main window and the dialogs."""

from __future__ import annotations

from tkinter import ttk

MUTED = "Muted.TLabel"


def section(parent, row: int, text: str, *, first: bool = None) -> int:
    """A section heading plus the rule that runs to the right of it.

    Not ttk.LabelFrame: that draws a raised 3D box and its label doesn't inherit
    the frame background, which reads as a stray grey panel under the dark theme.

    Returns the next free grid row.
    """
    if first is None:
        first = row == 0
    head = ttk.Frame(parent)
    head.grid(row=row, column=0, sticky="ew", pady=(0 if first else 14, 6))
    head.columnconfigure(1, weight=1)
    ttk.Label(head, text=text, style="Section.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Separator(head, orient="horizontal").grid(row=0, column=1, sticky="ew",
                                                  padx=(10, 0))
    return row + 1


def hint(parent, text: str, **grid):
    """A dimmed explanation under a control.

    Placed for you only when grid options are given; otherwise the label comes
    back unplaced so the caller can pack it. Gridding unconditionally would put
    it under whichever geometry manager the parent isn't using, which Tk refuses
    at runtime rather than at build time.
    """
    label = ttk.Label(parent, text=text, style=MUTED, wraplength=380,
                      justify="left")
    if grid:
        label.grid(**grid)
    return label
