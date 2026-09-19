#!/usr/bin/env python3
"""termagent: a terminal coding agent. Run `python cli.py` in your project directory."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

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


@dataclass
class LineCallbacks(Callbacks):
    """Streams events to a plain stdout REPL."""
    auto_approve: bool = False

    def on_content_delta(self, text: str) -> None:
        print(text, end="", flush=True)

    def on_reasoning_delta(self, text: str) -> None:
        # Reasoning is shown dimmed so it reads as separate from the answer.
        print(f"\033[2m{text}\033[0m", end="", flush=True)

    def on_tool_start(self, name: str, args: dict) -> None:
        preview = json.dumps(args)
        if len(preview) > 120:
            preview = preview[:120] + "..."
        print(f"\n  \u2192 {name}({preview})", flush=True)

    def on_tool_result(self, name: str, result: str, error: bool) -> None:
        if error:
            print(f"\n  \u2717 {name} failed", flush=True)

    def on_approve(self, description: str) -> bool:
        if self.auto_approve:
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
    parser.add_argument("--plugins-dir", default="plugins", help="directory to load extra tools from")
    parser.add_argument("prompt", nargs="*", help="if given, run one-shot instead of an interactive REPL")
    args = parser.parse_args()

    config = Config.load({
        "api_key": args.api_key,
        "base_url": args.base_url,
        "model": args.model,
        "auto_approve": args.yolo,
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
            config.max_iterations,
        )
        print(flush=True)
        print(reply)
        return

    print(f"termagent -- model: {config.model}  base_url: {config.base_url}")
    print("Type /help for commands, /exit to quit.\n")

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            cmd, _, rest = user_input.partition(" ")
            rest = rest.strip()

            if cmd in ("/exit", "/quit"):
                break
            if cmd == "/help":
                print(HELP)
                continue
            if cmd == "/clear":
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                print("(history cleared)")
                continue
            if cmd == "/plugins":
                print(", ".join(loaded) if loaded else "(no plugins loaded)")
                continue
            if cmd == "/tui":
                from tui import run_tui
                run_tui(client, config)
                print(f"termagent -- model: {config.model}  base_url: {config.base_url}")
                continue
            if cmd == "/save":
                if not rest:
                    print("usage: /save <name>")
                    continue
                SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
                out = SESSIONS_DIR / f"{rest}.json"
                out.write_text(json.dumps(messages, indent=2))
                print(f"saved to {out}")
                continue
            if cmd == "/load":
                if not rest:
                    print("usage: /load <name>")
                    continue
                src = SESSIONS_DIR / f"{rest}.json"
                if not src.exists():
                    print(f"no saved session at {src}")
                    continue
                messages = json.loads(src.read_text())
                print(f"loaded {src} ({len(messages)} messages)")
                continue

            print(f"unknown command: {cmd} (try /help)")
            continue

        messages.append({"role": "user", "content": user_input})
        cb = LineCallbacks(auto_approve=config.auto_approve)
        reply = run_turn(client, messages, cb, config.max_iterations)
        print(f"\n\nagent> {reply}\n")


if __name__ == "__main__":
    sys.exit(main() or 0)
