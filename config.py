"""
Configuration loading for termagent.

Reads settings from environment variables, falling back to a local .env
file (simple KEY=VALUE format, no external dependency needed) if present.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Very small .env loader: KEY=VALUE per line, '#' comments allowed."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Don't overwrite a value the user already set in their real shell env.
        os.environ.setdefault(key, value)


@dataclass
class Config:
    api_key: str
    base_url: str
    model: str
    auto_approve: bool = False
    max_iterations: int = 25
    request_timeout: int = 120

    @classmethod
    def load(cls, cli_overrides: dict | None = None) -> "Config":
        _load_dotenv(Path.cwd() / ".env")
        _load_dotenv(Path.home() / ".termagent.env")

        cli_overrides = cli_overrides or {}

        api_key = cli_overrides.get("api_key") or os.environ.get("ATRIA_API_KEY", "")
        base_url = (
            cli_overrides.get("base_url")
            or os.environ.get("ATRIA_BASE_URL", "https://api.atria-asi.ai/v1")
        ).rstrip("/")
        # The endpoint rejects this lowercase form with HTTP 400; the real id
        # is "Atria-Dawn-Preview". LLMClient.resolve_model() fixes up a wrong
        # case automatically, but keep the default correct to begin with.
        model = (
            cli_overrides.get("model")
            or os.environ.get("ATRIA_MODEL", "Atria-Dawn-Preview")
        )

        if not api_key:
            raise SystemExit(
                "No API key found. Set ATRIA_API_KEY in your environment, in a "
                "./.env file, or pass --api-key on the command line."
            )

        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            auto_approve=cli_overrides.get("auto_approve", False),
        )
