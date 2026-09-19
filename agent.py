"""The agent loop: plan -> call tools -> feed results back -> repeat until done.

UIs plug in through a Callbacks object: the loop emits events (streaming token
deltas, tool starts/finishes, approvals) and the callback decides how to draw
them. A dumb line-mode callback prints to stdout; the TUI redraws a
full-screen transcript. Either way, the loop logic stays identical.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from llm_client import LLMClient, LLMError, accumulate_tool_calls
from tools import BUILTIN_TOOLS, DANGEROUS_TOOLS, IMPLEMENTATIONS

SYSTEM_PROMPT = """You are a terminal coding agent running in a user's local project directory.
You can read and write files, run shell commands, search code, fetch web pages, read images,
and track multi-step plans with the todo tools.

Guidelines:
- For anything beyond a trivial one-step request, write a short plan with todo_write before
  acting, and mark items in_progress/done with todo_write as you go.
- Prefer edit_file for small changes to existing files; use write_file only for new files.
- Read a file before editing it if you haven't seen its current contents this session.
- After making changes, verify them (e.g. run tests, run the code, or re-read the file) rather
  than assuming they worked.
- Be concise in your final response to the user: summarize what you did and why, don't restate
  every tool call.
- If a task is ambiguous, make a reasonable assumption, state it briefly, and proceed rather
  than stopping to ask, unless proceeding could cause real damage (e.g. destructive shell
  commands, deleting files, force-pushing).
"""


@dataclass
class Callbacks:
    """Override the hooks you care about; the defaults are all no-ops."""

    on_content_delta: Callable[[str], None] = lambda _t: None
    on_reasoning_delta: Callable[[str], None] = lambda _t: None
    # (name, args) when the model requests a tool, before it runs.
    on_tool_start: Callable[[str, dict], None] = lambda _n, _a: None
    # (name, result, error?) when the tool has finished.
    on_tool_result: Callable[[str, str, bool], None] = lambda _n, _r, _e: None
    # A transient status line ("thinking", "running X", ...).
    on_status: Callable[[str], None] = lambda _s: None
    # Ask the user to approve a destructive action. Must return True/False.
    on_approve: Callable[[str], bool] = lambda _d: False
    # Non-fatal problem (unknown tool, bad args, etc.).
    on_warning: Callable[[str], None] = lambda _w: None


def _describe_call(name: str, args: dict[str, Any]) -> str:
    if name == "run_shell":
        return f"run_shell: {args.get('command', '')}"
    if name == "write_file":
        return f"write_file: {args.get('path', '')} ({len(args.get('content', ''))} chars)"
    if name == "edit_file":
        return f"edit_file: {args.get('path', '')}"
    return f"{name}: {json.dumps(args)[:200]}"


def _truncate(text: str, limit: int = 4000) -> str:
    """Cap tool output so one verbose command can't eat the whole context."""
    if len(text) <= limit:
        return text
    keep = limit // 2
    return (
        text[:keep]
        + f"\n\n... [output truncated: {len(text) - limit} chars omitted] ...\n\n"
        + text[-keep:]
    )


def execute_tool(
    name: str, args: dict[str, Any], cb: Callbacks
) -> tuple[str, str | None]:
    """
    Runs a tool. Returns (text_result, image_data_uri_or_None).
    The image is surfaced separately so the loop can attach it as real vision
    content on the next message, not just as a string.
    """
    impl = IMPLEMENTATIONS.get(name)
    if impl is None:
        cb.on_warning(f"unknown tool '{name}'")
        return f"ERROR: unknown tool '{name}'", None

    if name in DANGEROUS_TOOLS and not cb.on_approve(_describe_call(name, args)):
        return "DENIED: user did not approve this action", None

    try:
        result = impl(**args)
    except TypeError as e:
        cb.on_warning(f"bad arguments for {name}: {e}")
        return f"ERROR: bad arguments for {name}: {e}", None
    except Exception as e:  # noqa: BLE001 - surface tool errors to the model, don't crash
        return f"ERROR running {name}: {e}", None

    if name == "read_image" and isinstance(result, str) and result.startswith("data:image"):
        return "Image loaded, see attached.", result
    return result, None


def _stream_turn(
    client: LLMClient, messages: list[dict[str, Any]], cb: Callbacks, temperature: float,
    interrupt: Any = None,
) -> tuple[str | None, list[dict[str, Any]], str | None]:
    """Run one streamed model call. Returns (content, tool_calls, reasoning)."""
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for delta in client.stream(messages, tools=BUILTIN_TOOLS, temperature=temperature):
        if delta["reasoning"]:
            reasoning_parts.append(delta["reasoning"])
            cb.on_reasoning_delta(delta["reasoning"])
        if delta["content"]:
            content_parts.append(delta["content"])
            cb.on_content_delta(delta["content"])
        if delta["tool_calls"]:
            accumulate_tool_calls(tool_calls, delta["tool_calls"])
        # Let Ctrl+C cut a long response short rather than reading it all.
        if interrupt is not None and interrupt.is_set():
            break

    content = "".join(content_parts) or None
    reasoning = "".join(reasoning_parts) or None
    return content, tool_calls, reasoning


def _blocked_turn(
    client: LLMClient, messages: list[dict[str, Any]], cb: Callbacks, temperature: float
) -> tuple[str | None, list[dict[str, Any]], str | None]:
    """Fallback when streaming is unavailable: one shot, emit as one delta."""
    try:
        response = client.chat(messages, tools=BUILTIN_TOOLS, temperature=temperature)
    except LLMError:
        raise

    if response.reasoning:
        cb.on_reasoning_delta(response.reasoning)
    if response.content:
        cb.on_content_delta(response.content)
    return response.content, response.tool_calls, response.reasoning


def run_turn(
    client: LLMClient,
    messages: list[dict[str, Any]],
    callbacks: Callbacks | None = None,
    max_iterations: int = 25,
    stream: bool = True,
    temperature: float = 0.2,
    interrupt: Any = None,
) -> str:
    """
    Runs the agent loop for one user turn (which may involve many tool calls)
    and returns the assistant's final text reply. `messages` is mutated in
    place so the caller keeps full conversation history across turns.

    `interrupt` is an optional threading.Event; if set, the loop stops after
    the current request instead of running further tool calls.
    """
    cb = callbacks or Callbacks()

    def interrupted() -> bool:
        return bool(interrupt is not None and interrupt.is_set())

    for _ in range(max_iterations):
        cb.on_status("thinking")
        try:
            if stream:
                content, tool_calls, _reasoning = _stream_turn(
                    client, messages, cb, temperature, interrupt
                )
            else:
                content, tool_calls, _reasoning = _blocked_turn(
                    client, messages, cb, temperature
                )
        except LLMError as e:
            return f"[error talking to model] {e}"

        if interrupted():
            # Still record what the model said before we stop.
            assistant_msg = {"role": "assistant", "content": content or ""}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)
            return content or "[interrupted]"

        # The assistant message must echo back the tool_calls it made so the
        # provider can match them to the results we return next.
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        if not tool_calls:
            cb.on_status("idle")
            return content or ""

        pending_image_messages: list[dict[str, Any]] = []
        for call in tool_calls:
            fn = call["function"]
            name = fn["name"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
                cb.on_warning(f"model sent invalid JSON arguments for {name}")

            cb.on_tool_start(name, args)
            result_text, image_uri = execute_tool(name, args, cb)
            cb.on_tool_result(
                name,
                result_text if not result_text.startswith(("ERROR", "DENIED")) else result_text,
                result_text.startswith(("ERROR", "DENIED")),
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "name": name,
                    "content": _truncate(result_text),
                }
            )

            if image_uri:
                pending_image_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": f"(image from {args.get('path')})"},
                            {"type": "image_url", "image_url": {"url": image_uri}},
                        ],
                    }
                )

        messages.extend(pending_image_messages)

    cb.on_status("idle")
    return "[stopped: reached max tool-call iterations for this turn]"
