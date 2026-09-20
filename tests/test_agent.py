"""Agent-loop tests against a scripted fake model client.

No network. The fake client hands back canned SSE-shaped responses in
sequence, so these tests assert the loop's actual behavior: that tool results
go back to the model, that approvals gate dangerous tools, that the loop
terminates, and that usage is reported.
"""
import threading
import unittest
from typing import Any

from agent import Callbacks, run_turn, _truncate


class FakeClient:
    """Stands in for LLMClient, replaying a script of scripted responses.

    Each entry is a dict of content / tool_calls / reasoning / usage; the
    client yields one per call, and it can also stream them delta-by-delta.
    """

    def __init__(self, script: list[dict]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    def chat(self, messages, tools=None, temperature=0.2):
        self.requests.append({"messages": messages, "tools": tools,
                              "temperature": temperature, "streamed": False})
        if not self.script:
            raise AssertionError("script exhausted: the loop asked for another turn")
        frame = self.script.pop(0)
        from llm_client import LLMResponse

        return LLMResponse(
            content=frame.get("content"),
            tool_calls=frame.get("tool_calls") or [],
            reasoning=frame.get("reasoning"),
            usage=frame.get("usage") or {},
        )

    def stream(self, messages, tools=None, temperature=0.2):
        self.requests.append({"messages": messages, "tools": tools,
                              "temperature": temperature, "streamed": True})
        if not self.script:
            raise AssertionError("script exhausted: the loop asked for another turn")
        frame = self.script.pop(0)
        content = frame.get("content") or ""
        # Emit content one word at a time so streaming accumulation is tested,
        # not just the final string.
        words = content.split(" ")
        for i, word in enumerate(words):
            yield {
                "content": (word + " ") if i < len(words) - 1 else word,
                "reasoning": "", "tool_calls": [], "usage": None,
            }
        if frame.get("reasoning"):
            yield {"content": "", "reasoning": frame["reasoning"],
                   "tool_calls": [], "usage": None}
        for call in frame.get("tool_calls") or []:
            yield {"content": "", "reasoning": "",
                   "tool_calls": [call], "usage": None}
        if frame.get("usage"):
            yield {"content": "", "reasoning": "", "tool_calls": [],
                   "usage": frame["usage"]}


def _tool_call(name: str, arguments: dict, call_id: str = "c1") -> dict:
    import json

    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


class RecordingCallbacks(Callbacks):
    """Records every event the loop emits, for assertion."""

    def __init__(self, approvals: dict[str, bool] | None = None) -> None:
        self.content = []
        self.reasoning = []
        self.tool_starts: list[tuple[str, dict, str]] = []
        self.tool_results: list[tuple[str, str, bool, str]] = []
        self.usage: list[dict] = []
        self.statuses: list[str] = []
        self.warnings: list[str] = []
        self._approvals = approvals or {}

    def on_content_delta(self, text):
        self.content.append(text)

    def on_reasoning_delta(self, text):
        self.reasoning.append(text)

    def on_tool_start(self, name, args, call_id=""):
        self.tool_starts.append((name, args, call_id))

    def on_tool_result(self, name, result, error, call_id=""):
        self.tool_results.append((name, result, error, call_id))

    def on_usage(self, usage):
        self.usage.append(usage)

    def on_status(self, status):
        self.statuses.append(status)

    def on_approve(self, description):
        # Approve or deny based on the tool name embedded in the description.
        for name, allowed in self._approvals.items():
            if description.startswith(f"{name}:"):
                return allowed
        return False

    def on_warning(self, message):
        self.warnings.append(message)


class TestAgentLoop(unittest.TestCase):
    def setUp(self) -> None:
        self.messages: list[dict] = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "do the thing"},
        ]

    def test_plain_reply_without_tools(self):
        client = FakeClient([{"content": "Hello there."}])
        cb = RecordingCallbacks()
        reply = run_turn(client, self.messages, cb, max_iterations=5)

        self.assertEqual(reply, "Hello there.")
        self.assertEqual("".join(cb.content), "Hello there.")
        self.assertEqual(cb.tool_starts, [])
        self.assertEqual(len(self.messages), 3)  # system + user + assistant

    def test_tool_call_round_trip(self):
        # The model first reads a real file, then answers using its contents.
        import pathlib

        sample = pathlib.Path(__file__).parent / "_roundtrip_sample.txt"
        sample.write_text("the file says hello\n", encoding="utf-8")
        try:
            client = FakeClient([
                {"tool_calls": [_tool_call("read_file", {"path": str(sample)})]},
                {"content": "The file says hello."},
            ])
            cb = RecordingCallbacks()
            reply = run_turn(client, self.messages, cb, max_iterations=5)
        finally:
            sample.unlink()

        self.assertEqual(reply, "The file says hello.")
        self.assertEqual(cb.tool_starts[0][0], "read_file")

        # The tool ran successfully (not an ERROR/DENIED marker).
        self.assertEqual(cb.tool_results[0][0], "read_file")
        self.assertFalse(cb.tool_results[0][2])
        self.assertIn("the file says hello", cb.tool_results[0][1])

        # And its result was sent back to the model on the next request.
        tool_msgs = [m for m in self.messages if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertIn("the file says hello", tool_msgs[0]["content"])
        self.assertEqual(tool_msgs[0]["tool_call_id"], "c1")

        # The assistant message echoes its tool_calls so the provider can
        # correlate them with the results.
        assistant_with_calls = next(
            m for m in self.messages if m.get("tool_calls")
        )
        self.assertEqual(assistant_with_calls["tool_calls"][0]["id"], "c1")

    def test_approval_denies_dangerous_tool(self):
        client = FakeClient([
            {"tool_calls": [_tool_call("run_shell", {"command": "rm -rf /"})]},
            {"content": "denied path not taken"},
        ])
        cb = RecordingCallbacks(approvals={"run_shell": False})
        run_turn(client, self.messages, cb, max_iterations=5)

        # The tool was requested but never run: the result is a DENIED marker,
        # not a shell output.
        self.assertEqual(cb.tool_results[0][0], "run_shell")
        self.assertTrue(cb.tool_results[0][1].startswith("DENIED"))
        self.assertTrue(cb.tool_results[0][2])

    def test_approval_allows_dangerous_tool(self):
        client = FakeClient([
            {"tool_calls": [_tool_call("run_shell", {"command": "echo hi"})]},
            {"content": "done"},
        ])
        cb = RecordingCallbacks(approvals={"run_shell": True})
        run_turn(client, self.messages, cb, max_iterations=5)

        self.assertEqual(cb.tool_results[0][0], "run_shell")
        self.assertTrue(cb.tool_results[0][1].startswith("exit_code:"))
        self.assertFalse(cb.tool_results[0][2])

    def test_unknown_tool_surfaces_warning_and_error(self):
        client = FakeClient([
            {"tool_calls": [_tool_call("not_a_tool", {})]},
            {"content": "ok"},
        ])
        cb = RecordingCallbacks()
        run_turn(client, self.messages, cb, max_iterations=5)

        self.assertEqual(cb.warnings, ["unknown tool 'not_a_tool'"])
        self.assertTrue(cb.tool_results[0][1].startswith("ERROR: unknown tool"))

    def test_bad_json_arguments_warns_but_continues(self):
        client = FakeClient([
            {"tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "read_file", "arguments": "{not json"},
            }]},
            {"content": "recovered"},
        ])
        cb = RecordingCallbacks()
        run_turn(client, self.messages, cb, max_iterations=5)

        self.assertTrue(any("invalid JSON" in w for w in cb.warnings))
        self.assertEqual(reply_or_none(cb), "recovered")

    def test_max_iterations_stops_the_loop(self):
        # A model that never stops calling tools should hit the cap, not loop
        # forever. Each iteration needs a distinct tool-call id so the
        # accumulated results don't collide.
        script = [
            {"tool_calls": [_tool_call("todo_read", {}, call_id=f"c{i}")]}
            for i in range(10)
        ]
        client = FakeClient(script)
        cb = RecordingCallbacks()
        reply = run_turn(client, self.messages, cb, max_iterations=3)

        self.assertIn("max tool-call iterations", reply)
        self.assertEqual(len(cb.tool_starts), 3)

    def test_usage_is_reported(self):
        client = FakeClient([
            {"content": "hi", "usage": {"total_tokens": 42,
                                        "prompt_tokens": 10,
                                        "completion_tokens": 32}},
        ])
        cb = RecordingCallbacks()
        run_turn(client, self.messages, cb, max_iterations=5)

        self.assertEqual(cb.usage[0]["total_tokens"], 42)

    def test_interrupt_stops_after_current_request(self):
        client = FakeClient([
            {"content": "partial"},
            {"content": "should not happen"},
        ])
        interrupt = threading.Event()
        interrupt.set()  # already interrupted before we start
        cb = RecordingCallbacks()
        reply = run_turn(client, self.messages, cb, max_iterations=5,
                         interrupt=interrupt)

        # The first response still lands in history; the loop stops there.
        self.assertEqual(reply, "partial")
        self.assertEqual(len(client.requests), 1)

    def test_error_reply_on_llm_failure(self):
        from llm_client import LLMError

        class Broken(FakeClient):
            def stream(self, *a, **kw):
                raise LLMError("HTTP 500: nope")

        client = Broken([])
        cb = RecordingCallbacks()
        reply = run_turn(client, self.messages, cb, max_iterations=5)

        self.assertIn("[error talking to model]", reply)
        self.assertIn("HTTP 500", reply)

    def test_non_streaming_path(self):
        client = FakeClient([{"content": "blocked reply"}])
        cb = RecordingCallbacks()
        reply = run_turn(client, self.messages, cb, max_iterations=5,
                         stream=False)

        self.assertEqual(reply, "blocked reply")
        self.assertFalse(client.requests[0]["streamed"])


class TestTruncate(unittest.TestCase):
    def test_short_text_untouched(self):
        self.assertEqual(_truncate("short"), "short")

    def test_long_text_keeps_head_and_tail(self):
        text = "A" * 10_000
        result = _truncate(text, limit=1000)
        self.assertLess(len(result), len(text))
        self.assertIn("[output truncated", result)
        self.assertTrue(result.startswith("A" * 500))
        self.assertTrue(result.endswith("A" * 500))

    def test_boundary_is_exact(self):
        self.assertEqual(_truncate("x" * 1000, limit=1000), "x" * 1000)


def reply_or_none(cb: RecordingCallbacks):
    return "".join(cb.content) or None


if __name__ == "__main__":
    unittest.main()
