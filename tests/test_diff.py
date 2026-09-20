"""Diff rendering tests."""
import unittest
from pathlib import Path

from diff import make_diff, render_diff
from term import strip_ansi


class TestMakeDiff(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(__file__).parent / "_scratch_diff"
        self.tmp.mkdir(exist_ok=True)
        self.path = self.tmp / "sample.py"
        self.old = "def greet():\n    return 'hi'\n"
        self.path.write_text(self.old, encoding="utf-8")

    def tearDown(self) -> None:
        for f in self.tmp.glob("*"):
            f.unlink()
        self.tmp.rmdir()

    def test_single_line_change(self):
        new = self.old.replace("'hi'", "'hello'")
        diff = make_diff("sample.py", self.old, new)
        self.assertIn("-    return 'hi'", diff)
        self.assertIn("+    return 'hello'", diff)

    def _body(self, diff: list[str]) -> list[str]:
        """Diff lines excluding the ---/+++ file headers."""
        return [line for line in diff
                if not line.startswith("---") and not line.startswith("+++")]

    def test_added_line(self):
        new = "def greet():\n    name = 'world'\n    return 'hi'\n"
        diff = self._body(make_diff("sample.py", self.old, new))
        self.assertTrue(any(line.startswith("+") for line in diff))
        self.assertFalse(any(line.startswith("-") for line in diff))

    def test_removed_line(self):
        new = "def greet():\n"
        diff = self._body(make_diff("sample.py", self.old, new))
        self.assertTrue(any(line.startswith("-") for line in diff))

    def test_identical_content_has_no_diff(self):
        self.assertEqual(make_diff("sample.py", self.old, self.old), [])

    def test_none_inputs_are_safe(self):
        self.assertEqual(make_diff("sample.py", None, "x"), [])
        self.assertEqual(make_diff("sample.py", "x", None), [])

    def test_hunk_header_present(self):
        new = self.old.replace("'hi'", "'hello'")
        diff = make_diff("sample.py", self.old, new)
        self.assertTrue(any(line.startswith("@@") for line in diff))

    def test_context_lines_surround_changes(self):
        # With 3 lines of context, unchanged neighbors appear for orientation.
        old = "\n".join(f"line {i}" for i in range(10))
        new = old.replace("line 5", "line five")
        diff = make_diff("s.txt", old, new, context=3)
        self.assertIn(" line 4", diff)
        self.assertIn(" line 6", diff)
        self.assertNotIn(" line 0", diff)

    def test_preview_edit_matches_actual(self):
        # The diff the UI shows must be the diff the tool would apply.
        from tools.fs_tools import edit_file, preview_edit

        target = self.tmp / "real.py"
        target.write_text("x = 1\n", encoding="utf-8")
        _old, expected, err = preview_edit(str(target), "x = 1", "x = 2")
        self.assertEqual(err, "")
        shown = make_diff(str(target), _old, expected)
        edit_file(str(target), "x = 1", "x = 2")
        actual = make_diff(str(target), _old, target.read_text())
        self.assertEqual(shown, actual)


class TestRenderDiff(unittest.TestCase):
    def _diff(self, old: str, new: str, path: str = "f.py") -> list[str]:
        return make_diff(path, old, new)

    def test_empty_diff_renders_nothing(self):
        self.assertEqual(render_diff([], 80), [])

    def test_renders_a_panel_with_stats(self):
        diff = self._diff("a\n", "b\n")
        out = [strip_ansi(line) for line in render_diff(diff, 80)]
        joined = "\n".join(out)
        self.assertIn("+1", joined)  # added count
        self.assertIn("-1", joined)  # removed count
        self.assertTrue(out[0].startswith("┌"))

    def test_added_and_removed_are_marked(self):
        diff = self._diff("old line\n", "new line\n")
        out = [strip_ansi(line) for line in render_diff(diff, 80)]
        self.assertTrue(any(line.lstrip("│ ").startswith("+") for line in out))
        self.assertTrue(any(line.lstrip("│ ").startswith("-") for line in out))

    def test_respects_width(self):
        diff = self._diff("short\n", "a really quite extremely long replacement line\n")
        out = [strip_ansi(line) for line in render_diff(diff, 40)]
        for line in out:
            self.assertLessEqual(len(line), 41, line)  # width + 1 tolerance

    def test_truncation_marker(self):
        long_line = "x" * 300
        diff = self._diff("a\n", long_line + "\n")
        out = [strip_ansi(line) for line in render_diff(diff, 40)]
        self.assertTrue(any("…" in line for line in out))

    def test_cjk_width_is_respected(self):
        # Full-width glyphs count as 2 columns; the panel must not overflow.
        diff = self._diff("日本語\n", "日本語テスト\n")
        out = [strip_ansi(line) for line in render_diff(diff, 30)]
        for line in out:
            self.assertLessEqual(len(line), 32, line)


if __name__ == "__main__":
    unittest.main()
