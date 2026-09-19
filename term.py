"""
Low-level terminal control: raw input, screen size, ANSI escapes.

Uses only the standard library. On Windows it enables virtual-terminal
processing via ctypes and reads keys through msvcrt; on POSIX it uses
termios + select. Either way the API above the platform split is the same.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

# --------------------------------------------------------------------- escapes

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
UNDERLINE = "\033[4m"

# 8-color foreground helpers.
class FG:
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"


def rgb(r: int, g: int, b: int) -> str:
    return f"\033[38;2;{r};{g};{b}m"


def hide_cursor() -> str:
    return "\033[?25l"


def show_cursor() -> str:
    return "\033[?25h"


def clear_screen() -> str:
    return "\033[2J"


def alt_screen(on: bool) -> str:
    return "\033[?1049h" if on else "\033[?1049l"


def move_to(row: int, col: int) -> str:
    """1-indexed cursor position."""
    return f"\033[{row};{col}H"


def clear_line() -> str:
    return "\033[2K"


def scroll_region(top: int, bottom: int) -> str:
    """Set the scrolling region, 1-indexed inclusive."""
    return f"\033[{top};{bottom}r"


# ------------------------------------------------------------------ text widths

def char_width(ch: str) -> int:
    """Display width of one character: 2 for CJK/fullwidth, else 1."""
    code = ord(ch)
    if code < 0x1100:
        return 1
    ranges = (
        (0x1100, 0x115F), (0x2E80, 0x303E), (0x3041, 0x33FF), (0x3400, 0x4DBF),
        (0x4E00, 0x9FFF), (0xA000, 0xA4CF), (0xAC00, 0xD7A3), (0xF900, 0xFAFF),
        (0xFE30, 0xFE4F), (0xFF00, 0xFF60), (0xFFE0, 0xFFE6),
    )
    return 2 if any(lo <= code <= hi for lo, hi in ranges) else 1


def text_width(s: str) -> int:
    return sum(char_width(c) for c in s)


def wrap_text(text: str, width: int) -> list[str]:
    """Word wrap to `width` display columns. Preserves blank lines."""
    if width <= 0:
        return [text]
    out: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            out.append("")
            continue
        line, w = "", 0
        for word in paragraph.split(" "):
            ww = text_width(word)
            sep = 0 if not line else 1
            if w + sep + ww > width and line:
                out.append(line)
                line, w = word, ww
            else:
                line = f"{line} {word}" if line else word
                w += sep + ww
        out.append(line)
    return out


def strip_ansi(s: str) -> str:
    """Remove escape sequences so len() reflects visible text."""
    out: list[str] = []
    i = 0
    while i < len(s):
        if s[i] == "\033" and i + 1 < len(s) and s[i + 1] == "[":
            j = i + 2
            while j < len(s) and not (0x40 <= ord(s[j]) <= 0x7E):
                j += 1
            i = j + 1
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


# ---------------------------------------------------------------- terminal size

def get_size() -> tuple[int, int]:
    """Return (rows, cols), falling back to 24x80."""
    def env_fallback() -> tuple[int, int]:
        try:
            cols = int(os.environ.get("COLUMNS", "80"))
            rows = int(os.environ.get("LINES", "24"))
            return max(rows, 1), max(cols, 1)
        except ValueError:
            return 24, 80

    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class _CSBI(ctypes.Structure):
                _fields_ = [
                    ("x", wintypes.SHORT), ("y", wintypes.SHORT),
                    ("cx", wintypes.SHORT), ("cy", wintypes.SHORT),
                    ("wAttributes", wintypes.WORD),
                    ("left", wintypes.SHORT), ("top", wintypes.SHORT),
                    ("right", wintypes.SHORT), ("bottom", wintypes.SHORT),
                    ("dwMaximumWindowSizeX", wintypes.SHORT),
                    ("dwMaximumWindowSizeY", wintypes.SHORT),
                ]

            h = ctypes.windll.kernel32.GetStdHandle(-11)
            csbi = _CSBI()
            if ctypes.windll.kernel32.GetConsoleScreenBufferInfo(h, ctypes.byref(csbi)):
                cols = max(csbi.right - csbi.left + 1, 1)
                rows = max(csbi.bottom - csbi.top + 1, 1)
                return rows, cols
        except Exception:  # noqa: BLE001 - size detection must never crash the UI
            pass
        return env_fallback()

    # POSIX: prefer the TIOCGWINSZ ioctl on a real tty.
    try:
        import fcntl
        import struct
        import termios

        with open(os.devnull, "rb") as _dev_null:  # keep the import honest
            pass
        for fd in (sys.stdout.fileno(), sys.stdin.fileno(), 2):
            try:
                packed = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\x00" * 8)
                rows, cols = struct.unpack("hhhh", packed)[:2]
                if rows and cols:
                    return max(rows, 1), max(cols, 1)
            except OSError:
                continue
    except ImportError:
        pass
    return env_fallback()


# --------------------------------------------------------------------- raw input

@dataclass
class KeyEvent:
    """A logical key. `ch` is a printable char or None for special keys."""
    ch: str | None = None
    name: str | None = None  # up/down/left/right/enter/backspace/tab/esc/etc.

    def __repr__(self) -> str:
        return f"KeyEvent(ch={self.ch!r}, name={self.name!r})"


class RawInput:
    """
    Switches stdin to raw mode and reads one KeyEvent at a time.

    Used as a context manager: on POSIX it saves and restores termios state;
    on Windows it relies on msvcrt, which needs no mode change.
    """

    def __init__(self) -> None:
        self._posix_fd = -1
        self._posix_old: bytes | None = None
        self._enabled = False

    def __enter__(self) -> "RawInput":
        if sys.platform != "win32":
            import termios
            import tty

            self._posix_fd = sys.stdin.fileno()
            self._posix_old = termios.tcgetattr(self._posix_fd)
            tty.setraw(self._posix_fd)
        self._enabled = True
        return self

    def __exit__(self, *exc: object) -> None:
        if self._posix_fd >= 0 and self._posix_old is not None:
            import termios

            termios.tcsetattr(self._posix_fd, termios.TCSADRAIN, self._posix_old)
        self._enabled = False

    def available(self) -> bool:
        """Whether a key is ready to read without blocking."""
        if not self._enabled:
            return False
        if sys.platform == "win32":
            import msvcrt

            return msvcrt.kbhit()
        import select

        return bool(select.select([sys.stdin], [], [], 0)[0])

    def read_key(self) -> KeyEvent | None:
        """Read one key if available, else None (never blocks)."""
        if not self.available():
            return None

        if sys.platform == "win32":
            import msvcrt

            ch = msvcrt.getwch()
            if ch == "\x00" or ch == "à":  # extended key prefix
                code = ord(msvcrt.getwch())
                return KeyEvent(name=_WIN_EXT.get(code))
            return _classify(ch)

        import os

        try:
            data = os.read(sys.stdin.fileno(), 64)
        except OSError:
            return None
        return _parse_csi(data.decode("utf-8", errors="replace"))


# Special-key lookup for Windows extended (non-ASCII) key codes.
_WIN_EXT = {
    0x48: "up", 0x50: "down", 0x4B: "left", 0x4D: "right",
    0x47: "home", 0x4F: "end", 0x49: "pageup", 0x51: "pagedown",
    0x52: "insert", 0x53: "delete",
    0x3B: "f1", 0x3C: "f2", 0x3D: "f3", 0x3E: "f4", 0x3F: "f5",
    0x40: "f6", 0x41: "f7", 0x42: "f8", 0x43: "f9", 0x44: "f10",
    0x52: "insert",
}


_CTRL_NAMES = {
    "\x01": "ctrl_a", "\x02": "ctrl_b", "\x03": "ctrl_c", "\x04": "ctrl_d",
    "\x05": "ctrl_e", "\x06": "ctrl_f", "\x07": "ctrl_g", "\x08": "ctrl_h",
    "\x0b": "ctrl_k", "\x0c": "ctrl_l", "\x0e": "ctrl_n", "\x0f": "ctrl_o",
    "\x10": "ctrl_p", "\x11": "ctrl_q", "\x12": "ctrl_r", "\x13": "ctrl_s",
    "\x14": "ctrl_t", "\x15": "ctrl_u", "\x16": "ctrl_v", "\x17": "ctrl_w",
    "\x18": "ctrl_x", "\x19": "ctrl_y", "\x1a": "ctrl_z",
    "\x0a": "ctrl_j",  # often sent by terminals for ctrl+j / shift+enter
}


def _classify(ch: str) -> KeyEvent:
    if ch == "\r" or ch == "\n":
        return KeyEvent(name="enter")
    # Backspace: \x7f on POSIX, \x08 on Windows (msvcrt.getwch). Both must
    # map to the same logical key or the editor can't delete on Windows.
    if ch in ("\x7f", "\x08"):
        return KeyEvent(name="backspace")
    if ch == "\t":
        return KeyEvent(name="tab")
    if ch == "\x1b":
        return KeyEvent(name="esc")
    if ch in _CTRL_NAMES:
        return KeyEvent(name=_CTRL_NAMES[ch])
    if not ch.isprintable() and ch not in (" ",):
        return KeyEvent(ch=ch)  # let callers decide on odd control chars
    return KeyEvent(ch=ch)


def _parse_csi(seq: str) -> KeyEvent:
    """Decode a possibly-multibyte ANSI key sequence into a KeyEvent."""
    if not seq:
        return None  # type: ignore[return-value]
    if len(seq) == 1:
        return _classify(seq)

    # Arrow keys and friends: ESC [ X or ESC O X
    if seq[:2] in ("\x1b[", "\x1bO"):
        tail = seq[-1]
        named = {
            "A": "up", "B": "down", "C": "right", "D": "left",
            "H": "home", "F": "end",
        }
        if tail in named:
            return KeyEvent(name=named[tail])
        if tail == "Z":
            return KeyEvent(name="shift_tab")
        if tail == "~":
            num = seq[2:-1]
            nums = {
                "1": "home", "4": "end", "5": "pageup", "6": "pagedown",
                "2": "insert", "3": "delete",
                "15": "f5", "17": "f6", "18": "f7", "19": "f8",
                "20": "f9", "21": "f10", "23": "f11", "24": "f12",
            }
            return KeyEvent(name=nums.get(num))
    # Alt+key arrives as ESC followed by the key.
    if seq[0] == "\x1b" and len(seq) >= 2:
        base = _classify(seq[1])
        if base.ch:
            return KeyEvent(ch=base.ch, name="alt_" + base.ch)
    return _classify(seq[0])


def enable_windows_ansi() -> bool:
    """On Windows 10+, turn on ANSI escape processing. No-op elsewhere."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        ENABLE_VT = 0x0004
        for handle in (-11, -12):  # stdout, stderr
            h = kernel32.GetStdHandle(handle)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                kernel32.SetConsoleMode(h, mode.value | ENABLE_VT)
        return True
    except Exception:  # noqa: BLE001
        return False
