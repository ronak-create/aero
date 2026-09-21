"""
The Atria theme.

A "dawn" palette: deep indigo background tones, violet/iris as the primary
accent, warm amber for anything needing human attention, and a teal/coral pair
for diffs. Deliberately its own identity, built around the Atria name.
"""
from __future__ import annotations

from term import rgb


def _r(r: int, g: int, b: int) -> str:
    return rgb(r, g, b)


class Theme:
    """Semantic color slots. Read these instead of hardcoding escapes."""

    # Core accents
    primary = _r(139, 122, 255)        # iris violet -- brand color, headers
    primary_dim = _r(99, 86, 214)      # borders, rules, secondary chrome
    accent = _r(79, 196, 249)          # sky cyan -- user messages, links

    # Roles
    user = _r(129, 200, 255)           # "you" label
    assistant = _r(169, 143, 255)      # agent replies
    reasoning = _r(122, 133, 168)      # dim slate -- internal monologue
    success = _r(74, 222, 166)         # teal-green -- done, additions
    warning = _r(251, 191, 84)         # amber -- approvals, caution
    danger = _r(248, 113, 113)         # coral -- errors, deletions

    # Surfaces
    panel = _r(26, 27, 43)             # code block background (deep indigo)
    panel_border = _r(52, 54, 82)      # code block + panel border
    muted = _r(110, 114, 148)          # hints, secondary text
    faint = _r(68, 71, 96)             # rules, nearly-invisible chrome

    # Diff lines
    diff_add = _r(74, 222, 166)
    diff_add_bg = _r(21, 42, 36)
    diff_del = _r(248, 113, 113)
    diff_del_bg = _r(48, 26, 34)
    diff_hunk = _r(129, 140, 248)      # @@ hunk headers
    diff_meta = _r(110, 114, 148)      # +++ / --- file headers

    # Code block language tag
    code_tag = _r(122, 133, 168)


# A 16-color fallback for terminals without truecolor support.
THEME_FALLBACK = {
    "primary": "\033[35m",
    "primary_dim": "\033[95m",
    "accent": "\033[36m",
    "user": "\033[36m",
    "assistant": "\033[95m",
    "reasoning": "\033[90m",
    "success": "\033[32m",
    "warning": "\033[33m",
    "danger": "\033[31m",
    "panel": "",
    "panel_border": "\033[90m",
    "muted": "\033[90m",
    "faint": "\033[90m",
    "diff_add": "\033[32m",
    "diff_add_bg": "",
    "diff_del": "\033[31m",
    "diff_del_bg": "",
    "diff_hunk": "\033[35m",
    "diff_meta": "\033[90m",
    "code_tag": "\033[90m",
}


def supports_truecolor() -> bool:
    """True if the terminal likely supports 24-bit color.

    Checks COLORTERM first (the standard signal), then falls back to
    heuristics: Windows Terminal, WezTerm, iTerm2, VS Code's integrated
    terminal, and modern versions of most other emulators all support
    truecolor but don't always set COLORTERM.
    """
    import os
    import sys

    colorterm = os.environ.get("COLORTERM", "").lower()
    if colorterm in ("truecolor", "24bit"):
        return True

    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    if term_program in ("iterm.app", "hyper", "wezterm", "vscode"):
        return True

    term = os.environ.get("TERM", "")
    if "256color" in term or "kitty" in term:
        return True

    wt_session = os.environ.get("WT_SESSION", "")
    if wt_session:
        return True

    if sys.platform == "win32":
        # Windows Terminal and the modern Windows Console Host (ConHost v2+)
        # both support truecolor via VT sequences. If we got this far on
        # Windows, the VT processing enable in term.py already succeeded, so
        # truecolor is almost certainly available.
        try:
            build = int(os.environ.get("VSCODE_GIT_ASKPASS_NODE", "")
                        or "0")
        except ValueError:
            build = 0
        # Windows 10 build 14931+ supports 24-bit color. Rather than parsing
        # the build number (fragile), trust that modern Windows + VT = truecolor.
        return True

    return False


def load_theme() -> Theme:
    """
    Return the active theme.

    Falls back to 16-color equivalents when the terminal can't render
    truecolor, so we never emit escape sequences that show up as raw text.
    """
    if supports_truecolor():
        return Theme

    # Rebuild the same attribute set from the 16-color fallback table.
    return type("FallbackTheme", (), dict(THEME_FALLBACK))
