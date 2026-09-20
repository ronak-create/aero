"""Markdown renderer tests."""
import unittest

from markdown import render_markdown
from term import strip_ansi


def _plain(lines: list[str]) -> list[str]:
    return [strip_ansi(line) for line in lines]


class TestMarkdown(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(render_markdown("", 80), [""])

    def test_paragraph_passthrough(self):
        self.assertEqual(_plain(render_markdown("hello world", 80)), ["hello world"])

    def test_headings_are_bold(self):
        out = render_markdown("# Title", 80)
        self.assertEqual(_plain(out), ["Title", ""])

    def test_heading_level_scales_wrap_width(self):
        out = render_markdown("# " + "word " * 30, 40)
        # No line exceeds the terminal width.
        for line in _plain(out):
            self.assertLessEqual(len(line), 40)

    def test_bullet_lists(self):
        out = _plain(render_markdown("- one\n- two\n- three", 80))
        self.assertEqual(out, ["- one", "- two", "- three"])

    def test_nested_bullets_keep_indent(self):
        out = _plain(render_markdown("- top\n  - child", 80))
        self.assertEqual(out[0], "- top")
        self.assertTrue(out[1].strip().startswith("- child"))

    def test_ordered_lists(self):
        out = _plain(render_markdown("1. first\n2. second", 80))
        self.assertEqual(out, ["1. first", "2. second"])

    def test_bullet_wraps_under_the_marker(self):
        text = "- " + "word " * 20
        out = _plain(render_markdown(text, 30))
        for line in out:
            self.assertLessEqual(len(line), 30)
        # Continuation lines align past the marker, not at column 0.
        self.assertTrue(out[1].startswith("  "))

    def test_inline_code(self):
        out = _plain(render_markdown("run `pip install` now", 80))
        self.assertEqual(out[0], "run pip install now")

    def test_bold(self):
        out = _plain(render_markdown("this is **bold** text", 80))
        self.assertEqual(out[0], "this is bold text")

    def test_code_fence_becomes_panel(self):
        md = "```python\nprint('hi')\n```"
        out = _plain(render_markdown(md, 80))
        # The panel keeps the code on its own line, distinct from prose.
        self.assertTrue(any("print('hi')" in line for line in out))

    def test_code_fence_with_no_language(self):
        md = "```\nplain code\n```"
        out = _plain(render_markdown(md, 80))
        self.assertTrue(any("plain code" in line for line in out))

    def test_unclosed_code_fence_still_renders(self):
        # No closing fence: don't lose the content.
        out = _plain(render_markdown("```\norphan", 80))
        self.assertTrue(any("orphan" in line for line in out))

    def test_long_code_lines_are_truncated_not_wrapped(self):
        md = "```\n" + ("x" * 200) + "\n```"
        out = _plain(render_markdown(md, 60))
        code_lines = [line for line in out if "x" * 10 in line]
        self.assertTrue(code_lines)
        for line in code_lines:
            self.assertLessEqual(len(line), 60)
        self.assertTrue(any("…" in line for line in code_lines))

    def test_code_panel_borders_are_complete(self):
        # The unlabeled fence used to render its top edge as a bare corner
        # glyph because it sliced into an ANSI escape sequence.
        out = _plain(render_markdown("```\ncode\n```", 60))
        top = out[0]
        self.assertTrue(top.startswith("┌"), top)
        self.assertGreater(len(top), 3)  # not just the corner glyph
        self.assertTrue(all(c == "┄" for c in top[1:]))

        # The labeled variant puts the language tag on the top edge instead.
        out = _plain(render_markdown("```py\ncode\n```", 60))
        self.assertTrue(out[0].startswith("┌"))
        self.assertIn("py", out[0])

    def test_table_passthrough(self):
        md = "| a | b |\n|---|---|\n| 1 | 2 |"
        out = _plain(render_markdown(md, 80))
        self.assertEqual(out[0], "| a | b |")
        self.assertEqual(out[2], "| 1 | 2 |")

    def test_links_show_text(self):
        out = _plain(render_markdown("see [the docs](https://x.test)", 80))
        self.assertEqual(out[0], "see the docs")

    def test_trailing_blank_lines_collapse(self):
        # Runs of trailing blanks collapse to at most one.
        out = render_markdown("text\n\n\n\n", 80)
        self.assertEqual(_plain(out), ["text", ""])
        self.assertEqual(_plain(render_markdown("text", 80)), ["text"])

    def test_output_never_exceeds_width(self):
        text = (
            "## Heading\n\n"
            "Some long prose with many words that must wrap properly "
            "within the given column width, including averylongunbrokenword.\n\n"
            "- a bullet item that also goes on for quite a while and needs "
            "to wrap somewhere sensible\n"
        )
        for width in (20, 40, 78):
            for line in _plain(render_markdown(text, width)):
                self.assertLessEqual(
                    len(line), width, f"line {line!r} exceeds {width}"
                )


if __name__ == "__main__":
    unittest.main()
