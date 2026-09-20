"""Tool registry and plugin loader tests."""
import json
import struct
import sys
import textwrap
import unittest
import zlib
from pathlib import Path

import tools as tools_pkg
from tools import BUILTIN_TOOLS, DANGEROUS_TOOLS, IMPLEMENTATIONS, load_plugins


def _make_png(width: int = 1, height: int = 1) -> bytes:
    """A minimal valid PNG, built rather than copied from a base64 blob."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    raw = b"".join(b"\x00" + b"\x00\x00\x00\x00" * width for _ in range(height))
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return header + ihdr + idat + iend


_ONE_PIXEL_PNG = _make_png()


class TestRegistry(unittest.TestCase):
    def test_every_builtin_schema_has_an_implementation(self):
        names = {s["function"]["name"] for s in BUILTIN_TOOLS}
        for name in names:
            self.assertIn(name, IMPLEMENTATIONS, f"{name} has no implementation")

    def test_every_implementation_has_a_schema(self):
        names = {s["function"]["name"] for s in BUILTIN_TOOLS}
        for name in IMPLEMENTATIONS:
            self.assertIn(name, names, f"{name} has no schema")

    def test_schemas_are_well_formed(self):
        for schema in BUILTIN_TOOLS:
            self.assertEqual(schema["type"], "function")
            fn = schema["function"]
            self.assertIn("name", fn)
            self.assertIn("description", fn)
            params = fn["parameters"]
            self.assertEqual(params["type"], "object")
            declared = set(params.get("properties", {}))
            for required in params.get("required", []):
                self.assertIn(required, declared)

    def test_dangerous_set_matches_mutating_tools(self):
        self.assertEqual(DANGEROUS_TOOLS, {"write_file", "edit_file", "run_shell"})

    def test_readonly_tools_are_not_gated(self):
        for name in ("read_file", "list_dir", "grep_search", "todo_read"):
            self.assertNotIn(name, DANGEROUS_TOOLS)


class TestTodoTool(unittest.TestCase):
    def setUp(self) -> None:
        from tools.todo_tools import _TODOS

        self._todos = _TODOS.copy()
        _TODOS.clear()

    def tearDown(self) -> None:
        from tools.todo_tools import _TODOS

        _TODOS[:] = self._todos

    def test_write_and_read(self):
        from tools.todo_tools import get_todos, todo_read, todo_write

        result = todo_write([
            {"id": "1", "text": "first", "status": "pending"},
            {"id": "2", "text": "second", "status": "in_progress"},
        ])
        self.assertIn("first", result)
        self.assertIn("[~]", result)
        self.assertEqual(len(get_todos()), 2)
        self.assertIn("first", todo_read())

    def test_invalid_status_falls_back_to_pending(self):
        from tools.todo_tools import get_todos, todo_write

        todo_write([{"text": "x", "status": "bogus"}])
        self.assertEqual(get_todos()[0]["status"], "pending")

    def test_ids_are_stringified(self):
        from tools.todo_tools import get_todos, todo_write

        todo_write([{"id": 7, "text": "x"}])
        self.assertEqual(get_todos()[0]["id"], "7")

    def test_empty_list(self):
        from tools.todo_tools import todo_read

        self.assertEqual(todo_read(), "(todo list is empty)")


class TestOtherTools(unittest.TestCase):
    def test_shell_echo(self):
        from tools.shell_tools import run_shell

        result = run_shell("echo hello")
        self.assertIn("exit_code: 0", result)
        self.assertIn("hello", result)

    def test_shell_nonzero_exit_is_reported_not_raised(self):
        from tools.shell_tools import run_shell

        result = run_shell("exit 3")
        self.assertIn("exit_code: 3", result)

    def test_shell_timeout(self):
        from tools.shell_tools import run_shell

        result = run_shell("sleep 5", timeout=1)
        self.assertTrue(result.startswith("ERROR: command timed out"))

    def test_fetch_rejects_non_http(self):
        from tools.web_tools import fetch_url

        self.assertTrue(fetch_url("file:///etc/passwd").startswith("ERROR"))

    def test_fetch_bad_host_is_an_error_not_a_crash(self):
        from tools.web_tools import fetch_url

        self.assertTrue(fetch_url("https://no-such-host.invalid/").startswith("ERROR"))

    def test_image_rejects_non_image(self):
        from tools.vision_tools import read_image

        text_file = Path(__file__).parent / "not_an_image.txt"
        text_file.write_text("hello", encoding="utf-8")
        try:
            self.assertTrue(read_image(str(text_file)).startswith("ERROR"))
        finally:
            text_file.unlink()

    def test_image_returns_data_uri(self):
        import base64

        from tools.vision_tools import read_image

        png = Path(__file__).parent / "tiny.png"
        # A 1x1 transparent PNG, written as raw bytes so the test can't be
        # broken by a hand-typed base64 string.
        png.write_bytes(_ONE_PIXEL_PNG)
        try:
            result = read_image(str(png))
            self.assertTrue(result.startswith("data:image/png;base64,"))
            self.assertGreater(len(result), 50)
            # The payload must round-trip back to the exact bytes.
            encoded = result.split(",", 1)[1]
            self.assertEqual(base64.b64decode(encoded), _ONE_PIXEL_PNG)
        finally:
            png.unlink()


class TestPluginLoading(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = Path(__file__).parent / "_scratch_plugins"
        self._dir.mkdir(exist_ok=True)
        # Snapshot the registries: plugin loading mutates them globally.
        self._tools = list(BUILTIN_TOOLS)
        self._impls = dict(IMPLEMENTATIONS)

    def tearDown(self) -> None:
        # Importing a plugin leaves a __pycache__ behind; sweep the tree.
        for path in sorted(self._dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        BUILTIN_TOOLS[:] = self._tools
        IMPLEMENTATIONS.clear()
        IMPLEMENTATIONS.update(self._impls)

    def _write(self, name: str, source: str) -> None:
        (self._dir / name).write_text(textwrap.dedent(source), encoding="utf-8")

    def test_single_tool_plugin(self):
        self._write("single.py", """
            SCHEMA = {"type": "function", "function": {
                "name": "single_tool", "description": "d",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }}

            def run():
                return "single ok"
        """)
        loaded = load_plugins(self._dir)
        self.assertEqual(loaded, ["single_tool"])
        self.assertIn("single_tool", IMPLEMENTATIONS)
        self.assertEqual(IMPLEMENTATIONS["single_tool"](), "single ok")

    def test_multi_tool_plugin(self):
        self._write("multi.py", """
            def a():
                return "a"
            def b():
                return "b"

            TOOLS = [
                ({"type": "function", "function": {
                    "name": "tool_a", "description": "d",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                }}, a),
                ({"type": "function", "function": {
                    "name": "tool_b", "description": "d",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                }}, b),
            ]
        """)
        loaded = load_plugins(self._dir)
        self.assertEqual(sorted(loaded), ["tool_a", "tool_b"])

    def test_broken_plugin_does_not_kill_loading(self):
        self._write("broken.py", "raise RuntimeError('boom')\n")
        self._write("good.py", """
            SCHEMA = {"type": "function", "function": {
                "name": "good_tool", "description": "d",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }}

            def run():
                return "good"
        """)
        loaded = load_plugins(self._dir)
        self.assertEqual(loaded, ["good_tool"])

    def test_missing_directory_is_fine(self):
        self.assertEqual(load_plugins(Path(__file__).parent / "does_not_exist"), [])

    def test_underscore_files_are_skipped(self):
        # __init__.py and other private files aren't treated as plugins.
        self._write("_private.py", """
            SCHEMA = {"type": "function", "function": {
                "name": "private", "description": "d",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }}
            def run():
                return "x"
        """)
        self.assertEqual(load_plugins(self._dir), [])

    def test_plugin_arguments_are_passed(self):
        self._write("args.py", """
            SCHEMA = {"type": "function", "function": {
                "name": "echo_arg", "description": "d",
                "parameters": {"type": "object",
                              "properties": {"text": {"type": "string"}},
                              "required": ["text"]},
            }}

            def run(text):
                return f"got {text}"
        """)
        load_plugins(self._dir)
        self.assertEqual(IMPLEMENTATIONS["echo_arg"](text="hi"), "got hi")


if __name__ == "__main__":
    unittest.main()
