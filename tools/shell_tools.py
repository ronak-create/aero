"""Shell execution tool. Approval gating happens in agent.py, not here."""
from __future__ import annotations

import subprocess


def run_shell(command: str, cwd: str | None = None, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {timeout}s"

    out = result.stdout[-8000:] if result.stdout else ""
    err = result.stderr[-4000:] if result.stderr else ""
    parts = [f"exit_code: {result.returncode}"]
    if out:
        parts.append(f"stdout:\n{out}")
    if err:
        parts.append(f"stderr:\n{err}")
    return "\n".join(parts)
