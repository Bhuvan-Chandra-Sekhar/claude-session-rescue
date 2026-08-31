"""Small text helpers shared by the commands that print prose.

Terminal output that runs off the right edge is unreadable, and this tool talks
to people who are already frustrated. Everything narrative goes through
:func:`wrap_block`.
"""

from __future__ import annotations

import textwrap
from typing import List

RULE = "-" * 72
WIDTH = 78


def wrap_block(text: str, indent: str = "", width: int = WIDTH,
               keep_breaks: bool = False) -> List[str]:
    """Wrap paragraphs to a terminal-friendly width.

    Lines that are already short, and anything the caller marks as unbreakable,
    are left alone so that command examples stay copy-pasteable.
    """
    out: List[str] = []
    for raw in text.strip().splitlines():
        line = raw.rstrip()
        if not line.strip():
            out.append("")
            continue
        stripped = line.strip()
        if keep_breaks or len(stripped) + len(indent) <= width:
            out.append(indent + stripped)
            continue
        extra = "  " if stripped.startswith(("break ", "- ", "* ")) else ""
        out.extend(textwrap.wrap(
            stripped, width=width, initial_indent=indent,
            subsequent_indent=indent + extra, break_long_words=False,
            break_on_hyphens=False,
        ))
    return out


def paragraph(text: str, indent: str = "") -> str:
    """Wrap a single paragraph and return it as one string."""
    return "\n".join(wrap_block(text, indent=indent))


def heading(number: int, title: str) -> str:
    """A numbered section heading for the walkthrough."""
    return "{0}. {1}\n{2}".format(number, title, "-" * min(len(title) + 3, WIDTH))
