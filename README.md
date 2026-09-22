<div align="center">

```
      _    _____ ____   ___
     / \  | ____|  _ \ / _ \
    / _ \ |  _| | |_) | | | |
   / ___ \| |___|  _ <| |_| |
  /_/   \_\_____|_| \_\\___/
```

**A zero-dependency terminal coding agent.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![No Dependencies](https://img.shields.io/badge/dependencies-zero-orange.svg)](#)

[Features](#features) · [Install](#install) · [Usage](#usage) · [Plugins](#plugins) · [Architecture](#architecture) · [Tests](#tests)

</div>

---

AERO is a terminal coding agent that gives an LLM direct access to your filesystem, shell, and the web. Plan → edit → run → verify → iterate — all from your terminal.

Built entirely on the Python standard library. No `pip install` of third-party packages required. Works with any OpenAI-compatible API endpoint.

## Features

- **Full-screen TUI** — session header, scrollable transcript, markdown rendering, collapsible reasoning, diff previews, multi-line input with history
- **11 built-in tools** — read/write/edit files, list/glob/grep search, shell execution, web fetching, image reading, todo planner
- **Diff-based approval** — every file edit shown as a colored diff you approve or reject before it touches disk
- **Streaming** — token-by-token output with live reasoning display
- **Plugin system** — drop a `.py` file into `plugins/` to add your own tools
- **Session management** — save/load conversations with `/save` and `/load`
- **Cross-platform** — Windows, macOS, Linux
- **Zero dependencies** — pure Python standard library

## Install

### Quick start (run from source)

```bash
git clone https://github.com/ronak-create/aero.git
cd aero
cp .env.example .env          # add your API key
python cli.py --tui           # launch the TUI
```

### Install as a command (`aero`)

```bash
git clone https://github.com/ronak-create/aero.git
cd aero
pip install -e .
aero --tui                    # now works from anywhere
```

### Using Make

```bash
make install                  # pip install -e .
make tui                      # launch TUI
make test                     # run test suite
```

Requires **Python 3.10+** (developed on 3.11).

## Configuration

Set your API key in `.env` or as an environment variable:

```env
ATRIA_API_KEY=atr_your_key_here
ATRIA_BASE_URL=https://api.atria-asi.ai/v1
ATRIA_MODEL=Atria-Dawn-Preview
```

All settings can be overridden via CLI flags:

```bash
aero --api-key <key> --base-url <url> --model <model>
aero --max-iterations 50 --timeout 180 --temperature 0.4
```

## Usage

### Full-screen TUI

```bash
aero --tui
```

```
┌──────────────────────────────────────────────────────────────────────────┐
│      _    _____ ____   ___       model     Atria-Dawn-Preview           │
│     / \  | ____|  _ \ / _ \      endpoint  api.atria-asi.ai             │
│    / _ \ |  _| | |_) | | | |     cwd       ~/my-project                │
│   / ___ \| |___|  _ <| |_| |     tools     11 builtin                  │
│  /_/   \_\_____|_| \_\\___/      plugins   none                        │
│                                   mode      confirm                     │
└──────────────────────────────────────────────────────────────────────────┘

● Refactor the auth module to use the new token format.
⌁ reasoning  The user wants to refactor auth… I should read it first.
  ✓ read_file  src/auth.py
> I've updated `src/auth.py` to use the new token format…
```

### Interactive REPL

```bash
aero
```

### One-shot (good for scripting / CI)

```bash
aero "fix the failing test in tests/test_parser.py"
```

### Auto-approve mode

```bash
aero --yolo                   # or /yolo inside the TUI
```

### Keyboard shortcuts (TUI)

| Key | Action |
|---|---|
| `Enter` | Send message |
| `Shift+Enter` | Newline (multi-line input) |
| `Esc` | Interrupt request / clear input |
| `↑` / `↓` | Scroll transcript |
| `Ctrl+P` / `Ctrl+N` | Input history |
| `Ctrl+U` / `Ctrl+K` | Delete to start/end of line |
| `Ctrl+L` | Jump to newest |
| `Tab` | Autocomplete `/` commands |
| `Ctrl+C` | Interrupt, then quit |

### Slash commands

`/help` · `/clear` · `/save <name>` · `/load <name>` · `/plugins` · `/todos` · `/yolo` · `/model` · `/tui` · `/context` · `/cost` · `/exit`

## Tools

| Tool | Description | Approval |
|---|---|---|
| `read_file` | Read files with line numbers | No |
| `write_file` | Create/overwrite files | **Yes** |
| `edit_file` | Exact-match find/replace | **Yes** |
| `list_dir` | List directory contents | No |
| `glob_search` | Recursive pattern matching | No |
| `grep_search` | Regex content search | No |
| `run_shell` | Execute shell commands | **Yes** |
| `fetch_url` | Fetch web pages as text | No |
| `read_image` | Load images for vision models | No |
| `todo_write` | Agent's planning scratchpad | No |
| `todo_read` | Read the task list | No |

## Plugins

Drop a `.py` file into `plugins/`:

```python
# plugins/my_tool.py
SCHEMA = {
    "type": "function",
    "function": {
        "name": "my_tool",
        "description": "Does something useful",
        "parameters": {
            "type": "object",
            "properties": {"input": {"type": "string"}},
            "required": ["input"],
        },
    },
}

def run(input: str) -> str:
    return f"processed: {input}"
```

Auto-discovered at startup. No registration needed.

## Architecture

```
cli.py          Entry point — REPL, one-shot, arg parsing, slash commands
tui.py          Full-screen UI — blocks, input editor, approvals, spinner
agent.py        Agent loop — messages → tool calls → execute → repeat
llm_client.py   HTTP client — streaming SSE, non-streaming, model resolution
config.py       Configuration — .env loader, env vars, CLI flag merging
term.py         Terminal layer — raw input, ANSI, screen size, wrapping
theme.py        Dawn color palette — truecolor + 16-color fallback
markdown.py     Markdown → ANSI renderer
diff.py         Colored unified diff renderer
tools/          Built-in tool schemas + implementations
plugins/        Drop-in user tools
tests/          Full test suite
```

## Tests

```bash
python run_tests.py -v        # stdlib runner
python -m pytest tests/       # if you have pytest
```

Covers: terminal layer, filesystem tools, diff/markdown rendering, LLM client (stubbed HTTP + SSE), plugin loader, agent loop (scripted fake model with approval gates, iteration caps, interrupts), and the TUI.

## License

[MIT](LICENSE) — Copyright (c) 2026 ronak-create
