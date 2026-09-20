"""
Full-screen terminal UI for termagent.

Layout (top to bottom):
    header      model / cwd / usage
    transcript  scrollable message history (user, reasoning, assistant, tools)
    input box   multi-line editor with history and slash commands
    status bar  busy spinner, pending-approval prompt, or a hint line

The agent loop runs in a worker thread; the main thread owns the terminal and
redraws. Cross-thread handoff is a queue of render requests plus a lock around
shared state, so nothing the model emits is ever dropped while you type.
"""
from __future__ import annotations

import json
import os
import queue
import shlex
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from agent import SYSTEM_PROMPT, Callbacks, run_turn
from config import Config
from llm_client import LLMClient
from markdown import render_markdown
from term import (
    BOLD, FG, ITALIC, RESET, KeyEvent, RawInput, alt_screen, char_width,
    clear_line, clear_screen, get_size, hide_cursor, move_to, show_cursor,
    strip_ansi, text_width,
)
from tools.todo_tools import get_todos

# ---------------------------------------------------------------------- blocks


@dataclass
class Block:
    """One entry in the transcript."""

    def render(self, width: int) -> list[str]:
        return [""]


@dataclass
class UserBlock(Block):
    text: str
    # A message typed while a turn was in flight. It is already on screen but
    # hasn't been sent: render it greyed out until the turn ends and it goes.
    queued: bool = False
    # The turn died before this could be sent. It stays greyed so it doesn't
    # read as something the agent saw, with a note explaining why.
    dropped: bool = False

    def render(self, width: int) -> list[str]:
        if not self.text:
            # An empty message has nothing to show; the caller already
            # refuses to submit one, so this is just defensive.
            return []
        T = _theme()
        # A dot marks the user's turn instead of a "you" label, with the
        # prompt itself highlighted so it reads as the input. A hollow dim
        # dot marks one that hasn't gone anywhere yet.
        if self.queued or self.dropped:
            marker = f"{T.muted}○{RESET}"
            color = T.muted
        else:
            marker = f"{T.success}●{RESET}"
            color = T.user
        indent = " " * len("● ")
        prefix = f"{marker} "
        wrapped = _wrap(self.text, width - len("● "))
        lines = []
        for i, line in enumerate(wrapped):
            if i == 0:
                lines.append(f"{prefix}{color}{line}{RESET}")
            else:
                lines.append(f"{indent}{color}{line}{RESET}")
        if self.queued:
            lines.append(
                f"{indent}{T.faint}(queued — sends when the current turn ends)"
                f"{RESET}"
            )
        elif self.dropped:
            lines.append(
                f"{indent}{T.faint}(not sent — the previous turn failed)"
                f"{RESET}"
            )
        lines.append("")
        return lines


@dataclass
class ReasoningBlock(Block):
    text: str = ""
    collapsed: bool = True

    def render(self, width: int) -> list[str]:
        T = _theme()
        one_line = self.text.replace("\n", " ").strip()
        if not one_line:
            return []
        if self.collapsed:
            # A single dim line; expand via the /expand command or Enter on it.
            if len(one_line) > width - len("reasoning "):
                one_line = one_line[: width - len("reasoning ") - 1] + "…"
            return [
                f"{T.reasoning}{ITALIC}⌁ reasoning{RESET} "
                f"{T.reasoning}{one_line}{RESET}",
                "",
            ]
        lines = [f"{T.reasoning}{ITALIC}⌁ reasoning{RESET}"]
        for line in _wrap(self.text, width - 2):
            lines.append(f"{T.reasoning}{ITALIC}{line}{RESET}")
        lines.append("")
        return lines


@dataclass
class AssistantBlock(Block):
    text: str = ""
    done: bool = False

    def render(self, width: int) -> list[str]:
        T = _theme()
        # A ">" marker leads the reply, matching the input box's own prompt
        # so a turn reads as a call and response down the left gutter.
        label = f"{T.assistant}{BOLD}>{RESET} "
        if not self.text:
            placeholder = (
                f"{T.warning}thinking…{RESET}" if not self.done
                else f"{T.muted}(no output){RESET}"
            )
            return [f"{label}{placeholder}", ""]
        md = render_markdown(self.text, width - len("agent "))
        out = []
        for i, line in enumerate(md):
            out.append(f"{label}{line}" if i == 0 else line)
        out.append("")
        return out


@dataclass
class ToolBlock(Block):
    name: str = ""
    args: dict = field(default_factory=dict)
    status: str = "running"  # running | done | error | denied
    result: str | None = None
    expanded: bool = False
    call_id: str = ""
    _rendered_args: str = field(default="", repr=False)

    def _summary(self) -> str:
        if self.name == "run_shell":
            return f"$ {self.args.get('command', '')}"
        if "path" in self.args:
            return str(self.args.get("path"))
        if "pattern" in self.args:
            return str(self.args.get("pattern"))
        if not self.args:
            return ""
        try:
            preview = json.dumps(self.args, ensure_ascii=False)
        except (TypeError, ValueError):
            preview = str(self.args)
        return preview

    def render(self, width: int) -> list[str]:
        T = _theme()
        icon = {
            "running": f"{T.warning}●{RESET}",
            "done": f"{T.success}✓{RESET}",
            "error": f"{T.danger}✗{RESET}",
            "denied": f"{T.warning}⊘{RESET}",
        }[self.status]
        label = f"{T.primary}{self.name}{RESET}"
        summary = self._summary()
        head = f"  {icon} {label} {T.muted}{summary}{RESET}"
        lines = [_truncate_visual(head, width)]
        if self.expanded and self.result is not None:
            for line in self.result.split("\n")[:200]:
                lines.append(f"{T.faint}  │ {RESET}{line}")
            lines.append("")
        return lines


@dataclass
class DiffBlock(Block):
    """A pending file edit shown as a colored diff, awaiting a decision."""

    path: str = ""
    diff_lines: list = field(default_factory=list)
    decided: str = ""  # "" | approved | rejected

    def render(self, width: int) -> list[str]:
        from diff import render_diff

        T = _theme()
        if self.decided == "approved":
            head = f"  {T.success}✓{RESET} {T.muted}edited {self.path}{RESET}"
            return [head, ""]
        if self.decided == "rejected":
            head = f"  {T.danger}✗{RESET} {T.muted}rejected edit to {self.path}{RESET}"
            return [head, ""]
        lines = render_diff(self.diff_lines, width)
        lines.append(
            f"  {T.warning}approve this change? [y/n]{RESET}"
        )
        return lines


@dataclass
class BannerBlock(Block):
    """The session header: Atria ASCII art beside a stats panel."""

    model: str = ""
    base_url: str = ""
    cwd: str = ""
    tool_count: int = 0
    plugin_names: tuple = ()
    yolo: bool = False

    _ART = (
        "    ___    ____    _____   ____  \n"
        "   /   |  / __ \\  / ___/  / __ \\ \n"
        "  / /| | / /_/ / / /__   / / / / \n"
        " / ___ |/ __  /  \\___ \\ / /_/ /  \n"
        "/_/  |_/_/ /_/ /_____/  \\____/   "
    )

    def _stats(self) -> list[tuple[str, str]]:
        host = self.base_url.replace("https://", "").replace("http://", "")
        host = host.rstrip("/").split("/")[0]
        # Keep the path short enough that the right panel never overflows.
        cwd = self.cwd
        if len(cwd) > 30:
            cwd = "…" + cwd[-29:]
        return [
            ("model", self.model),
            ("endpoint", host),
            ("cwd", cwd),
            ("tools", f"{self.tool_count} builtin"),
            ("plugins", ", ".join(self.plugin_names) if self.plugin_names else "none"),
            ("mode", "yolo (auto-approve)" if self.yolo else "confirm"),
        ]

    def render(self, width: int) -> list[str]:
        T = _theme()
        art_lines = [
            f"{T.primary}{line}{RESET}" for line in self._ART.split("\n")
        ]
        stats = self._stats()
        label_w = max((len(k) for k, _ in stats), default=0)
        stat_lines = [
            f"{T.muted}{k.ljust(label_w)}{RESET}  {T.accent}{v}{RESET}"
            for k, v in stats
        ]

        inner_w = max(width - 4, 2)
        art_w = max(len(line) for line in self._ART.split("\n"))

        # Decide side-by-side vs stacked by available width.
        need = art_w + 4 + label_w + 2 + max((len(v) for _, v in stats), default=0)
        side_by_side = inner_w >= need

        out: list[str] = [f"{T.primary_dim}┌{'─' * inner_w}{RESET}"]
        rows = max(len(art_lines), len(stat_lines)) + 1
        for i in range(rows):
            pad = " " * max(inner_w - 2, 0)
            left = art_lines[i] if i < len(art_lines) else ""
            right = stat_lines[i] if i < len(stat_lines) else ""
            if side_by_side:
                gap = max(art_w + 4 - _visual_len(left), 2)
                body = f"{left}{' ' * gap}{right}"
            else:
                body = left or right
            out.append(
                f"{T.primary_dim}│{RESET} {body}"
                f"{pad[len(_strip(body)):]}{T.primary_dim}│{RESET}"
            )
        out.append(f"{T.primary_dim}└{'─' * inner_w}{RESET}")
        out.append("")
        return out


def _visual_len(s: str) -> int:
    return text_width(strip_ansi(s))


def _strip(s: str) -> str:
    return strip_ansi(s)


def _fmt_tokens(n: int) -> str:
    """1234 -> 1.2k; keep the header narrow on long sessions."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


@dataclass
class SystemBlock(Block):
    text: str
    kind: str = "info"  # info | warn | error

    def render(self, width: int) -> list[str]:
        T = _theme()
        color = {
            "info": T.accent, "warn": T.warning, "error": T.danger,
        }[self.kind]
        out = []
        for line in _wrap(self.text, width - 2):
            out.append(f"{color}{line}{RESET}")
        out.append("")
        return out


@dataclass
class TodoBlock(Block):
    def render(self, width: int) -> list[str]:
        T = _theme()
        todos = get_todos()
        if not todos:
            return []
        lines = [f"{T.primary}{BOLD}tasks{RESET}"]
        icons = {
            "pending": f"{T.muted}[ ]{RESET}",
            "in_progress": f"{T.warning}[~]{RESET}",
            "done": f"{T.success}[x]{RESET}",
        }
        for t in todos:
            mark = icons.get(t["status"], icons["pending"])
            text_line = f"  {mark} {t['text']}{RESET}"
            lines.append(_truncate_visual(text_line, width))
        lines.append("")
        return lines


# --------------------------------------------------------------------- helpers

_THEME_INSTANCE = None


def _theme():
    """The active Atria theme (cached; truecolor or 16-color fallback)."""
    global _THEME_INSTANCE
    if _THEME_INSTANCE is None:
        from theme import load_theme

        _THEME_INSTANCE = load_theme()
    return _THEME_INSTANCE


def _wrap(text: str, width: int) -> list[str]:
    from term import wrap_text

    return wrap_text(text, max(width, 1))





def _truncate_visual(line: str, width: int) -> str:
    visible = strip_ansi(line)
    if text_width(visible) <= width:
        return line
    out: list[str] = []
    w = 0
    in_esc = False
    for ch in line:
        if ch == "\033":
            in_esc = True
            out.append(ch)
            continue
        if in_esc:
            out.append(ch)
            if ch.isalpha():
                in_esc = False
            continue
        if w + char_width(ch) > width - 1:
            break
        out.append(ch)
        w += char_width(ch)
    return "".join(out) + f"{RESET}{FG.BRIGHT_BLACK}…{RESET}"


SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# Tools that ship with termagent; anything else in IMPLEMENTATIONS is a plugin.
_BUILTIN_TOOL_NAMES = {
    "read_file", "write_file", "edit_file", "list_dir", "glob_search",
    "grep_search", "run_shell", "fetch_url", "read_image", "todo_write",
    "todo_read",
}

SLASH_COMMANDS = {
    "/help": "show commands",
    "/clear": "reset history",
    "/exit": "quit",
    "/quit": "quit",
    "/save": "save session",
    "/load": "load session",
    "/plugins": "list plugin tools",
    "/todos": "toggle todo panel",
    "/expand": "toggle reasoning",
    "/yolo": "toggle auto-approve",
    "/model": "show model info",
}


# ------------------------------------------------------------------------ app

def _last_edit(app: "TuiApp") -> tuple[str | None, dict | None]:
    """The most recent pending tool call, if it's a file edit."""
    for block in reversed(app.tool_blocks):
        if block.status == "running" and block.name in ("edit_file", "write_file"):
            return block.name, block.args
    return None, None


class TuiCallbacks(Callbacks):
    """Feeds agent events into the UI thread-safe queue."""

    def __init__(self, app: "TuiApp") -> None:
        self.app = app
        self._current_assistant: AssistantBlock | None = None
        self._current_reasoning: ReasoningBlock | None = None

    def _touch(self) -> None:
        self.app.request_render()

    def on_content_delta(self, text: str) -> None:
        with self.app.lock:
            if self._current_assistant is None:
                self._current_assistant = AssistantBlock()
                self.app.blocks.append(self._current_assistant)
            self._current_assistant.text += text
            # Count what's streamed so the header's token counter climbs while
            # the reply is being written. The endpoint only bills at the end,
            # so this is an estimate until on_usage lands the real number.
            self.app._in_flight_chars += len(text)
        # NOTE: deliberately not setting autoscroll here. scroll_offset is
        # measured from the bottom, so an offset of 0 already tracks new
        # output; pinning unconditionally would cancel any scroll-up the
        # user just did, the instant the model emitted another token.
        self._touch()

    def on_reasoning_delta(self, text: str) -> None:
        with self.app.lock:
            if self._current_reasoning is None:
                self._current_reasoning = ReasoningBlock()
                self.app.blocks.append(self._current_reasoning)
            self._current_reasoning.text += text
            self.app._in_flight_chars += len(text)
        self._touch()

    def on_status(self, status: str) -> None:
        self.app.set_status(status)
        if status == "idle":
            with self.app.lock:
                self._current_assistant = None
                self._current_reasoning = None
                # A provider that never reports usage would otherwise leave a
                # stale estimate climbing the header forever after the turn.
                self.app._in_flight_chars = 0

    def on_tool_start(self, name: str, args: dict, call_id: str = "") -> None:
        with self.app.lock:
            # A tool call interrupts an in-progress assistant message; close
            # it out so the transcript reads in order.
            self._current_assistant = None
            self._current_reasoning = None
            block = ToolBlock(name=name, args=args, call_id=call_id)
            self.app.blocks.append(block)
            self.app.tool_blocks.append(block)
        self.app.set_status(f"running {name}")
        self._touch()

    def on_tool_result(self, name: str, result: str, error: bool, call_id: str = "") -> None:
        with self.app.lock:
            # Match by call_id first: two calls of the same tool can be in
            # flight at once, and matching by name would resolve the wrong
            # block. Fall back to name for providers that don't send ids.
            block = None
            if call_id:
                block = next(
                    (b for b in reversed(self.app.tool_blocks)
                     if b.call_id == call_id and b.status == "running"),
                    None,
                )
            if block is None:
                block = next(
                    (b for b in reversed(self.app.tool_blocks)
                     if b.name == name and b.status == "running"),
                    None,
                )
            if block is not None:
                block.status = "denied" if result.startswith("DENIED") else (
                    "error" if error else "done"
                )
                block.result = result
        self.app.request_render()

    def on_usage(self, usage: dict) -> None:
        # Accumulate across the whole session, not just the last turn.
        with self.app.lock:
            prev = self.app.last_usage or {}
            merged = dict(prev)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if usage.get(key):
                    merged[key] = int(merged.get(key, 0)) + int(usage[key])
            self.app.last_usage = merged
            # The real numbers are in, so the streamed-character estimate
            # for this model call is no longer needed.
            self.app._in_flight_chars = 0
        self.app.request_render()

    def on_approve(self, description: str) -> bool:
        # For file edits, render the actual change as a diff and attach it to
        # the approval prompt so the user sees what they're approving.
        tool, args = _last_edit(self.app)
        if tool in ("edit_file",) and isinstance(args, dict):
            from diff import make_diff
            from tools.fs_tools import preview_edit

            path = args.get("path", "")
            old, new, err = preview_edit(
                path, args.get("old_str", ""), args.get("new_str", "")
            )
            if not err:
                diff_lines = make_diff(path, old, new)
                if diff_lines:
                    with self.app.lock:
                        self.app.blocks.append(
                            DiffBlock(path=path, diff_lines=diff_lines)
                        )
                    return self.app.request_approval(description, diff_block=True)
        return self.app.request_approval(description)

    def on_warning(self, message: str) -> None:
        with self.app.lock:
            self.app.blocks.append(SystemBlock(text=message, kind="warn"))
        self._touch()


class InputEditor:
    """A small multi-line text editor with cursor + history."""

    def __init__(self) -> None:
        self.buffer: list[str] = [""]
        self.row = 0
        self.col = 0
        self.history: list[str] = []
        self.hist_index: int = -1  # -1 means "current input, not history"
        self._draft = ""

    @property
    def text(self) -> str:
        return "\n".join(self.buffer)

    def _clamp_col(self) -> None:
        self.col = max(0, min(self.col, len(self.buffer[self.row])))

    def insert(self, ch: str) -> None:
        line = self.buffer[self.row]
        self.buffer[self.row] = line[: self.col] + ch + line[self.col :]
        self.col += len(ch)
        self.hist_index = -1

    def newline(self) -> None:
        line = self.buffer[self.row]
        before, after = line[: self.col], line[self.col :]
        self.buffer[self.row] = before
        self.buffer.insert(self.row + 1, after)
        self.row += 1
        self.col = 0
        self.hist_index = -1

    def backspace(self) -> None:
        if self.col > 0:
            line = self.buffer[self.row]
            self.buffer[self.row] = line[: self.col - 1] + line[self.col :]
            self.col -= 1
        elif self.row > 0:
            prev = self.buffer[self.row - 1]
            self.col = len(prev)
            self.buffer[self.row - 1] = prev + self.buffer[self.row]
            del self.buffer[self.row]
            self.row -= 1
        self.hist_index = -1

    def delete(self) -> None:
        line = self.buffer[self.row]
        if self.col < len(line):
            self.buffer[self.row] = line[: self.col] + line[self.col + 1 :]
        elif self.row < len(self.buffer) - 1:
            self.buffer[self.row] = line + self.buffer[self.row + 1]
            del self.buffer[self.row + 1]
        self.hist_index = -1

    def move_left(self) -> None:
        if self.col > 0:
            self.col -= 1
        elif self.row > 0:
            self.row -= 1
            self.col = len(self.buffer[self.row])

    def move_right(self) -> None:
        if self.col < len(self.buffer[self.row]):
            self.col += 1
        elif self.row < len(self.buffer) - 1:
            self.row += 1
            self.col = 0

    def move_up(self) -> bool:
        """Returns True if the cursor actually moved (else caller treats it as history)."""
        if self.row > 0:
            self.row -= 1
            self._clamp_col()
            return True
        return False

    def move_down(self) -> bool:
        if self.row < len(self.buffer) - 1:
            self.row += 1
            self._clamp_col()
            return True
        return False

    def home(self) -> None:
        self.col = 0

    def end(self) -> None:
        self.col = len(self.buffer[self.row])

    def kill_to_end(self) -> None:
        self.buffer[self.row] = self.buffer[self.row][: self.col]

    def kill_to_start(self) -> None:
        self.buffer[self.row] = self.buffer[self.row][self.col :]
        self.col = 0

    def delete_word_back(self) -> None:
        """Erase the word before the caret (Ctrl+Backspace / Ctrl+W).

        At the start of a line this behaves like backspace and merges the
        line up, so the key keeps working across a paragraph break.
        """
        line = self.buffer[self.row]
        if self.col == 0:
            self.backspace()
            return
        # Skip back over trailing spaces, then back over the word itself.
        start = self.col
        while start > 0 and line[start - 1] == " ":
            start -= 1
        while start > 0 and line[start - 1] != " ":
            start -= 1
        self.buffer[self.row] = line[:start] + line[self.col :]
        self.col = start
        self.hist_index = -1

    def history_up(self) -> None:
        if not self.history:
            return
        if self.hist_index == -1:
            self._draft = self.text
            self.hist_index = len(self.history)
        if self.hist_index > 0:
            self.hist_index -= 1
            self._load(self.history[self.hist_index])

    def history_down(self) -> None:
        if self.hist_index == -1:
            return
        if self.hist_index >= len(self.history) - 1:
            self.hist_index = -1
            self._load(self._draft)
        else:
            self.hist_index += 1
            self._load(self.history[self.hist_index])

    def _load(self, text: str) -> None:
        self.buffer = text.split("\n") if text else [""]
        self.row = 0
        self.col = len(self.buffer[0])

    def submit(self) -> str:
        text = self.text
        if text.strip():
            self.history.append(text)
        self.hist_index = -1
        self.buffer = [""]
        self.row = 0
        self.col = 0
        return text


class TuiApp:
    def __init__(self, client: LLMClient, config: Config) -> None:
        self.client = client
        self.config = config
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self.blocks: list[Block] = []
        self.tool_blocks: list[ToolBlock] = []
        self.input = InputEditor()
        self.lock = threading.RLock()
        self.render_queue: queue.Queue = queue.Queue()
        self.running = True
        self.busy = False
        self.autoscroll = True
        self.scroll_offset = 0  # lines from the bottom; 0 = pinned to newest
        # Scroll geometry from the last render, so the key handler can clamp
        # without re-rendering the whole transcript on every keystroke.
        self._scroll_total = 0
        self._scroll_view = 3
        self.status = "idle"
        self.show_todos = True
        self.auto_approve = config.auto_approve
        self.last_usage: dict | None = None
        # Characters streamed by the model call in flight, used to estimate
        # its tokens before the endpoint bills us for them (see _header).
        self._in_flight_chars = 0
        # Messages typed while a turn is in flight. Starting a second worker
        # on the same `messages` list mid-turn would corrupt history, so these
        # wait for the current turn to finish.
        self._queued: list[str] = []
        # Shown in the input box when it's empty.
        self.placeholder = "ask anything…  (/ for commands)"
        # Popup menu state (slash command picker).
        self.menu: list[tuple[str, str]] = []
        self.menu_index = 0
        self.menu_visible = False

        # Session header: art on the left, stats on the right.
        self._add_banner()

        # Approval handoff between the worker thread and the input thread.
        self._approval_lock = threading.Lock()
        self._pending_approval: dict | None = None
        # Set by Ctrl+C to ask the worker to stop after the current request.
        self._interrupt = threading.Event()

        self.rows, self.cols = 24, 80
        self._frame = 0
        self.out = sys.stdout  # overridable for headless tests

    # ------------------------------------------------------------- status / busy

    def set_status(self, status: str) -> None:
        self.status = status
        self.busy = status not in ("idle",)
        self.request_render()

    def request_render(self) -> None:
        try:
            self.render_queue.put_nowait(True)
        except queue.Full:
            pass

    # ------------------------------------------------------------- session header

    def _add_banner(self) -> None:
        """Prepend the session header panel (art + stats)."""
        from tools import IMPLEMENTATIONS

        self.blocks.insert(0, BannerBlock(
            model=self.config.model,
            base_url=self.config.base_url,
            cwd=os.getcwd(),
            tool_count=len(_BUILTIN_TOOL_NAMES),
            plugin_names=tuple(sorted(
                n for n in IMPLEMENTATIONS if n not in _BUILTIN_TOOL_NAMES
            )),
            yolo=self.auto_approve,
        ))

    # ------------------------------------------------------------- approvals

    def request_approval(self, description: str, diff_block: bool = False) -> bool:
        """Called from the worker thread: block until the user answers."""
        if self.auto_approve:
            with self.lock:
                self.blocks.append(
                    SystemBlock(text=f"auto-approved: {description}", kind="warn")
                )
            self.request_render()
            return True

        event = threading.Event()
        approval = {
            "description": description,
            "event": event,
            "result": False,
        }
        with self._approval_lock:
            self._pending_approval = approval
        self.request_render()
        # Wait without hogging the GIL; the UI thread will set the event.
        while not event.wait(timeout=0.1):
            if not self.running or self._interrupt.is_set():
                # The event never fired, so the UI thread hasn't answered --
                # the approval is still ours to retract. (Checking the object
                # identity guards against racing an answer that landed between
                # the wait() timeout and this lock.)
                with self._approval_lock:
                    if self._pending_approval is approval:
                        self._pending_approval = None
                self._mark_diff_block(False, diff_block)
                return False
        with self._approval_lock:
            result = self._pending_approval.get("result", False) if self._pending_approval else False
            self._pending_approval = None
        self._mark_diff_block(result, diff_block)
        self.request_render()
        return result

    def _mark_diff_block(self, approved: bool, was_diff: bool = True) -> None:
        """Resolve the DiffBlock that accompanied this approval, if any."""
        if not was_diff:
            return
        with self.lock:
            for block in reversed(self.blocks):
                if isinstance(block, DiffBlock) and not block.decided:
                    block.decided = "approved" if approved else "rejected"
                    break

    def _answer_approval(self, approved: bool) -> None:
        with self._approval_lock:
            pending = self._pending_approval
            if pending is None:
                return
            pending["result"] = approved
            pending["event"].set()
        with self.lock:
            verb = "approved" if approved else "denied"
            kind = "info" if approved else "warn"
            self.blocks.append(
                SystemBlock(text=f"{verb}: {pending['description']}", kind=kind)
            )

    # ------------------------------------------------------------- rendering

    def _all_lines(self, width: int) -> list[str]:
        """Render every block to display lines."""
        out: list[str] = []
        with self.lock:
            snapshot = list(self.blocks)
        for block in snapshot:
            out.extend(block.render(width))
        return out

    def render(self) -> None:
        self.rows, self.cols = _get_size()
        self.rows = max(self.rows, 8)
        width = max(self.cols - 2, 10)

        # The transcript gets whatever space is left after header/menu/input/status.
        # The input region is its rule line plus the editor body.
        input_region_h = self._input_height() + 1
        header_h = 1
        status_h = 1
        menu_h = len(self._menu_lines(width)) if self.menu_visible else 0
        transcript_h = max(
            self.rows - header_h - input_region_h - status_h - menu_h, 3
        )

        lines = self._all_lines(width)
        # Flip a switch for live todos when the agent is using them.
        if self.show_todos and get_todos():
            todo_lines = TodoBlock().render(width)
            if todo_lines:
                lines.extend(todo_lines)

        total = len(lines)
        # autoscroll is the pin to the bottom. While it's on the view tracks
        # the newest lines no matter what arrives -- the offset is measured
        # from the end, so 0 already follows growth. When it's off, clamp to
        # what actually exists so a shrinking transcript (or a narrow one)
        # can't leave us staring past the top.
        max_offset = max(0, total - transcript_h)
        if self.autoscroll:
            self.scroll_offset = 0
        else:
            self.scroll_offset = min(self.scroll_offset, max_offset)
        # Remember the geometry for the scroll keys and the status bar.
        self._scroll_total, self._scroll_view = total, transcript_h

        visible = lines[total - transcript_h - self.scroll_offset :]
        visible = visible[:transcript_h]
        # Pad so the input box always sits at the same place.
        while len(visible) < transcript_h:
            visible.insert(0, "")

        out = []
        out.append(self._header(width))
        out.extend(visible)
        # The command menu floats directly above the input box.
        menu = self._menu_lines(width)
        out.extend(menu)
        # The input box is several rows; flatten it so each visual row is
        # its own entry and the status bar lands below it, not on top of it.
        out.extend(self._input_box_lines(width, self._input_height()))
        out.append(self._status_bar(width))
        self._frame += 1

        buf = []
        # Repaint in place rather than clearing the whole screen each frame:
        # a full 2J on every keystroke/spinner tick makes most terminals
        # flicker badly. Positioning + per-line clear is enough.
        buf.append(hide_cursor())
        for i, line in enumerate(out):
            buf.append(move_to(i + 1, 1))
            buf.append(clear_line())
            buf.append(line)
        # Show the cursor inside the input box at the caret position.
        # +1 for the rule line above the input body, +1 for 1-indexing.
        box_top = header_h + transcript_h + 1
        caret_row = box_top + 1 + (self.input.row - max(0, self.input.row - self._input_height() + 1))
        buf.append(move_to(caret_row, self._caret_col() + 1))
        buf.append(show_cursor())
        self._write("".join(buf))

    def _write(self, data: str) -> None:
        """Write the render buffer. Overridable for headless testing."""
        try:
            self.out.write(data)
            self.out.flush()
        except (ValueError, OSError):
            # A closed/redirected stream shouldn't kill the UI thread.
            pass

    def _header(self, width: int) -> str:
        T = _theme()
        cwd = os.path.basename(os.getcwd())
        model = self.config.model
        left = (
            f"{BOLD}{T.primary}AERO{RESET}{T.primary_dim}·agent{RESET} "
            f"{T.muted}{model}{RESET}"
        )
        usage = ""
        if self.last_usage:
            total = int(self.last_usage.get("total_tokens", 0) or 0)
            prompt = int(self.last_usage.get("prompt_tokens", 0) or 0)
            completion = int(self.last_usage.get("completion_tokens", 0) or 0)
            if total or prompt or completion:
                total = total or prompt + completion
                # A turn in flight hasn't been billed yet, so estimate its
                # tokens from what's streamed and mark the whole count with
                # "~" -- it's a live guess, not a reported number. The real
                # usage replaces it the moment the endpoint sends one.
                estimate = self._in_flight_chars // 4
                if estimate:
                    shown = f"~{_fmt_tokens(total + estimate)}"
                else:
                    shown = _fmt_tokens(total)
                detail = ""
                if prompt and completion:
                    detail = f" ({_fmt_tokens(prompt)}↑ {_fmt_tokens(completion)}↓)"
                usage = f" {T.faint}·{RESET} {T.muted}{shown} tok{detail}{RESET}"
        right = f"{T.muted}{cwd}{RESET}{usage}"
        gap = max(width - text_width(strip_ansi(left)) - text_width(strip_ansi(right)), 1)
        return left + " " * gap + right

    def _input_height(self) -> int:
        return min(max(len(self.input.buffer), 1), 6)

    def _input_scroll(self, width: int) -> int:
        """How far the caret's line is scrolled left, in characters.

        A line longer than the terminal is wide would wrap at the terminal
        level and shift the whole layout down a row, so the caret's own line
        scrolls horizontally to keep it on screen. Other lines are clipped to
        the width instead.
        """
        avail = max(width - len("> "), 1)
        line = self.input.buffer[self.input.row]
        if len(line) <= avail:
            return 0
        # Keep a little lookahead so typing at the right edge isn't jumpy.
        return max(0, self.input.col - avail + 8)

    def _caret_col(self) -> int:
        """The caret's column on screen, after horizontal scrolling."""
        width = max(self.cols - 2, 10)
        return len("> ") + self.input.col - self._input_scroll(width)

    def _input_box(self, width: int, height: int) -> str:
        """A bordered input panel with a caret and a placeholder hint."""
        T = _theme()
        prompt = f"{T.accent}>{RESET} "
        pad = " " * (len("> "))
        avail = max(width - len("> "), 1)
        scroll = self._input_scroll(width)

        start = max(0, self.input.row - height + 1)
        shown = self.input.buffer[start : start + height]

        lines = []
        # Any content at all -- including a lone space or tab -- hides the
        # placeholder, so it never sits on top of whitespace the user typed.
        empty = not self.input.text
        for i, line in enumerate(shown):
            display = line.replace("\t", "    ")
            # The caret's row scrolls with the caret; every other row is
            # clipped to the available width.
            if start + i == self.input.row:
                display = display[scroll : scroll + avail]
            else:
                display = display[:avail]
            if i == 0:
                if empty:
                    lines.append(
                        f"{prompt}{T.faint}{self.placeholder}{RESET}"
                    )
                else:
                    lines.append(f"{prompt}{display}")
            else:
                lines.append(f"{pad}{display}")
        while len(lines) < height:
            lines.append(pad)
        body = "\n".join(lines)
        rule = f"{T.primary_dim}{'─' * width}{RESET}\n"
        return rule + body

    def _input_box_lines(self, width: int, height: int) -> list[str]:
        """The input box as one entry per visual row (for the write loop)."""
        return self._input_box(width, height).split("\n")

    def _status_bar(self, width: int) -> str:
        T = _theme()
        with self._approval_lock:
            pending = self._pending_approval
        if pending:
            text = (
                f"{T.warning}{BOLD}approve?{RESET} {pending['description']}  "
                f"{T.faint}[{RESET}{T.success}y{RESET}{T.faint}/{RESET}"
                f"{T.danger}n{RESET}{T.faint}]{RESET}"
            )
            return _truncate_visual(text, width)

        if self.busy:
            spinner = SPINNER[self._frame % len(SPINNER)]
            status = {"thinking": "thinking…"}.get(self.status, self.status)
            held = len(self._queued)
            if held:
                status = f"{status} · {held} queued"
            left = f"{T.primary}{spinner}{RESET} {T.muted}{status}{RESET}"
        elif self.auto_approve:
            left = (
                f"{T.warning}⚠ yolo mode{RESET} {T.muted}(auto-approve on){RESET}"
            )
        else:
            left = f"{T.muted}ready{RESET}"
        if self.scroll_offset > 0 and self._scroll_total > self._scroll_view:
            # How far through the history the top of the viewport sits, as a
            # percentage of the scrollable range: 0 is pinned to the newest.
            span = self._scroll_total - self._scroll_view
            pct = round((span - self.scroll_offset) / span * 100)
            hint = (
                f"{T.faint}history {pct}% · shift+↑/↓ scrolls · "
                f"ctrl+l back to newest{RESET}"
            )
        else:
            hint = (
                f"{T.faint}enter send · ↑/↓ scroll · ctrl+p/n history · "
                f"esc interrupt · / for commands{RESET}"
            )
        # A status bar longer than the row would wrap at the terminal level
        # and shove the input box down a line, so clip to the width: first
        # the hint, then -- if even the status alone doesn't fit -- it.
        left_w = text_width(strip_ansi(left))
        if left_w >= width - 1:
            return _truncate_visual(left, width)
        room = width - left_w - 1
        if text_width(strip_ansi(hint)) > room:
            hint = _truncate_visual(hint, room)
        gap = max(width - left_w - text_width(strip_ansi(hint)), 1)
        return left + " " * gap + hint

    # ------------------------------------------------------------- command menu

    def _update_menu(self) -> None:
        """Recompute the slash-command popup from the current input line."""
        line = self.input.buffer[self.input.row]
        if line.startswith("/") and " " not in line:
            prefix = line
            self.menu = [
                (name, desc) for name, desc in SLASH_COMMANDS.items()
                if name.startswith(prefix)
            ]
            self.menu.sort()
            if self.menu_index >= len(self.menu):
                self.menu_index = 0
            self.menu_visible = bool(self.menu)
        else:
            self.menu = []
            self.menu_visible = False
        self.request_render()

    def _menu_lines(self, width: int) -> list[str]:
        """Render the popup as a small floating list above the input box."""
        if not self.menu_visible:
            return []
        T = _theme()
        inner = max(width - 4, 2)
        out: list[str] = [f"{T.primary_dim}┌{RESET}"]
        for i, (name, desc) in enumerate(self.menu):
            selected = i == self.menu_index
            marker = f"{T.primary}▸{RESET}" if selected else " "
            label = f"{T.accent}{name}{RESET}" if not selected else \
                f"{BOLD}{T.primary}{name}{RESET}"
            row = f"{T.primary_dim}│{RESET}{marker}{label} {T.faint}{desc}{RESET}"
            out.append(_truncate_visual(row, width))
        out.append(f"{T.primary_dim}└{'─' * inner}{RESET}")
        return out

    # ------------------------------------------------------------- input loop

    def handle_key(self, key: KeyEvent) -> bool:
        """Process one key. Returns False to quit the app."""
        with self._approval_lock:
            pending = self._pending_approval

        # Approvals take over the keyboard while one is outstanding.
        if pending is not None and key.ch and key.ch.lower() in ("y", "n"):
            self._answer_approval(key.ch.lower() == "y")
            self.request_render()
            return True

        if key.name == "ctrl_c":
            if self.busy:
                self._request_interrupt(
                    "Interrupt requested — stopping after the current "
                    "request. Press Ctrl+C again to force-quit."
                )
                return True
            return False
        if key.name == "ctrl_d":
            if not self.input.text:
                return False
            self.input.delete()
            self.request_render()
            return True
        if key.name in ("esc",):
            if self.menu_visible:
                self.menu_visible = False
                self.request_render()
                return True
            if self.busy:
                # ESC interrupts the running request, same as Ctrl+C but
                # without the force-quit second press.
                self._request_interrupt("Interrupted with ESC.")
                return True
            # Idle: clear the draft so ESC works as a "cancel what I typed".
            if self.input.text:
                self.input = InputEditor()
                self.menu_visible = False
                self.request_render()
            return True

        if key.name == "enter":
            self.submit()
            self.request_render()
            return True
        # Shift+Enter inserts a newline. (Alt+Enter is kept as an alias for
        # the terminals where Shift+Enter isn't distinguishable from Enter.)
        if key.name in ("shift_enter", "alt_enter"):
            self.input.newline()
            self.request_render()
            return True
        if key.name == "backspace":
            self.input.backspace()
            self._update_menu()
            self.request_render()
            return True
        if key.name == "delete":
            self.input.delete()
            self.request_render()
            return True
        if key.name == "left":
            self.input.move_left()
            self.request_render()
            return True
        if key.name == "right":
            self.input.move_right()
            self.request_render()
            return True
        if key.name == "up":
            if self.menu_visible:
                self.menu_index = (self.menu_index - 1) % len(self.menu)
                self.request_render()
                return True
            # In a multi-line draft the arrows still move the caret between
            # rows. A single-line draft has nowhere to go, so the key scrolls
            # the transcript instead -- it's what the chat is for.
            if self.input.move_up():
                self.request_render()
            else:
                self._scroll_by(1)
            return True
        if key.name == "down":
            if self.menu_visible:
                self.menu_index = (self.menu_index + 1) % len(self.menu)
                self.request_render()
                return True
            if self.input.move_down():
                self.request_render()
            else:
                self._scroll_by(-1)
            return True
        # History recall. Plain arrows used to do this, which made the chat
        # unscrollable from the keyboard; Ctrl+P/N is the readline binding and
        # arrives as a plain control char, so it works on every terminal
        # including the Windows console, where modified arrows don't.
        if key.name == "ctrl_p":
            self.input.history_up()
            self.request_render()
            return True
        if key.name == "ctrl_n":
            self.input.history_down()
            self.request_render()
            return True
        if key.name == "home":
            self.input.home()
            self.request_render()
            return True
        if key.name == "end":
            self.input.end()
            self.request_render()
            return True
        if key.name == "pageup":
            self._scroll_by(10)
            return True
        if key.name == "pagedown":
            self._scroll_by(-10)
            return True
        # Shift+arrows always scroll a line, even inside a multi-line draft
        # where the plain arrows are moving the caret. Ctrl+arrows are the
        # same on terminals that send them.
        if key.name in ("shift_up", "ctrl_up"):
            self._scroll_by(1)
            return True
        if key.name in ("shift_down", "ctrl_down"):
            self._scroll_by(-1)
            return True
        # Home/End jump the view to the oldest / newest history while their
        # plain forms keep moving the caret, same as the arrows above.
        if key.name in ("shift_home", "ctrl_home"):
            self._scroll_to(-1)
            return True
        if key.name in ("shift_end", "ctrl_end"):
            self._scroll_to(0)
            return True
        if key.name in ("ctrl_l",):
            self.scroll_offset = 0
            self.autoscroll = True
            self.request_render()
            return True
        if key.name == "ctrl_u":
            self.input.kill_to_start()
            self.request_render()
            return True
        if key.name == "ctrl_k":
            self.input.kill_to_end()
            self.request_render()
            return True
        # Ctrl+Backspace and Ctrl+W both erase the word behind the caret.
        # Terminals encode Ctrl+Backspace several different ways (see term.py),
        # so both names route to the same behavior.
        if key.name in ("ctrl_backspace", "ctrl_w"):
            self.input.delete_word_back()
            self._update_menu()
            self.request_render()
            return True

        if key.name == "tab":
            if self.menu_visible and self.menu:
                chosen = self.menu[self.menu_index][0]
                self.input.buffer[self.input.row] = chosen + " "
                self.input.col = len(chosen) + 1
                self._update_menu()
                self.request_render()
                return True
            self._autocomplete()
            self.request_render()
            return True

        if key.ch:
            self.input.insert(key.ch)
            if key.ch == "/":
                self._update_menu()
            elif self.menu_visible:
                self._update_menu()
            self.request_render()
            return True
        return True

    def _scroll_by(self, delta: int) -> None:
        """Move the transcript view by `delta` lines (negative = newer).

        Clamps against the geometry the last render measured, so the offset
        can't outrun the content, and pins back to the bottom once it lands
        there -- autoscroll is what keeps the view tracking new output.
        """
        max_offset = max(0, self._scroll_total - self._scroll_view)
        self.scroll_offset = max(0, min(self.scroll_offset + delta, max_offset))
        self.autoscroll = self.scroll_offset == 0
        self.request_render()

    def _scroll_to(self, offset: int) -> None:
        """Jump to a scroll position: 0 is newest, -1 is the very top."""
        max_offset = max(0, self._scroll_total - self._scroll_view)
        target = max_offset if offset < 0 else min(offset, max_offset)
        self.scroll_offset = target
        self.autoscroll = target == 0
        self.request_render()

    def _autocomplete(self) -> None:
        line = self.input.buffer[self.input.row]
        if not line.startswith("/"):
            # Insert a tab when not completing a command.
            self.input.insert("    ")
            return
        candidates = [c for c in SLASH_COMMANDS if c.startswith(line)]
        if len(candidates) == 1:
            self.input.buffer[self.input.row] = candidates[0] + " "
            self.input.col = len(candidates[0]) + 1
        elif candidates:
            with self.lock:
                self.blocks.append(
                    SystemBlock(text="  ".join(candidates), kind="info")
                )
            self.autoscroll = True

    # ------------------------------------------------------------- commands

    def submit(self) -> None:
        text = self.input.submit().strip()
        if not text:
            return
        if text.startswith("/"):
            if self._run_slash_command(text):
                return
        with self.lock:
            self.blocks.append(UserBlock(text=text, queued=self.busy))
        self.autoscroll = True
        if self.busy:
            # A turn is in flight: hold this until it lands. Running two
            # workers on the same history at once would interleave their
            # edits to `messages` and corrupt the conversation. The block
            # is already on screen, greyed out, so the user can see it
            # was heard rather than silently swallowed.
            with self.lock:
                self._queued.append(text)
            self.request_render()
            return
        self._dispatch(text)

    def _dispatch(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self._interrupt.clear()
        threading.Thread(target=self._run_agent, daemon=True).start()

    def _request_interrupt(self, message: str) -> None:
        """Ask the worker to stop after the current request."""
        self._interrupt.set()
        self.set_status("interrupting…")
        with self.lock:
            self.blocks.append(SystemBlock(text=message, kind="warn"))
        self.request_render()

    def _dispatch_next(self) -> None:
        """Send the next held message, or return to idle."""
        if not self.running:
            self.set_status("idle")
            return
        with self.lock:
            next_text = self._queued.pop(0) if self._queued else None
        if next_text is None:
            self.set_status("idle")
            self.request_render()
            return
        self._unqueue(next_text)
        self._dispatch(next_text)

    def _unqueue(self, text: str) -> None:
        """Mark a held message as actually sent: it stops being greyed out.

        The queue is FIFO, so the oldest greyed block is the one going out;
        matching on the text first keeps two identical messages in order.
        """
        with self.lock:
            target = None
            for block in self.blocks:
                if isinstance(block, UserBlock) and block.queued:
                    if block.text == text:
                        target = block
                        break
                    if target is None:
                        target = block
            if target is not None:
                target.queued = False
        self.autoscroll = True

    def _drop_queue(self) -> None:
        """A turn died with messages still held: they will never be sent."""
        with self.lock:
            for block in self.blocks:
                if isinstance(block, UserBlock) and block.queued:
                    block.queued = False
                    block.dropped = True
        self._queued.clear()

    def _run_slash_command(self, text: str) -> bool:
        """Returns True if the input was consumed as a command."""
        parts = shlex.split(text)
        cmd = parts[0]
        rest = parts[1:]

        def say(msg: str, kind: str = "info") -> None:
            with self.lock:
                self.blocks.append(SystemBlock(text=msg, kind=kind))
            self.autoscroll = True

        if cmd in ("/exit", "/quit"):
            self.running = False
            return True
        if cmd == "/help":
            lines = ["termagent commands", ""]
            for name, desc in sorted(SLASH_COMMANDS.items()):
                lines.append(f"  {FG.BRIGHT_CYAN}{name:<10}{RESET} {desc}")
            lines.append("")
            lines.append(
                "  ↑/↓ or PageUp/PageDown scrolls the transcript (ctrl+l jumps\n"
                "  to the newest lines) · ctrl+p/ctrl+n recalls what you typed\n"
                "  · Tab completes commands"
            )
            with self.lock:
                self.blocks.append(SystemBlock(text="\n".join(lines)))
            self.autoscroll = True
            return True
        if cmd == "/clear":
            if self.busy:
                say("wait for the current turn to finish before /clear", kind="warn")
                return True
            self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            with self.lock:
                self.blocks = []
                self.tool_blocks = []
                self._add_banner()
            say("conversation cleared")
            return True
        if cmd == "/expand":
            with self.lock:
                changed = False
                for block in self.blocks:
                    if isinstance(block, ReasoningBlock) and block.text:
                        block.collapsed = not block.collapsed
                        changed = True
                if not changed:
                    say("(no reasoning to expand yet)")
                    return True
            say("reasoning " + ("expanded" if not any(
                isinstance(b, ReasoningBlock) and b.collapsed for b in self.blocks
            ) else "collapsed"))
            return True
        if cmd == "/yolo":
            self.auto_approve = not self.auto_approve
            say(
                f"auto-approve {'ON — writes/edits/shell run without asking' if self.auto_approve else 'OFF'}",
                kind="warn" if self.auto_approve else "info",
            )
            return True
        if cmd == "/todos":
            self.show_todos = not self.show_todos
            say(f"todo panel {'shown' if self.show_todos else 'hidden'}")
            return True
        if cmd == "/model":
            try:
                models = self.client.list_models()
            except Exception as e:  # noqa: BLE001
                say(f"could not list models: {e}", kind="error")
                return True
            say(
                "configured: "
                f"{self.config.model}\navailable: {', '.join(models) or '(none listed)'}"
            )
            return True
        if cmd == "/plugins":
            from tools import IMPLEMENTATIONS

            custom = sorted(
                n for n in IMPLEMENTATIONS
                if n not in {
                    "read_file", "write_file", "edit_file", "list_dir",
                    "glob_search", "grep_search", "run_shell", "fetch_url",
                    "read_image", "todo_write", "todo_read",
                }
            )
            say("plugins: " + (", ".join(custom) if custom else "(none loaded)"))
            return True
        if cmd == "/save":
            if not rest:
                say("usage: /save <name>", kind="warn")
                return True
            from cli import SESSIONS_DIR

            SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            out = SESSIONS_DIR / f"{rest[0]}.json"
            out.write_text(json.dumps(self.messages, indent=2))
            say(f"saved to {out}")
            return True
        if cmd == "/load":
            if not rest:
                say("usage: /load <name>", kind="warn")
                return True
            if self.busy:
                say("wait for the current turn to finish before /load", kind="warn")
                return True
            from cli import SESSIONS_DIR

            src = SESSIONS_DIR / f"{rest[0]}.json"
            if not src.exists():
                say(f"no saved session at {src}", kind="error")
                return True
            self.messages = json.loads(src.read_text())
            say(f"loaded {len(self.messages)} messages from {src}")
            return True
        say(f"unknown command: {cmd} (try /help)", kind="warn")
        return True

    # ------------------------------------------------------------- agent thread

    def _run_agent(self) -> None:
        self._interrupt.clear()
        self.set_status("thinking")
        cb = TuiCallbacks(self)
        try:
            reply = run_turn(
                self.client, self.messages, cb, self.config.max_iterations,
                stream=True, temperature=self.config.temperature,
                interrupt=self._interrupt,
            )
        except Exception as e:  # noqa: BLE001 - never let the worker kill the UI
            with self.lock:
                self.blocks.append(SystemBlock(text=f"agent crashed: {e}", kind="error"))
            # Held messages can never go out now; mark them so they don't
            # sit greyed with no turn left to carry them.
            self._drop_queue()
            self.set_status("idle")
            self.request_render()
            return
        if self._interrupt.is_set():
            with self.lock:
                self.blocks.append(SystemBlock(
                    text="[interrupted by user]", kind="warn",
                ))
            # Don't echo a partial reply as if it were final, but still send
            # anything the user queued behind the interrupted turn.
            self._dispatch_next()
            return
        # Streaming already carried the reply into an AssistantBlock; mark it
        # final and DON'T append a second copy (that was the duplicate echo).
        # Only append when nothing was streamed (e.g. an error message).
        with self.lock:
            last = self.blocks[-1] if self.blocks else None
            if isinstance(last, AssistantBlock):
                last.done = True
            elif reply:
                self.blocks.append(AssistantBlock(text=reply, done=True))
        self._dispatch_next()

    # ------------------------------------------------------------- main loop

    def run(self, raw_input) -> None:
        # One full clear on entry; after that every frame repaints in place.
        self._write(clear_screen())
        self.request_render()
        last_size = (0, 0)
        while self.running:
            # Terminal resize forces a full redraw.
            try:
                size = _get_size()
            except Exception:  # noqa: BLE001
                size = last_size
            if size != last_size:
                last_size = size
                self.rows, self.cols = size
                self.request_render()

            # Drain every pending render request, then draw once.
            drew = False
            while True:
                try:
                    self.render_queue.get_nowait()
                    drew = True
                except queue.Empty:
                    break
            if drew:
                self.render()

            # Non-blocking key read; the spinner animates on the tick below.
            key = raw_input.read_key()
            if key is not None:
                if not self.handle_key(key):
                    break
                continue

            # Keep the spinner alive while waiting on the model.
            if self.busy:
                self.request_render()
            time.sleep(0.03)


def _get_size() -> tuple[int, int]:
    return get_size()


def run_tui(client: LLMClient, config: Config) -> None:
    """Entry point: take over the terminal, run until the user quits."""
    from term import enable_windows_ansi

    enable_windows_ansi()

    # Enter the alternate screen buffer: this is a separate buffer with no
    # scrollback, so our per-frame repaints don't pile up in the user's real
    # terminal history (that's why scrolling up showed the answer duplicated
    # many times). Leaving restores the primary buffer -- and the shell
    # history behind it -- untouched.
    sys.stdout.write(alt_screen(True))
    sys.stdout.flush()

    app = TuiApp(client, config)
    with RawInput() as raw:
        try:
            app.run(raw)
        finally:
            # Return to the primary buffer and leave a clean prompt line.
            sys.stdout.write(alt_screen(False))
            sys.stdout.write(clear_screen() + move_to(1, 1) + show_cursor())
            sys.stdout.flush()
