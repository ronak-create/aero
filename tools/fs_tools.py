"""File system tools: read, write, edit, list, and search files."""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path

MAX_READ_CHARS = 60_000  # guard against dumping huge files into the context


def read_file(path: str, start_line: int | None = None, end_line: int | None = None) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"ERROR: file not found: {path}"
    if p.is_dir():
        return f"ERROR: {path} is a directory, use list_dir instead"

    text = p.read_text(errors="replace")
    lines = text.splitlines()

    if start_line or end_line:
        start = (start_line or 1) - 1
        end = end_line if end_line else len(lines)
        lines = lines[max(start, 0):end]
        offset = max(start, 0)
    else:
        offset = 0

    numbered = "\n".join(f"{i + offset + 1:>5}\t{line}" for i, line in enumerate(lines))
    if len(numbered) > MAX_READ_CHARS:
        numbered = numbered[:MAX_READ_CHARS] + "\n... [truncated, file is large; request a line range]"
    return numbered


def write_file(path: str, content: str, overwrite: bool = False) -> str:
    p = Path(path).expanduser()
    if p.exists() and not overwrite:
        return f"ERROR: {path} already exists. Pass overwrite=true to replace it, or use edit_file."
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"OK: wrote {len(content)} chars to {path}"


def preview_edit(path: str, old_str: str, new_str: str) -> tuple[str, str, str]:
    """
    Compute what edit_file *would* do, without touching the file.

    Returns (old_content, new_content, error). Exactly one of new_content /
    error is set. Used by the UI to render a diff for approval before writing.
    """
    p = Path(path).expanduser()
    if not p.exists():
        return "", "", f"file not found: {path}"
    text = p.read_text()
    count = text.count(old_str)
    if count == 0:
        return text, "", "old_str not found in file (must match exactly, whitespace included)"
    if count > 1:
        return text, "", f"old_str matches {count} locations; make it more specific so it's unique"
    return text, text.replace(old_str, new_str, 1), ""


def edit_file(path: str, old_str: str, new_str: str) -> str:
    old_content, new_content, error = preview_edit(path, old_str, new_str)
    if error:
        return f"ERROR: {error}"
    Path(path).expanduser().write_text(new_content)
    return f"OK: applied edit to {path}"


def list_dir(path: str = ".") -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"ERROR: path not found: {path}"
    if not p.is_dir():
        return f"ERROR: {path} is not a directory"
    entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
    lines = []
    for e in entries:
        if e.name.startswith(".") and e.name not in (".env",):
            continue
        tag = "/" if e.is_dir() else ""
        lines.append(f"{e.name}{tag}")
    return "\n".join(lines) if lines else "(empty directory)"


def glob_search(pattern: str, path: str = ".") -> str:
    root = Path(path).expanduser()
    matches = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__", ".venv")]
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern):
                matches.append(rel)
        if len(matches) > 500:
            break
    return "\n".join(matches[:500]) if matches else "(no matches)"


def grep_search(pattern: str, path: str = ".", file_glob: str = "*") -> str:
    import re

    root = Path(path).expanduser()
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"ERROR: invalid regex: {e}"

    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__", ".venv")]
        for name in filenames:
            if not fnmatch.fnmatch(name, file_glob):
                continue
            full = Path(dirpath) / name
            try:
                for i, line in enumerate(full.read_text(errors="ignore").splitlines(), start=1):
                    if regex.search(line):
                        rel = os.path.relpath(full, root)
                        results.append(f"{rel}:{i}: {line.strip()[:200]}")
            except (UnicodeDecodeError, PermissionError, IsADirectoryError):
                continue
        if len(results) > 300:
            break
    return "\n".join(results[:300]) if results else "(no matches)"
