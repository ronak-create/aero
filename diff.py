"""
Render file edits as colored unified diffs.

The agent's edit_file does an exact-string replacement; this computes the
before/after difference so the UI can show you exactly what will change and
let you approve or reject it -- rather than trusting a bare file path.
"""
from __future__ import annotations

import difflib

from term import RESET
from theme import Theme as T


def make_diff(path: str, old: str, new: str, context: int = 3) -> list[str]:
    """
    Return unified-diff lines (without a trailing newline on each).

    `old` is the current file content; `new` is what the edit would produce.
    Returns an empty list when there's no change or either input is missing.
    """
    if old is None or new is None or old == new:
        return []
    old_lines = old.splitlines(keepends=False)
    new_lines = new.splitlines(keepends=False)
    return list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"{path}",
            tofile=f"{path}",
            n=context,
            lineterm="",
        )
    )


def render_diff(diff_lines: list[str], width: int) -> list[str]:
    """Turn unified-diff lines into styled display lines."""
    if not diff_lines:
        return []

    inner_w = max(width - 4, 2)
    out: list[str] = []

    # Header bar for the panel.
    added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
    stats = f"{T.success}+{added}{RESET} {T.danger}-{removed}{RESET}"
    out.append(
        f"{T.panel_border}┌{RESET} {stats} "
        f"{T.diff_meta}{diff_lines[0] if diff_lines else ''}{RESET}"
    )

    for line in diff_lines:
        if line.startswith("+++"):
            out.append(f"{T.panel_border}│{RESET} {T.diff_meta}{_truncate(line, inner_w)}{RESET}")
        elif line.startswith("---"):
            out.append(f"{T.panel_border}│{RESET} {T.diff_meta}{_truncate(line, inner_w)}{RESET}")
        elif line.startswith("@@"):
            out.append(f"{T.panel_border}│{RESET} {T.diff_hunk}{_truncate(line, inner_w)}{RESET}")
        elif line.startswith("+"):
            out.append(
                f"{T.panel_border}│{RESET}{T.diff_add_bg} {T.diff_add}"
                f"{_truncate(line, inner_w)}{RESET}"
            )
        elif line.startswith("-"):
            out.append(
                f"{T.panel_border}│{RESET}{T.diff_del_bg} {T.diff_del}"
                f"{_truncate(line, inner_w)}{RESET}"
            )
        else:
            out.append(f"{T.panel_border}│{RESET} {_truncate(line, inner_w)}{RESET}")

    out.append(f"{T.panel_border}└{'┄' * (inner_w + 1)}{RESET}")
    return out


def _truncate(line: str, width: int) -> str:
    from term import char_width

    if sum(char_width(c) for c in line) <= width:
        return line
    out: list[str] = []
    w = 0
    for ch in line:
        if w + char_width(ch) > width - 1:
            break
        out.append(ch)
        w += char_width(ch)
    return "".join(out) + "…"
