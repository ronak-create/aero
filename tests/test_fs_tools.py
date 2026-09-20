"""Filesystem tool tests against real files in a temp directory."""
import os
import unittest
from pathlib import Path

from tools.fs_tools import (
    edit_file, glob_search, grep_search, list_dir, preview_edit, read_file,
    write_file,
)


class TestReadFile(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(__file__).parent / "_scratch_read"
        self.tmp.mkdir(exist_ok=True)
        self.path = self.tmp / "sample.txt"
        self.path.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    def tearDown(self) -> None:
        for f in self.tmp.glob("*"):
            f.unlink()
        self.tmp.rmdir()

    def test_reads_with_line_numbers(self):
        result = read_file(str(self.path))
        self.assertIn(f"{1:>5}\talpha", result)
        self.assertIn(f"{2:>5}\tbeta", result)

    def test_line_range_is_inclusive_and_offset(self):
        result = read_file(str(self.path), start_line=2, end_line=2)
        self.assertIn("beta", result)
        self.assertNotIn("alpha", result)
        self.assertNotIn("gamma", result)
        # Numbers reflect the original file, not the slice.
        self.assertIn(f"{2:>5}\tbeta", result)

    def test_missing_file_is_an_error_not_an_exception(self):
        self.assertTrue(read_file(str(self.tmp / "nope.txt")).startswith("ERROR"))

    def test_directory_rejected(self):
        self.assertTrue(read_file(str(self.tmp)).startswith("ERROR"))

    def test_truncates_very_large_files(self):
        big = self.tmp / "big.txt"
        big.write_text("x" * 200_000, encoding="utf-8")
        result = read_file(str(big))
        self.assertIn("[truncated", result)
        self.assertLess(len(result), 200_000)


class TestWriteEdit(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(__file__).parent / "_scratch_write"
        self.tmp.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        for f in self.tmp.glob("**/*"):
            if f.is_file():
                f.unlink()
        for d in sorted(self.tmp.glob("**/*"), reverse=True):
            if d.is_dir():
                d.rmdir()
        self.tmp.rmdir()

    def test_write_creates_parent_dirs(self):
        out = self.tmp / "nested" / "deep" / "file.txt"
        result = write_file(str(out), "hello")
        self.assertTrue(result.startswith("OK"))
        self.assertEqual(out.read_text(), "hello")

    def test_write_refuses_existing_without_overwrite(self):
        existing = self.tmp / "exists.txt"
        write_file(str(existing), "first")
        result = write_file(str(existing), "second")
        self.assertTrue(result.startswith("ERROR"))
        self.assertEqual(existing.read_text(), "first")

    def test_write_overwrite_replaces(self):
        existing = self.tmp / "exists.txt"
        write_file(str(existing), "first")
        write_file(str(existing), "second", overwrite=True)
        self.assertEqual(existing.read_text(), "second")

    def test_preview_reports_what_would_change(self):
        p = self.tmp / "code.py"
        p.write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
        old, new, err = preview_edit(str(p), "'hi'", "'hello'")
        self.assertEqual(err, "")
        self.assertIn("'hi'", old)
        self.assertIn("'hello'", new)
        self.assertNotIn("'hello'", old)

    def test_preview_rejects_missing_match(self):
        p = self.tmp / "code.py"
        p.write_text("a\n", encoding="utf-8")
        _old, _new, err = preview_edit(str(p), "zzz", "yyy")
        self.assertIn("not found", err)

    def test_preview_rejects_ambiguous_match(self):
        p = self.tmp / "code.py"
        p.write_text("dup\ndup\n", encoding="utf-8")
        _old, _new, err = preview_edit(str(p), "dup", "one")
        self.assertIn("2 locations", err)

    def test_edit_file_applies_unique_replace(self):
        p = self.tmp / "code.py"
        p.write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
        result = edit_file(str(p), "'hi'", "'hello there'")
        self.assertTrue(result.startswith("OK"))
        self.assertIn("'hello there'", p.read_text())

    def test_edit_does_not_write_on_failure(self):
        p = self.tmp / "code.py"
        p.write_text("original\n", encoding="utf-8")
        edit_file(str(p), "missing", "x")
        self.assertEqual(p.read_text(), "original\n")

    def test_edit_is_exact_match_whitespace_sensitive(self):
        # edit_file does exact substring replacement, so spacing the model
        # only imagined is a failed match, not a fuzzy success.
        p = self.tmp / "code.py"
        p.write_text("value=1\n", encoding="utf-8")
        _old, _new, err = preview_edit(str(p), "value = 1", "value = 2")
        self.assertIn("not found", err)
        # ...and the exact text does match.
        _old, new, err = preview_edit(str(p), "value=1", "value=2")
        self.assertEqual(err, "")
        self.assertEqual(new, "value=2\n")


class TestSearch(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(__file__).parent / "_scratch_search"
        self.tmp.mkdir(exist_ok=True)
        (self.tmp / "a.py").write_text("def one():\n    pass\n", encoding="utf-8")
        (self.tmp / "b.txt").write_text("needle in a haystack\n", encoding="utf-8")
        nested = self.tmp / "pkg"
        nested.mkdir()
        (nested / "c.py").write_text("NEEDLE upper\n", encoding="utf-8")

    def tearDown(self) -> None:
        for f in self.tmp.glob("**/*"):
            if f.is_file():
                f.unlink()
        for d in sorted(self.tmp.glob("**/*"), reverse=True):
            if d.is_dir():
                d.rmdir()
        self.tmp.rmdir()

    def test_list_dir_sorts_dirs_first(self):
        listed = list_dir(str(self.tmp))
        lines = listed.split("\n")
        self.assertEqual(lines[0], "pkg/")
        self.assertIn("a.py", lines)

    def test_list_dir_hides_dotfiles(self):
        (self.tmp / ".secret").write_text("nope", encoding="utf-8")
        self.assertNotIn(".secret", list_dir(str(self.tmp)))

    def test_glob_finds_by_pattern(self):
        result = glob_search("**/*.py", str(self.tmp))
        self.assertIn("a.py", result)
        self.assertIn(os.path.join("pkg", "c.py"), result)
        self.assertNotIn("b.txt", result)

    def test_glob_starstar_matches_top_level(self):
        # "**/" is allowed to match zero directories.
        result = glob_search("**/*.txt", str(self.tmp))
        self.assertIn("b.txt", result)

    def test_glob_no_matches(self):
        self.assertEqual(glob_search("*.rs", str(self.tmp)), "(no matches)")

    def test_grep_matches_and_reports_locations(self):
        result = grep_search("needle", str(self.tmp))
        self.assertIn("b.txt:1:", result)

    def test_grep_is_case_sensitive(self):
        result = grep_search("needle", str(self.tmp))
        self.assertNotIn("c.py", result)
        self.assertIn("c.py:1:", grep_search("NEEDLE", str(self.tmp)))

    def test_grep_file_glob_filter(self):
        result = grep_search("NEEDLE|needle", str(self.tmp), file_glob="*.py")
        self.assertIn("c.py:1:", result)
        self.assertNotIn("b.txt", result)

    def test_grep_bad_regex_is_reported(self):
        self.assertTrue(grep_search("(unclosed", str(self.tmp)).startswith("ERROR"))

    def test_grep_no_matches(self):
        self.assertEqual(grep_search("doesnotexistword", str(self.tmp)), "(no matches)")


if __name__ == "__main__":
    unittest.main()
