"""LLM client tests: SSE parsing, tool-call accumulation, model resolution.

Everything runs against a stubbed urlopen -- no network, no real key.
"""
import io
import json
import unittest
import urllib.request

import llm_client
from llm_client import LLMClient, LLMError, accumulate_tool_calls


class FakeResponse(io.BytesIO):
    """A urlopen stand-in that behaves like the streaming HTTP body."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _sse(*events: str) -> FakeResponse:
    body = ("\n".join(f"data: {e}" for e in events) + "\ndata: [DONE]\n\n")
    return FakeResponse(body.encode("utf-8"))


def _json_response(payload: dict) -> FakeResponse:
    return FakeResponse(json.dumps(payload).encode("utf-8"))


def install_urlopen(monkeypatch_target, responses):
    """Make urlopen hand out `responses` in order (callables or bodies)."""
    calls = []

    def fake(req, timeout=None):
        calls.append(req)
        item = responses.pop(0)
        if callable(item):
            return item(req)
        return item

    llm_client.urllib.request.urlopen = fake
    return calls


class TestSSEParsing(unittest.TestCase):
    class Client(LLMClient):
        """Skips /models resolution so it can't eat a stubbed response."""

        def list_models(self):
            return ["Model"]

    def setUp(self) -> None:
        self._original = llm_client.urllib.request.urlopen

    def tearDown(self) -> None:
        llm_client.urllib.request.urlopen = self._original

    def _client(self):
        return self.Client("key", "https://example.test/v1", "Model")

    def test_content_deltas_are_yielded(self):
        install_urlopen(None, [
            _sse(
                json.dumps({"choices": [{"delta": {"content": "Hello"}}]}),
                json.dumps({"choices": [{"delta": {"content": " there"}}]}),
            )
        ])
        deltas = list(self._client().stream([{"role": "user", "content": "hi"}]))
        self.assertEqual("".join(d["content"] for d in deltas), "Hello there")

    def test_reasoning_is_surfaced_separately(self):
        install_urlopen(None, [
            _sse(
                json.dumps({"choices": [{"delta": {"reasoning_content": "think"}}]}),
                json.dumps({"choices": [{"delta": {"content": "answer"}}]}),
            )
        ])
        deltas = list(self._client().stream([{"role": "user", "content": "hi"}]))
        self.assertEqual("".join(d["reasoning"] for d in deltas), "think")
        self.assertEqual("".join(d["content"] for d in deltas), "answer")

    def test_usage_frame_is_yielded(self):
        usage = {"total_tokens": 5, "prompt_tokens": 2, "completion_tokens": 3}
        install_urlopen(None, [
            _sse(
                json.dumps({"choices": [{"delta": {"content": "a"}}]}),
                # The terminal accounting frame can carry empty choices.
                json.dumps({"choices": [], "usage": usage}),
            )
        ])
        deltas = list(self._client().stream([{"role": "user", "content": "hi"}]))
        self.assertEqual(deltas[-1]["usage"], usage)

    def test_stream_requests_include_usage_option(self):
        captured = install_urlopen(None, [
            _sse(json.dumps({"choices": [{"delta": {"content": "a"}}]})),
        ])
        list(self._client().stream([{"role": "user", "content": "hi"}]))
        payload = json.loads(captured[0].data.decode("utf-8"))
        self.assertEqual(payload["stream_options"], {"include_usage": True})
        self.assertTrue(payload["stream"])

    def test_malformed_lines_are_skipped_not_raised(self):
        install_urlopen(None, [
            FakeResponse(
                b"data: not-json\n\n"
                b": ignored comment\n\n"
                b'data: {"choices": [{"delta": {"content": "ok"}}]}\n\n'
                b"data: [DONE]\n\n"
            )
        ])
        deltas = list(self._client().stream([{"role": "user", "content": "hi"}]))
        self.assertEqual("".join(d["content"] for d in deltas), "ok")

    def test_non_streaming_chat_parses_message(self):
        install_urlopen(None, [_json_response({
            "choices": [{"message": {
                "content": "hi", "reasoning_content": "thinking",
                "tool_calls": [{"id": "t1", "type": "function",
                                "function": {"name": "f", "arguments": "{}"}}],
            }}],
            "usage": {"total_tokens": 4},
        })])
        resp = self._client().chat([{"role": "user", "content": "hi"}])
        self.assertEqual(resp.content, "hi")
        self.assertEqual(resp.reasoning, "thinking")
        self.assertEqual(resp.usage, {"total_tokens": 4})
        self.assertEqual(resp.tool_calls[0]["function"]["name"], "f")

    def test_http_error_becomes_llm_error(self):
        def raise_400(req):
            raise llm_client.urllib.error.HTTPError(
                req.full_url, 400, "Bad Request", {}, None
            )

        install_urlopen(None, [raise_400])
        with self.assertRaises(LLMError) as ctx:
            self._client().chat([{"role": "user", "content": "hi"}])
        self.assertIn("HTTP 400", str(ctx.exception))

    def test_connection_error_becomes_llm_error(self):
        def raise_url(req):
            raise llm_client.urllib.error.URLError("connection refused")

        install_urlopen(None, [raise_url])
        with self.assertRaises(LLMError):
            self._client().chat([{"role": "user", "content": "hi"}])

    def test_retries_once_on_overload(self):
        attempts = []

        def maybe_fail(req):
            attempts.append(req)
            if len(attempts) < 2:
                raise llm_client.urllib.error.HTTPError(
                    req.full_url, 503, "Overloaded", {}, None
                )
            return _json_response({"choices": [{"message": {"content": "ok"}}]})

        install_urlopen(None, [maybe_fail, maybe_fail])
        resp = self._client().chat([{"role": "user", "content": "hi"}])
        self.assertEqual(resp.content, "ok")
        self.assertEqual(len(attempts), 2)


class TestAccumulateToolCalls(unittest.TestCase):
    def test_arguments_are_concatenated_not_replaced(self):
        accumulated = []
        for fragment in ['{"path": "a', '.txt"}']:
            accumulate_tool_calls(accumulated, [{
                "index": 0, "id": "c1", "type": "function",
                "function": {"name": "read_file", "arguments": fragment},
            }])
        self.assertEqual(
            accumulated[0]["function"]["arguments"], '{"path": "a.txt"}'
        )

    def test_name_arrives_in_fragments(self):
        accumulated = []
        for piece in ("read", "_file"):
            accumulate_tool_calls(accumulated, [{
                "index": 0, "function": {"name": piece},
            }])
        self.assertEqual(accumulated[0]["function"]["name"], "read_file")

    def test_parallel_calls_keyed_by_index(self):
        accumulated = []
        accumulate_tool_calls(accumulated, [
            {"index": 0, "id": "a", "function": {"name": "one", "arguments": "{"}},
            {"index": 1, "id": "b", "function": {"name": "two", "arguments": "{"}},
        ])
        accumulate_tool_calls(accumulated, [
            {"index": 0, "function": {"arguments": "}"}},
            {"index": 1, "function": {"arguments": "}"}},
        ])
        self.assertEqual(len(accumulated), 2)
        self.assertEqual(accumulated[0]["id"], "a")
        self.assertEqual(accumulated[0]["function"]["arguments"], "{}")
        self.assertEqual(accumulated[1]["function"]["name"], "two")

    def test_missing_index_defaults_to_zero(self):
        accumulated = []
        accumulate_tool_calls(accumulated, [{"function": {"arguments": "x"}}])
        self.assertEqual(accumulated[0]["function"]["arguments"], "x")


class TestResolveModel(unittest.TestCase):
    class Client(LLMClient):
        def __init__(self, model: str, advertised: list[str]):
            super().__init__("key", "https://example.test/v1", model)
            self._advertised = advertised

        def list_models(self):
            return list(self._advertised)

    def test_exact_match(self):
        c = self.Client("Atria-Dawn-Preview", ["Atria-Dawn-Preview"])
        self.assertEqual(c.resolve_model(), "Atria-Dawn-Preview")

    def test_wrong_case_is_corrected(self):
        # The endpoint rejects lowercase ids with HTTP 400; this is the fixup.
        c = self.Client("atria-dawn-preview", ["Atria-Dawn-Preview"])
        self.assertEqual(c.resolve_model(), "Atria-Dawn-Preview")

    def test_substring_match(self):
        c = self.Client("dawn", ["Atria-Dawn-Preview", "Atria-Night"])
        self.assertEqual(c.resolve_model(), "Atria-Dawn-Preview")

    def test_resolution_is_cached(self):
        calls = []

        class Counting(self.Client):
            def list_models(self):
                calls.append(1)
                return super().list_models()

        c = Counting("dawn", ["Atria-Dawn-Preview"])
        c.resolve_model()
        c.resolve_model()
        self.assertEqual(len(calls), 1)

    def test_falls_back_to_configured_when_unlistable(self):
        # No /models support: surface the configured name so the provider's
        # own error message reaches the user.
        c = self.Client("MyModel", [])
        self.assertEqual(c.resolve_model(), "MyModel")


if __name__ == "__main__":
    unittest.main()
