"""
A tiny in-memory task list the agent uses to plan and track multi-step work.

State lives for the duration of the process (module-level list). It's
intentionally simple -- the point is giving the model a place to externalize
its plan so long tasks don't get lost, not a full project-management system.
"""
from __future__ import annotations

_TODOS: list[dict[str, str]] = []  # [{"id": "1", "text": "...", "status": "pending|in_progress|done"}]


def todo_write(items: list[dict[str, str]]) -> str:
    """Replace the whole todo list. Each item: {id, text, status}."""
    global _TODOS
    valid_statuses = {"pending", "in_progress", "done"}
    cleaned = []
    for item in items:
        status = item.get("status", "pending")
        if status not in valid_statuses:
            status = "pending"
        cleaned.append({
            "id": str(item.get("id", len(cleaned) + 1)),
            "text": item.get("text", ""),
            "status": status,
        })
    _TODOS = cleaned
    return render_todos()


def todo_read() -> str:
    return render_todos()


def get_todos() -> list[dict[str, str]]:
    """A live snapshot for the UI to render; do not mutate."""
    return list(_TODOS)


def render_todos() -> str:
    if not _TODOS:
        return "(todo list is empty)"
    icons = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]"}
    return "\n".join(f"{icons.get(t['status'], '[ ]')} {t['id']}. {t['text']}" for t in _TODOS)
