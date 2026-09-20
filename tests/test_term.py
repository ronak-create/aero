"""Terminal-layer unit tests: widths, wrapping, ANSI handling, key decoding."""
import unittest
import unittest.mock

from term import (
    KeyEvent, _classify, _parse_csi, char_width, strip_ansi, text_width,
    wrap_text,
)


class TestWidths(unittest.TestCase):
    def test_ascii_is_width_one(self):
        self.assertEqual(char_width("a"), 1)
        self.assertEqual(text_width("hello"), 5)

    def test_cjk_is_width_two(self):
        self.assertEqual(char_width("日"), 2)
        self.assertEqual(char_width("か"), 2)
        self.assertEqual(text_width("a日b"), 4)

    def test_zero_width_combining_is_counted_leniently(self):
        # We don't model grapheme clusters; the contract is "per code point".
        self.assertEqual(text_width("é"), 2)

    def test_strip_ansi_removes_sgr_and_csi(self):
        self.assertEqual(strip_ansi("\033[1mbold\033[0m"), "bold")
        self.assertEqual(strip_ansi("\033[38;2;1;2;3mcolor\033[0m!"), "color!")
        self.assertEqual(strip_ansi("plain"), "plain")
        # A lone escape start with no CSI body is left alone.
        self.assertEqual(strip_ansi("esc\033end"), "esc\033end")


class TestWrap(unittest.TestCase):
    def test_wraps_on_word_boundaries(self):
        self.assertEqual(
            wrap_text("one two three four", 10), ["one two", "three four"]
        )

    def test_unbreakable_word_is_hard_broken_at_column(self):
        # No space to break on, so it splits at the display boundary instead
        # of running past the column edge.
        self.assertEqual(
            wrap_text("supercalifragilistic", 5),
            ["super", "calif", "ragil", "istic"],
        )

    def test_word_exactly_fitting_is_not_broken(self):
        self.assertEqual(wrap_text("abcde", 5), ["abcde"])
        self.assertEqual(wrap_text("ab cd", 5), ["ab cd"])

    def test_preserves_blank_lines_and_indent_agnostic(self):
        self.assertEqual(wrap_text("a\n\nb", 80), ["a", "", "b"])

    def test_degenerate_width_returns_text_unchanged(self):
        self.assertEqual(wrap_text("some text", 0), ["some text"])

    def test_cjk_wraps_by_display_width(self):
        # CJK text uses no spaces, so it wraps at the glyph boundary; 6
        # display columns hold three full-width glyphs.
        self.assertEqual(wrap_text("日本語テスト", 6), ["日本語", "テスト"])

    def test_mixed_widths_wrap_cleanly(self):
        self.assertEqual(wrap_text("a日b日c", 3), ["a日", "b日", "c"])


class TestKeyClassification(unittest.TestCase):
    def test_control_chars_map_to_names(self):
        self.assertEqual(_classify("\x03").name, "ctrl_c")
        self.assertEqual(_classify("\x04").name, "ctrl_d")
        self.assertEqual(_classify("\x15").name, "ctrl_u")
        self.assertEqual(_classify("\x0a").name, "ctrl_j")

    def test_backspace_encodings(self):
        # \x08 is the plain Backspace key on every platform.
        self.assertEqual(_classify("\x08").name, "backspace")

    def test_enter_is_carriage_return(self):
        # Terminals send \r for the Return key.
        self.assertEqual(_classify("\r").name, "enter")

    def test_ctrl_j_is_not_enter(self):
        # \x0a is what POSIX sends for Ctrl+J; it must stay distinct from
        # Enter or the documented "ctrl+j inserts a newline" breaks.
        self.assertEqual(_classify("\x0a").name, "ctrl_j")

    def test_backspace_encodings(self):
        # \x08 is the plain Backspace key on every platform.
        self.assertEqual(_classify("\x08").name, "backspace")

    def test_backspace_x7f_is_platform_specific(self):
        # POSIX: \x7f is the ordinary Backspace key. Windows: plain Backspace
        # is \x08, so \x7f is Ctrl+Backspace instead.
        with unittest.mock.patch("term.sys.platform", "linux"):
            self.assertEqual(_classify("\x7f").name, "backspace")
        with unittest.mock.patch("term.sys.platform", "win32"):
            self.assertEqual(_classify("\x7f").name, "ctrl_backspace")

    def test_ctrl_backspace_windows_encoding(self):
        # Windows sends \x7f for Ctrl+Backspace while plain Backspace is \x08.
        with unittest.mock.patch("term.sys.platform", "win32"):
            self.assertEqual(_classify("\x7f").name, "ctrl_backspace")

    def test_ctrl_backspace_posix_encoding(self):
        # The byte most POSIX terminals emit for Ctrl+Backspace is \x17,
        # which is also Ctrl+W; both names erase a word.
        self.assertEqual(_classify("\x17").name, "ctrl_w")

    def test_ctrl_delete_csi_sequence(self):
        # "\x1b[3;5~" is Delete with the Ctrl modifier.
        self.assertEqual(_parse_csi("\x1b[3;5~").name, "ctrl_backspace")

    def test_plain_delete_still_works(self):
        self.assertEqual(_parse_csi("\x1b[3~").name, "delete")

    def test_shift_enter_kitty_encoding(self):
        # "ESC [ 13 ; 2 u" -- what kitty / WezTerm / Alacritty send for
        # Shift+Enter.
        self.assertEqual(_parse_csi("\x1b[13;2u").name, "shift_enter")

    def test_plain_enter_in_u_encoding(self):
        self.assertEqual(_parse_csi("\x1b[13u").name, "enter")

    def test_ctrl_enter_in_u_encoding(self):
        self.assertEqual(_parse_csi("\x1b[13;5u").name, "ctrl_enter")

    def test_shift_arrows(self):
        self.assertEqual(_parse_csi("\x1b[1;2A").name, "shift_up")
        self.assertEqual(_parse_csi("\x1b[1;2B").name, "shift_down")
        self.assertEqual(_parse_csi("\x1b[1;5A").name, "ctrl_up")

    def test_plain_arrow_when_modifier_is_none(self):
        self.assertEqual(_parse_csi("\x1b[A").name, "up")

    def test_bogus_modifier_falls_back_to_base(self):
        self.assertEqual(_parse_csi("\x1b[1;notanumberA").name, "up")

    def test_printable_chars_pass_through(self):
        key = _classify("a")
        self.assertEqual(key.ch, "a")
        self.assertIsNone(key.name)

    def test_csi_arrows(self):
        self.assertEqual(_parse_csi("\x1b[A").name, "up")
        self.assertEqual(_parse_csi("\x1b[B").name, "down")
        self.assertEqual(_parse_csi("\x1b[C").name, "right")
        self.assertEqual(_parse_csi("\x1b[D").name, "left")
        self.assertEqual(_parse_csi("\x1b[H").name, "home")
        self.assertEqual(_parse_csi("\x1b[F").name, "end")

    def test_csi_page_keys(self):
        self.assertEqual(_parse_csi("\x1b[5~").name, "pageup")
        self.assertEqual(_parse_csi("\x1b[6~").name, "pagedown")
        self.assertEqual(_parse_csi("\x1b[3~").name, "delete")

    def test_shift_tab(self):
        self.assertEqual(_parse_csi("\x1b[Z").name, "shift_tab")

    def test_alt_combination(self):
        key = _parse_csi("\x1ba")
        self.assertEqual(key.ch, "a")
        self.assertEqual(key.name, "alt_a")

    def test_single_bare_escape_is_named(self):
        self.assertEqual(_classify("\x1b").name, "esc")

    def test_key_repr_is_useful(self):
        self.assertIn("ctrl_c", repr(KeyEvent(name="ctrl_c")))


if __name__ == "__main__":
    unittest.main()
