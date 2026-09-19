"""
Very small Markdown -> ANSI renderer.

Good enough for chat output: fenced code blocks with a panel, inline code,
bold/italic, headings, bullet lists, and table passthrough (aligned by the
author's spacing). Not a spec-complete parser -- it renders model prose, not
arbitrary documents.
"""
from __future__ import annotations

import re

from term import (
    BOLD, ITALIC, RESET, UNDERLINE, strip_ansi, text_width, wrap_text,
)
from theme import Theme as T

_CODE_FENCE = re.compile(r"^(\s*)(```|~~~)(.*)$")
_INLINE_CODE = re.compile(r"(`+)(.+?)\1")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")
_HEADING = re.compile(r"^(#{1,6})\s*(.*)$")
_BULLET = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")
_TABLE = re.compile(r"^\s*\|.*\|\s*$")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

CODE_BG = T.panel
CODE_BORDER = T.panel_border
HEADING_COLOR = T.primary


def _inline(text: str, width: int) -> str:
    """Apply inline formatting (code, bold, italic, links) without wrapping."""
    def _code(m: re.Match) -> str:
        return f"{CODE_BG}{T.primary}{m.group(2)}{RESET}"

    def _bold(m: re.Match) -> str:
        return f"{BOLD}{m.group(1) or m.group(2)}{RESET}"

    def _ital(m: re.Match) -> str:
        return f"{ITALIC}{m.group(1) or m.group(2)}{RESET}"

    def _link(m: re.Match) -> str:
        return f"{T.accent}{UNDERLINE}{m.group(1)}{RESET}"

    # Links first so their text isn't re-processed for emphasis.
    text = _LINK.sub(_link, text)
    text = _INLINE_CODE.sub(_code, text)
    text = _BOLD.sub(_bold, text)
    text = _ITALIC.sub(_ital, text)
    return text


def _render_code_block(lines: list[str], info: str, width: int) -> list[str]:
    """A code block becomes a bordered panel one column inset from the margin."""
    inner_w = max(width - 4, 2)
    out: list[str] = []
    label = info.strip()
    bar = f"{CODE_BORDER}{'┄' * (inner_w + 2)}{RESET}"
    if label:
        out.append(f"{CODE_BORDER}┌{RESET} {T.code_tag}{label}{RESET}")
        out.append(f"{CODE_BORDER}│{RESET}{bar}")
    else:
        out.append(f"{CODE_BORDER}┌{bar[0]}{RESET}")
    for line in lines:
        # Truncate long lines rather than wrapping code.
        if text_width(strip_ansi(line)) > inner_w:
            while text_width(strip_ansi(line)) > inner_w and line:
                line = line[:-1]
            line += f"{RESET}{T.muted}…{RESET}"
        out.append(
            f"{CODE_BORDER}│{RESET} {CODE_BG}{line}"
            f"{' ' * max(inner_w - text_width(strip_ansi(line)), 0)}"
            f"{RESET}{CODE_BORDER} │{RESET}"
        )
    out.append(f"{CODE_BORDER}└{RESET}{bar}")
    return out


def render_markdown(text: str, width: int) -> list[str]:
    """
    Convert markdown text into a list of pre-styled ANSI display lines.
    `width` is the available display columns.
    """
    if not text:
        return [""]

    lines: list[str] = []
    src = text.split("\n")
    i = 0
    in_table = False

    while i < len(src):
        raw = src[i]

        # Fenced code block: collect until the matching fence.
        fence = _CODE_FENCE.match(raw)
        if fence:
            info = fence.group(3)
            code: list[str] = []
            i += 1
            while i < len(src):
                if _CODE_FENCE.match(src[i]):
                    break
                code.append(src[i])
                i += 1
            lines.extend(_render_code_block(code, info, width))
            i += 1
            continue

        # Tables: pass through verbatim; the author's spacing already aligns.
        if _TABLE.match(raw):
            table: list[str] = []
            while i < len(src) and _TABLE.match(src[i]):
                table.append(src[i])
                i += 1
            for row in table:
                cell = _inline(row, width)
                lines.append(f"{T.muted}{cell}{RESET}")
            lines.append("")
            continue

        heading = _HEADING.match(raw)
        if heading:
            level = len(heading.group(1))
            content = heading.group(2).strip()
            # Level shrinks the wrap width slightly for visual hierarchy.
            wrapped = wrap_text(content, width - level)
            for wl in wrapped:
                lines.append(f"{HEADING_COLOR}{BOLD}{wl}{RESET}")
            lines.append("")
            i += 1
            continue

        bullet = _BULLET.match(raw)
        if bullet:
            indent, marker, content = bullet.group(1), bullet.group(2), bullet.group(3)
            pad = " " * (len(indent) + len(marker) + 1)
            wrapped = wrap_text(content, width - len(pad))
            for j, wl in enumerate(wrapped):
                prefix = f"{indent}{T.accent}{marker}{RESET} " if j == 0 else pad
                lines.append(f"{prefix}{_inline(wl, width)}")
            i += 1
            continue

        if not raw.strip():
            lines.append("")
            i += 1
            continue

        for wl in wrap_text(raw, width):
            lines.append(_inline(wl, width))
        i += 1

    # Trim a single trailing blank the accumulator tends to add.
    while len(lines) > 1 and lines[-1] == "" and lines[-2] == "":
        lines.pop()
    return lines
