"""
Tool registry for termagent.

Exposes:
- BUILTIN_TOOLS: list of OpenAI-style function schemas (for the request's "tools" field)
- IMPLEMENTATIONS: dict mapping tool name -> callable
- DANGEROUS_TOOLS: names that require user approval before running (unless auto-approve is on)
- load_plugins(dir): discovers extra tools from user-supplied .py files
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Callable

from . import fs_tools, shell_tools, todo_tools, vision_tools, web_tools


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


BUILTIN_TOOLS: list[dict] = [
    _schema(
        "read_file", "Read a text file, optionally a line range. Returns numbered lines.",
        {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "description": "1-indexed, optional"},
            "end_line": {"type": "integer", "description": "1-indexed inclusive, optional"},
        },
        ["path"],
    ),
    _schema(
        "write_file", "Create a new file with the given content. Fails if the file exists unless overwrite=true.",
        {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "overwrite": {"type": "boolean"},
        },
        ["path", "content"],
    ),
    _schema(
        "edit_file",
        "Replace an exact, unique substring in an existing file. old_str must match exactly "
        "(including whitespace) and appear exactly once.",
        {
            "path": {"type": "string"},
            "old_str": {"type": "string"},
            "new_str": {"type": "string"},
        },
        ["path", "old_str", "new_str"],
    ),
    _schema(
        "list_dir", "List files and directories at a path (non-recursive).",
        {"path": {"type": "string"}}, [],
    ),
    _schema(
        "glob_search", "Find files whose relative path matches a glob pattern (e.g. '**/*.py').",
        {"pattern": {"type": "string"}, "path": {"type": "string"}}, ["pattern"],
    ),
    _schema(
        "grep_search", "Search file contents for a regex pattern, returning matching lines with file:line.",
        {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "file_glob": {"type": "string", "description": "e.g. '*.py'"},
        },
        ["pattern"],
    ),
    _schema(
        "run_shell", "Run a shell command and return its exit code, stdout, and stderr.",
        {
            "command": {"type": "string"},
            "cwd": {"type": "string"},
            "timeout": {"type": "integer", "description": "seconds, default 60"},
        },
        ["command"],
    ),
    _schema(
        "fetch_url", "Fetch a web page or API endpoint and return its readable text content.",
        {"url": {"type": "string"}}, ["url"],
    ),
    _schema(
        "read_image", "Read an image file so it can be shown to you in the next turn.",
        {"path": {"type": "string"}}, ["path"],
    ),
    _schema(
        "todo_write",
        "Replace the current task list. Use this to plan multi-step work and update progress "
        "as you complete each step.",
        {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "text": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "done"]},
                    },
                    "required": ["text"],
                },
            }
        },
        ["items"],
    ),
    _schema("todo_read", "Read the current task list.", {}, []),
]

IMPLEMENTATIONS: dict[str, Callable] = {
    "read_file": fs_tools.read_file,
    "write_file": fs_tools.write_file,
    "edit_file": fs_tools.edit_file,
    "list_dir": fs_tools.list_dir,
    "glob_search": fs_tools.glob_search,
    "grep_search": fs_tools.grep_search,
    "run_shell": shell_tools.run_shell,
    "fetch_url": web_tools.fetch_url,
    "read_image": vision_tools.read_image,
    "todo_write": todo_tools.todo_write,
    "todo_read": todo_tools.todo_read,
}

# Tools that mutate the filesystem, run code, or leave the machine -- gated behind
# a confirmation prompt in agent.py unless the user passed --yolo.
DANGEROUS_TOOLS = {"write_file", "edit_file", "run_shell"}


def load_plugins(plugins_dir: Path) -> list[str]:
    """
    Load extra tools from *.py files in plugins_dir.

    Each plugin file may define either:
      - TOOLS: list[tuple[dict schema, callable]]   (multiple tools per file), or
      - SCHEMA: dict, run: callable                  (a single tool per file)

    Returns the list of tool names that were loaded.
    """
    loaded: list[str] = []
    if not plugins_dir.exists():
        return loaded

    for path in sorted(plugins_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f"termagent_plugin_{path.stem}", path)
        if not spec or not spec.loader:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as e:  # noqa: BLE001 - plugin errors shouldn't crash the whole agent
            print(f"[plugin error] failed to load {path.name}: {e}")
            continue

        pairs = []
        if hasattr(module, "TOOLS"):
            pairs = list(module.TOOLS)
        elif hasattr(module, "SCHEMA") and hasattr(module, "run"):
            pairs = [(module.SCHEMA, module.run)]

        for schema, func in pairs:
            name = schema["function"]["name"]
            BUILTIN_TOOLS.append(schema)
            IMPLEMENTATIONS[name] = func
            loaded.append(name)

    return loaded
