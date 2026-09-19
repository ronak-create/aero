"""
Thin client for an OpenAI-compatible Chat Completions endpoint.

Talks the standard `/v1/chat/completions` shape (messages + tools +
tool_choice, response with choices[0].message, optional tool_calls) -- this
was verified against the live endpoint, which is fully OpenAI-compatible in
both the non-streaming and SSE streaming paths.

Two provider-specific details this handles:
  - Model IDs are case-sensitive on this endpoint ("atria-dawn-preview" is
    rejected with HTTP 400 "A supported model is required"; the real id is
    "Atria-Dawn-Preview"). resolve_model() fixes that up automatically.
  - The endpoint emits `reasoning_content` alongside `content`; we surface it
    separately so the UI can show reasoning as its own collapsible block.

Everything else in the project talks to the model only through
LLMClient.chat() / LLMClient.stream(), so this remains the single file to
touch if the API shape changes.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterator


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[dict[str, Any]]
    reasoning: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class LLMClient:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: int = 120):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        # Store as-given; resolve lazily on first request so a bad case is
        # corrected from /models rather than hard-failing.
        self._model = model
        self._resolved_model: str | None = None
        self.timeout = timeout

    # ------------------------------------------------------------------ model

    def list_models(self) -> list[str]:
        """Return the model ids the endpoint advertises (empty if unsupported)."""
        req = urllib.request.Request(
            f"{self.base_url}/models", method="GET",
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            return []
        return [m.get("id", "") for m in raw.get("data", []) if m.get("id")]

    def resolve_model(self) -> str:
        """
        Return a model id the endpoint will accept.

        Exact match wins. Otherwise try a case-insensitive match, then a
        case-insensitive substring match, so "atria-dawn-preview" maps to the
        advertised "Atria-Dawn-Preview". Falls back to the raw configured name
        if the endpoint doesn't expose /models -- the caller then sees the
        provider's own error message, which is more useful than a guess.
        """
        if self._resolved_model:
            return self._resolved_model

        wanted = self._model
        available = self.list_models()

        def pick(matches):
            return next(iter(matches), None)

        resolved = (
            pick([m for m in available if m == wanted])
            or pick([m for m in available if m.lower() == wanted.lower()])
            or pick([m for m in available if wanted.lower() in m.lower()])
        )
        self._resolved_model = resolved or wanted
        return self._resolved_model

    # ----------------------------------------------------------------- requests

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _post(self, url: str, payload: dict[str, Any], stream: bool = False):
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST", headers=self._headers()
        )
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = urllib.request.urlopen(req, timeout=self.timeout)
                return resp if stream else json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")
                # Retry once on a transient overload/rate-limit with backoff.
                if e.code in (429, 503) and attempt <= 2:
                    time.sleep(min(2.0 * attempt, 4.0))
                    req = urllib.request.Request(
                        url, data=body, method="POST", headers=self._headers()
                    )
                    continue
                raise LLMError(f"HTTP {e.code} from {url}: {detail}") from e
            except urllib.error.URLError as e:
                raise LLMError(f"Could not reach {url}: {e.reason}") from e

    # ------------------------------------------------------- non-streaming chat

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.resolve_model(),
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        raw = self._post(f"{self.base_url}/chat/completions", payload)
        try:
            choice = raw["choices"][0]["message"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"Unexpected response shape: {raw}") from e

        return LLMResponse(
            content=choice.get("content"),
            tool_calls=choice.get("tool_calls") or [],
            reasoning=choice.get("reasoning_content"),
            raw=raw,
        )

    # ----------------------------------------------------------- streaming chat

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
    ) -> Iterator[dict[str, Any]]:
        """
        Yield SSE deltas as dicts: {"content": str, "reasoning": str,
        "tool_calls": [...]}. Tool calls arrive incrementally across chunks;
        the caller is responsible for accumulating them (see accumulate()).
        Terminates after the final chunk ([DONE]) or raises LLMError.
        """
        payload: dict[str, Any] = {
            "model": self.resolve_model(),
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        resp = self._post(
            f"{self.base_url}/chat/completions", payload, stream=True
        )
        try:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                if delta or choice.get("finish_reason"):
                    yield {
                        "content": delta.get("content") or "",
                        "reasoning": delta.get("reasoning_content") or "",
                        "tool_calls": delta.get("tool_calls") or [],
                        "finish_reason": choice.get("finish_reason"),
                        "raw": chunk,
                    }
        finally:
            resp.close()


def accumulate_tool_calls(
    accumulated: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> None:
    """
    Merge one SSE chunk's tool_calls into an accumulated list, in place.
    Index-keyed deltas arrive split across many chunks; args arrive as
    fragments that must be concatenated, not replaced.
    """
    for inc in incoming:
        idx = inc.get("index", 0)
        while len(accumulated) <= idx:
            accumulated.append(
                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
            )
        slot = accumulated[idx]
        if inc.get("id"):
            slot["id"] = inc["id"]
        if inc.get("type"):
            slot["type"] = inc["type"]
        fn = inc.get("function") or {}
        if fn.get("name"):
            slot["function"]["name"] += fn["name"]
        if "arguments" in fn and fn["arguments"] is not None:
            slot["function"]["arguments"] += fn["arguments"]
