#!/usr/bin/env python3
"""termagent: a terminal coding agent. Run `python cli.py` in your project directory."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from term import FG, RESET

# On Windows the default console encoding (cp1252) can't represent the box /
# arrow characters the UI uses; force UTF-8 before anything prints.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass

from agent import SYSTEM_PROMPT, Callbacks, run_turn
from config import Config
from llm_client import LLMClient
from tools import load_plugins

SESSIONS_DIR = Path(".termagent") / "sessions"

HELP = """
Slash commands:
  /exit, /quit          leave
  /clear                reset conversation history
  /save <name>          save this session to .termagent/sessions/<name>.json
  /load <name>          load a previously saved session
  /plugins              list currently loaded plugin tools
  /todos                show the agent's current task list
  /yolo                 toggle auto-approve for writes, edits, and shell
  /model                show the configured model and what the endpoint offers
  /tui                  switch to the full-screen terminal UI
  /help                 show this message

Anything else is sent to the agent as a normal message.
""".strip()


def _confirm(prompt: str) -> bool:
    try:
        answer = input(f"\n  approve? [y/N] {prompt}\n  > ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def _run_repl_command(
    cmd: str,
    rest: str,
    *,
    messages: list[dict],
    client: "LLMClient",
    config: "Config",
    loaded: list[str],
    state: dict,
) -> str | bool:
    """
    Handle one line-REPL slash command.

    Returns "exit" to quit, True if the command was handled (and its output
    already printed), or False when `cmd` isn't a known command.
    """
    if cmd in ("/exit", "/quit"):
        return "exit"
    if cmd == "/help":
        print(HELP)
        return True
    if cmd == "/clear":
        messages[:] = [{"role": "system", "content": SYSTEM_PROMPT}]
        print("(history cleared)")
        return True
    if cmd == "/yolo":
        state["auto_approve"] = not state["auto_approve"]
        print(
            "auto-approve ON -- writes, edits, and shell commands run without "
            "asking"
            if state["auto_approve"]
            else "auto-approve OFF"
        )
        return True
    if cmd == "/plugins":
        print(", ".join(loaded) if loaded else "(no plugins loaded)")
        return True
    if cmd == "/todos":
        from tools.todo_tools import render_todos

        print(render_todos())
        return True
    if cmd == "/model":
        try:
            models = client.list_models()
        except Exception as e:  # noqa: BLE001
            print(f"could not list models: {e}")
            return True
        print(f"configured: {config.model}")
        print(f"available: {', '.join(models) if models else '(endpoint listed none)'}")
        return True
    if cmd == "/tui":
        from tui import run_tui

        run_tui(client, config)
        print(f"termagent -- model: {config.model}  base_url: {config.base_url}")
        return True
    if cmd == "/save":
        if not rest:
            print("usage: /save <name>")
            return True
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        out = SESSIONS_DIR / f"{rest}.json"
        out.write_text(json.dumps(messages, indent=2))
        print(f"saved to {out}")
        return True
    if cmd == "/load":
        if not rest:
            print("usage: /load <name>")
            return True
        src = SESSIONS_DIR / f"{rest}.json"
        if not src.exists():
            print(f"no saved session at {src}")
            return True
        loaded_messages = json.loads(src.read_text())
        if not isinstance(loaded_messages, list) or not loaded_messages:
            print("saved session is empty or malformed")
            return True
        messages[:] = loaded_messages
        print(f"loaded {src} ({len(messages)} messages)")
        return True
    return False


@dataclass
class LineCallbacks(Callbacks):
    """Streams events to a plain stdout REPL."""
    auto_approve: bool = False
    show_reasoning: bool = True

    def on_content_delta(self, text: str) -> None:
        print(text, end="", flush=True)

    def on_reasoning_delta(self, text: str) -> None:
        if not self.show_reasoning:
            return
        # Reasoning is shown dimmed so it reads as separate from the answer.
        print(f"\033[2m{text}\033[0m", end="", flush=True)

    def on_tool_start(self, name: str, args: dict, call_id: str = "") -> None:
        preview = json.dumps(args, ensure_ascii=False)
        if len(preview) > 120:
            preview = preview[:120] + "..."
        print(f"\n  \u2192 {name}({preview})", flush=True)

    def on_tool_result(self, name: str, result: str, error: bool, call_id: str = "") -> None:
        marker = "\u2717" if error else "\u2713"
        print(f"\n  {marker} {name}", flush=True)

    def on_usage(self, usage: dict) -> None:
        total = usage.get("total_tokens")
        if total:
            print(f"\033[2m  [tokens: {total}]\033[0m", flush=True)

    def on_approve(self, description: str) -> bool:
        if self.auto_approve:
            print(f"\033[2m  [auto-approved] {description}\033[0m", flush=True)
            return True
        print(flush=True)
        return _confirm(description)

    def on_status(self, status: str) -> None:
        # The line REPL has no status bar; only surface non-idle states.
        if status != "idle":
            print(f"\033[2m  [{status}]\033[0m", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="A terminal coding agent.")
    parser.add_argument("--api-key", default=None, help="overrides ATRIA_API_KEY")
    parser.add_argument("--base-url", default=None, help="overrides ATRIA_BASE_URL")
    parser.add_argument("--model", default=None, help="overrides ATRIA_MODEL")
    parser.add_argument(
        "--yolo", action="store_true",
        help="auto-approve file writes, edits, and shell commands (no confirmation prompts)",
    )
    parser.add_argument(
        "--tui", action="store_true",
        help="launch the full-screen terminal UI instead of the line-mode REPL",
    )
    parser.add_argument(
        "--max-iterations", type=int, default=None,
        help="tool-call rounds per turn before the loop stops (default 25)",
    )
    parser.add_argument(
        "--timeout", type=int, default=None,
        help="per-request timeout in seconds (default 120)",
    )
    parser.add_argument(
        "--temperature", type=float, default=None,
        help="sampling temperature, 0-2 (default 0.2)",
    )
    parser.add_argument("--plugins-dir", default="plugins", help="directory to load extra tools from")
    parser.add_argument("prompt", nargs="*", help="if given, run one-shot instead of an interactive REPL")
    args = parser.parse_args()

    config = Config.load({
        "api_key": args.api_key,
        "base_url": args.base_url,
        "model": args.model,
        "auto_approve": args.yolo,
        "max_iterations": args.max_iterations,
        "request_timeout": args.timeout,
        "temperature": args.temperature,
    })
    client = LLMClient(config.api_key, config.base_url, config.model, config.request_timeout)

    loaded = load_plugins(Path(args.plugins_dir))
    if loaded:
        print(f"Loaded {len(loaded)} plugin tool(s): {', '.join(loaded)}")

    if args.tui:
        # Full-screen UI takes over the terminal entirely.
        from tui import run_tui
        run_tui(client, config)
        return

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    if args.prompt:
        # One-shot mode: `python cli.py "fix the failing test in tests/test_x.py"`
        user_text = " ".join(args.prompt)
        messages.append({"role": "user", "content": user_text})
        reply = run_turn(
            client, messages, LineCallbacks(auto_approve=config.auto_approve),
            config.max_iterations, temperature=config.temperature,
        )
        print(flush=True)
        print(reply)
        return

    print(f"termagent -- model: {config.model}  base_url: {config.base_url}")
    print(f"  max_iterations={config.max_iterations}  timeout={config.request_timeout}s  "
          f"temperature={config.temperature}")
    print("Type /help for commands, /exit to quit.\n")

    state = {"auto_approve": config.auto_approve}
    # A green dot marks the user's turn in the line REPL too, matching the TUI.
    prompt = f"{FG.BRIGHT_GREEN}●{RESET} "

    while True:
        try:
            user_input = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            cmd, _, rest = user_input.partition(" ")
            rest = rest.strip()
            handled = _run_repl_command(
                cmd, rest, messages=messages, client=client, config=config,
                loaded=loaded, state=state,
            )
            if handled == "exit":
                break
            if handled:
                continue
            print(f"unknown command: {cmd} (try /help)")
            continue

        messages.append({"role": "user", "content": user_input})
        cb = LineCallbacks(auto_approve=state["auto_approve"])
        reply = run_turn(
            client, messages, cb, config.max_iterations,
            temperature=config.temperature,
        )
        print(f"\n\nagent> {reply}\n")


if __name__ == "__main__":
    sys.exit(main() or 0)
