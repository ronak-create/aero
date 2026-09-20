# atria-agent

**A terminal coding agent for the Atria Dawn Preview model.**

Plan → edit → run → verify → iterate, with a full-screen TUI, file
read/write/edit with diff approval, shell execution, web fetching, image
reading, a todo tracker for multi-step plans, and a plugin system for adding
your own tools. No third-party dependencies -- pure Python standard library.

Talks to the model over a standard OpenAI-compatible `/v1/chat/completions`
endpoint (messages + function-calling tools), verified working against the
Atria Dawn Preview API including non-streaming, SSE streaming, and multi-turn
tool-call round-trips. See **"Endpoint notes"** below for the two
provider-specific details that matter.

## Screenshots / layout

The TUI opens with a session header panel: ASCII art on the left, live session
stats on the right. Conversation flows below it, with collapsible reasoning,
per-tool status markers, and a diff preview for every file edit before it is
written.

## Setup

```bash
git clone https://github.com/ronak-create/atria-agent.git
cd atria-agent
cp .env.example .env
# edit .env: put your real API key in ATRIA_API_KEY
python cli.py --tui
```

No `pip install` needed. Requires Python 3.10+. Python 3.11 was used during
development and testing.

**Keep your key out of git.** `.env` is in `.gitignore` for a reason -- if you
accidentally commit a real key, rotate it immediately in your provider
console; a pushed key must be treated as public.

## Usage

### Full-screen TUI (like the modern terminal agents)

```bash
python cli.py --tui
```

Takes over the terminal with a session header panel (ASCII art beside live
session stats), a scrollable transcript that renders markdown (headings,
bold, inline code, fenced code panels, lists, tables), the model's reasoning
as a collapsible block, streamed replies token by token, and each tool call
reported with a live status marker:

```
┌──────────────────────────────────────────────────────────────────────────┐
│     ___    _____   ____    ____        model     Atria-Dawn-Preview      │
│    /   |  / ___/  / __ \  / __ \       endpoint  api.atria-asi.ai        │
│   / /| | / /__   / /_/ / / / / /       cwd       …termagent/termagent   │
│  / ___ | \___/  / _, _/ / /_/ /        tools     11 builtin             │
│ /_/  |_|/____/  /_/ |_|  \____/        plugins   none                   │
│                                        mode      confirm                │
└──────────────────────────────────────────────────────────────────────────┘

● Refactor the auth module to use the new token format.
⌁ reasoning  The user wants to refactor auth… I should read it first.
  ✓ read_file  src/auth.py
> I've updated `src/auth.py` to use the new token format…
──────────────────────────────────────────────────────────────────────────
> ask anything…  (/ for commands)
ready          enter send · ↑/↓ scroll · ctrl+p/n history · esc interrupt · /
```

When the agent wants to edit a file, the change is shown as a colored diff
you approve or reject, rather than trusting a bare file path:

```
  ✓ read_file  src/auth.py
┌ +1 -1 src/auth.py
│ @@ -1,3 +1,3 @@
│  def greet():
│ -    return 'hi'
│ +    return 'hello there'
└┄┄┄
  approve this change? [y/n]
```

Keyboard:

| Key | Action |
|---|---|
| `Enter` | send the message |
| `Shift+Enter` | insert a newline (multi-line input) |
| `Esc` | interrupt the current request; clear the input when idle |
| `Up` / `Down` | scroll the transcript, or move the caret in a multi-line draft |
| `Shift`+`Up` / `Down` | scroll the transcript one line |
| `Shift`+`Home` / `End` | jump the transcript to the oldest / newest lines |
| `Left` / `Right` / `Home` / `End` | move the caret |
| `Ctrl+U` / `Ctrl+K` | delete to start / end of line |
| `Ctrl+Backspace` / `Ctrl+W` | delete the word behind the caret |
| `Ctrl+P` / `Ctrl+N` | recall the previous / next thing you typed |
| `Backspace` / `Delete` | edit |
| `Tab` | complete a `/` command |
| `PageUp` / `PageDown` | scroll the transcript a page |
| `Ctrl+L` | jump back to the newest lines |
| `Ctrl+C` | interrupt the current request, then quit when idle |
| `Ctrl+D` | quit (or delete forward if the input is non-empty) |

The transcript scrolls independently of the agent: once you scroll up, new
output stops dragging the view back down, and the status bar shows how far
through the history you are. `Ctrl+L` (or scrolling back to the bottom)
re-pins it.

If you type while the agent is still working, your message is shown greyed out
with a hollow marker and held until the current turn finishes, then sent —
the status bar shows a `queued` count. Starting a second worker on the same
history mid-turn would corrupt the conversation, so this is the safe path.
If the turn dies instead of finishing, the held message is marked
`not sent` rather than quietly vanishing.

The header keeps a running token total for the session — `1.5k tok (600↑ 900↓)`
— reported from the endpoint's usage accounting.

When the agent wants to write a file, edit a file, or run a shell command,
the status bar shows the request and waits for `y` / `n` — unless `--yolo` or
`/yolo` is on, in which case it proceeds and logs what it auto-approved.

### Line-mode REPL

```bash
python cli.py
you> refactor the auth module to use the new token format
```

The classic interface. Same agent loop, streamed to stdout, no full-screen
takeover. `/tui` switches to the full-screen UI from inside the REPL.

### One-shot (good for scripting / CI)

```bash
python cli.py "fix the failing test in tests/test_parser.py"
```

### Auto-approve

```bash
python cli.py --yolo      # or /yolo inside the TUI
```

Skips confirmation prompts for file writes, edits, and shell commands.
Use with real caution -- this removes your safety net.

### Tuning the loop

```bash
python cli.py --max-iterations 50 --timeout 180 --temperature 0.4
```

Or set them once in `.env`: `ATRIA_MAX_ITERATIONS`, `ATRIA_TIMEOUT`,
`ATRIA_TEMPERATURE`. Command-line flags win over the environment, and a
value that isn't a number falls back to the default with a warning rather
than being ignored silently.

### Slash commands

`/help`, `/clear`, `/save <name>`, `/load <name>`, `/plugins`, `/todos`,
`/yolo`, `/model`, `/tui`, `/exit`. These work the same in both the TUI and
the line-mode REPL.

## What it can do

- **read_file / write_file / edit_file** -- file I/O, with `edit_file` doing
  exact-match, unique-substring replacement (same model as safe find/replace)
- **list_dir / glob_search / grep_search** -- explore and search a codebase
- **run_shell** -- run tests, install packages, git commands, anything
- **fetch_url** -- pull in a web page or API response as plain text
- **read_image** -- base64-encodes an image and attaches it as real vision
  content on the next turn (works if your model/endpoint supports vision;
  if not, the model will just say it can't see images)
- **todo_write / todo_read** -- the agent's own scratchpad for multi-step
  plans, shown to you as it works

By default, `write_file`, `edit_file`, and `run_shell` pause for a y/N
confirmation before running, since those are the ones that can actually
damage something. `--yolo` turns that off.

## Adding your own tools (plugins)

Drop a `.py` file into `plugins/`. Two supported shapes:

```python
# single tool
SCHEMA = {"type": "function", "function": {"name": "...", "description": "...",
          "parameters": {"type": "object", "properties": {...}, "required": [...]}}}
def run(**kwargs) -> str:
    ...
```

```python
# multiple tools in one file
TOOLS = [(schema_dict_1, func_1), (schema_dict_2, func_2)]
```

Plugins are auto-discovered at startup -- no registration step, no editing
core files. See `plugins/example_weather.py` for a working (toy) example.

## Architecture

```
cli.py          REPL / one-shot entry point, slash commands, session save/load
tui.py          the full-screen UI (blocks, input editor, approvals, spinner)
term.py         terminal layer: raw input, screen size, ANSI escapes, wrapping
markdown.py     small Markdown -> ANSI renderer for the transcript
agent.py        the loop: send messages -> get tool calls -> run them -> repeat
llm_client.py   HTTP client for the model API (the file to edit if the API
                shape differs) -- non-streaming chat(), streaming stream(),
                and model-name resolution
config.py       env var / .env loading
tools/          built-in tool implementations + the schema/plugin registry
plugins/        drop-in extra tools
tests/          the test suite (see below)
```

## Tests

```bash
python run_tests.py          # everything, stdlib only
python run_tests.py -v       # per-test output
```

No third-party runner required, though `python -m pytest tests/` works too.
The suite covers the terminal layer (wrapping, widths, key decoding), the
filesystem tools against real temp files, diff and markdown rendering, the
LLM client against a stubbed HTTP layer (SSE parsing, tool-call accumulation,
model resolution, retry), the plugin loader, and the agent loop end to end
against a scripted fake model -- including the approval gate, the iteration
cap, and interrupt handling.

## Endpoint notes

Verified against the live Atria Dawn Preview endpoint:

- **Model IDs are case-sensitive.** Sending `atria-dawn-preview` is rejected
  with `HTTP 400 {"error": "A supported model is required."}}`; the real id
  is `Atria-Dawn-Preview`. `LLMClient.resolve_model()` now fetches `/models`
  once and case-insensitively matches whatever you configured, so a wrong
  case is fixed rather than fatal. The default in `config.py` and
  `.env.example` is the correct casing.
- **The response carries `reasoning_content`** alongside `content`. It's
  surfaced as its own dimmed "reasoning" block in the TUI instead of being
  discarded, and arrives as `reasoning` deltas on the streaming path.
- The rest of the shape is standard OpenAI: `choices[0].message`,
  `tool_calls[].function.{name,arguments}`, `role: "tool"` results back in,
  and `data:` SSE chunks with `[DONE]`.

## Honest limitations

This covers the core feature set of a modern terminal coding agent (agentic
loop, file ops, shell, search, web, vision, planning, extensibility,
full-screen TUI) but it is not a byte-for-byte clone of a mature product. Things it deliberately
leaves out, which you could add if you need them: persistent memory across
separate process runs beyond the manual `/save`/`/load`, a permissions config
file (currently `--yolo` or per-call prompts), sandboxed/containerized shell
execution, and a real MCP client. The `DANGEROUS_TOOLS` set in
`tools/__init__.py` and the plugin loader are the two places to start if you
want to extend it further.

Ctrl+C interrupts cleanly between requests and mid-stream, but a single
in-flight HTTP call to the model can't be hard-killed from Python's `urllib`
-- the loop stops as soon as the current response finishes.
