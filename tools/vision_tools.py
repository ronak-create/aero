"""
Image reading tool.

Tool results in the chat-completions protocol are plain text, so this
returns a data: URI string. agent.py special-cases the `read_image` tool
name and, when the result looks like a data URI, follows up with a proper
image_url content block in the next message so a vision-capable model can
actually see the pixels (not just the string).
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

MAX_BYTES = 8 * 1024 * 1024  # 8MB, generous for a single image


def read_image(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"ERROR: file not found: {path}"

    size = p.stat().st_size
    if size > MAX_BYTES:
        return f"ERROR: {path} is {size} bytes, larger than the {MAX_BYTES} byte limit"

    mime, _ = mimetypes.guess_type(str(p))
    if not mime or not mime.startswith("image/"):
        return f"ERROR: {path} does not look like an image (guessed type: {mime})"

    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"
