<div align="center">

<img src="assets/aero-banner.png" alt="AERO" width="800">

**A zero-dependency terminal coding agent powered by Atria Dawn Preview.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![No Dependencies](https://img.shields.io/badge/dependencies-zero-orange.svg)](#)

[Features](#features) · [Install](#install) · [Configuration](#configuration) · [Usage](#usage) · [Tools](#tools) · [Plugins](#plugins) · [Architecture](#architecture) · [Tests](#tests)

</div>

---

AERO is a terminal coding agent powered by **Atria Dawn Preview**. It gives the model direct access to your filesystem, shell, and the web, enabling a **plan → edit → run → verify → iterate** workflow entirely from your terminal.

AERO uses **Atria ASI** as its hosted inference provider by default through its API. Atria Dawn Preview is designed for research, engineering, software development, tool use, and long-running Agent tasks.

Built entirely on the Python standard library. No `pip install` of third-party runtime packages required.

## Atria Dawn Preview

AERO is built around **Atria Dawn Preview**, the model provided through **Atria ASI's hosted inference API**.

Atria describes Dawn Preview as a model designed for research, engineering, and long-running Agent tasks, with capabilities spanning software development, tool use, research, interactive creation, and other multi-step workflows.

The model supports a **256K-token context window** and can be accessed through three standard API interfaces:

- **Chat Completions**
- **Messages**
- **Responses**

AERO uses the OpenAI-compatible API by default, while its configurable endpoint and model architecture allow compatible inference providers to be used as well.

### Default model configuration

```env
ATRIA_API_KEY=atr_your_key_here
ATRIA_BASE_URL=https://api.atria-asi.ai/v1
ATRIA_MODEL=Atria-Dawn-Preview
```

### Atria links

- **Atria ASI:** https://atria-asi.ai/
- **Atria Dawn Preview:** https://api.atria-asi.ai/
- **Atria API Documentation:** https://api.atria-asi.ai/docs

> **API availability:** Atria's current API offering and token allowances are subject to its account, pricing, and usage terms. Check the official Atria documentation for the current limits and availability.

## Features

- **Full-screen TUI** — session header, scrollable transcript, markdown rendering, collapsible reasoning, diff previews, multi-line input with history
- **11 built-in tools** — read/write/edit files, list/glob/grep search, shell execution, web fetching, image reading, todo planner
- **Diff-based approval** — every file edit shown as a colored diff you approve or reject before it touches disk
- **Streaming** — token-by-token output with live reasoning display
- **Agent loop** — plan, use tools, inspect results, and iterate toward the requested outcome
- **Plugin system** — drop a `.py` file into `plugins/` to add your own tools
- **Session management** — save/load conversations with `/save` and `/load`
- **Cross-platform** — Windows, macOS, Linux
- **Zero dependencies** — pure Python standard library

## Install

### Quick start — run from source

```bash
git clone https://github.com/ronak-create/aero.git
cd aero
cp .env.example .env          # add your API key
python cli.py --tui           # launch the TUI
```

### Install as a command

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

AERO is configured to use **Atria Dawn Preview through the Atria ASI API** by default.

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

### Using another OpenAI-compatible provider

AERO is not hard-coded to Atria. You can provide another compatible API endpoint and model:

```bash
aero \
  --api-key <key> \
  --base-url <compatible-endpoint> \
  --model <model>
```

## Usage

### Full-screen TUI

```bash
aero --tui
```

Example:

```text
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

### One-shot

Useful for scripting and CI:

```bash
aero "fix the failing test in tests/test_parser.py"
```

### Auto-approve mode

```bash
aero --yolo
```

Or inside the TUI:

```text
/yolo
```

> **Warning:** Auto-approve mode allows the agent to execute actions without the normal approval gates. Use it only in environments where you understand the consequences.

### Keyboard shortcuts

| Key | Action |
|---|---|
| `Enter` | Send message |
| `Shift+Enter` | Newline |
| `Esc` | Interrupt request / clear input |
| `↑` / `↓` | Scroll transcript |
| `Ctrl+P` / `Ctrl+N` | Input history |
| `Ctrl+U` / `Ctrl+K` | Delete to start/end of line |
| `Ctrl+L` | Jump to newest |
| `Tab` | Autocomplete `/` commands |
| `Ctrl+C` | Interrupt, then quit |

### Slash commands

```text
/help
/clear
/save <name>
/load <name>
/plugins
/todos
/yolo
/model
/tui
/context
/cost
/exit
```

## Tools

AERO currently includes 11 built-in tools:

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

### Approval model

Operations that can modify your environment are gated by default.

```text
Agent
  │
  ├── read_file ────────────────► allowed
  │
  ├── grep_search ──────────────► allowed
  │
  ├── edit_file ────────────────► approval required
  │
  └── run_shell ────────────────► approval required
```

For file modifications, AERO presents a diff before applying the change.

## Plugins

AERO supports drop-in Python plugins.

Create a `.py` file inside `plugins/`:

```python
# plugins/my_tool.py

SCHEMA = {
    "type": "function",
    "function": {
        "name": "my_tool",
        "description": "Does something useful",
        "parameters": {
            "type": "object",
            "properties": {
                "input": {"type": "string"}
            },
            "required": ["input"],
        },
    },
}


def run(input: str) -> str:
    return f"processed: {input}"
```

Plugins are automatically discovered at startup.

No registration required.

## Architecture

```text
cli.py          Entry point — REPL, one-shot, argument parsing, slash commands
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

### Agent loop

```text
User task
    │
    ▼
┌─────────────┐
│   AERO      │
│  Agent Loop │
└──────┬──────┘
       │
       ▼
   Model reasoning
       │
       ▼
    Tool call
       │
       ▼
┌─────────────────┐
│ Filesystem      │
│ Shell           │
│ Web             │
│ Planning        │
└────────┬────────┘
         │
         ▼
    Tool result
         │
         └──────────► Model
                       │
                       ▼
                    Iterate
                       │
                       ▼
                    Verify
```

## Development

Run the test suite with the standard-library runner:

```bash
python run_tests.py -v
```

If pytest is installed:

```bash
python -m pytest tests/
```

The test suite covers:

- Terminal layer
- Filesystem tools
- Diff rendering
- Markdown rendering
- LLM client
- Streaming SSE
- Plugin loader
- Agent loop
- Approval gates
- Iteration limits
- Interrupt handling
- TUI

## Why AERO?

AERO is intentionally small.

Instead of building a large framework around the model, the project focuses on providing the model with a practical execution environment:

```text
          Atria Dawn Preview
                  │
                  ▼
             AERO Agent
                  │
        ┌─────────┼─────────┐
        ▼         ▼         ▼
    Filesystem   Shell      Web
        │         │         │
        └─────────┼─────────┘
                  ▼
              Results
                  │
                  ▼
             Next action
```

The model handles reasoning and tool selection, while AERO provides the terminal interface, tool execution, approvals, state, and execution loop.

## Atria API

AERO uses the Atria API by default:

```text
https://api.atria-asi.ai/v1
```

Atria currently exposes:

- Chat Completions — `/v1/chat/completions`
- Messages — `/v1/messages`
- Responses — `/v1/responses`

All three use:

```text
Atria-Dawn-Preview
```

as the model ID.

For complete API usage and current limits:

**Atria API Documentation:**  
https://api.atria-asi.ai/docs

**Atria Dawn Preview:**  
https://api.atria-asi.ai/

## Project

**Model:**  
Atria Dawn Preview

**Inference Provider:**  
Atria ASI

## License

[MIT](LICENSE) — Copyright (c) 2026 ronak-create
