"""Baca keypress satu per satu — lintas platform (POSIX termios / Windows msvcrt).

Nilai kembalian dinormalisasi: "up", "down", "left", "right", "enter",
"esc", "backspace", "ctrl-c", atau karakter biasa.
"""

from __future__ import annotations

import contextlib
import sys
import time
from typing import Iterator, Optional

if sys.platform == "win32":
    import msvcrt

    _WIN_SPECIAL = {"H": "up", "P": "down", "K": "left", "M": "right"}

    @contextlib.contextmanager
    def raw_mode() -> Iterator[None]:
        yield

    def read_key(timeout: float = 0.5) -> Optional[str]:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):
                    return _WIN_SPECIAL.get(msvcrt.getwch())
                return _normalize(ch)
            time.sleep(0.02)
        return None

else:
    import os
    import select
    import termios
    import tty

    _ESC_SEQ = {"A": "up", "B": "down", "C": "right", "D": "left"}

    @contextlib.contextmanager
    def raw_mode() -> Iterator[None]:
        """cbreak: echo mati, input per karakter, output tetap normal."""
        if not sys.stdin.isatty():
            yield
            return
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        try:
            yield
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def _getch(timeout: float) -> Optional[str]:
        fd = sys.stdin.fileno()
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return None
        # os.read langsung — sys.stdin.read() punya buffer sendiri yang
        # bikin select() tidak melihat sisa byte escape sequence
        data = os.read(fd, 1)
        return data.decode("utf-8", "ignore") if data else None

    def read_key(timeout: float = 0.5) -> Optional[str]:
        ch = _getch(timeout)
        if ch is None:
            return None
        if ch == "\x1b":
            ch2 = _getch(0.05)
            if ch2 is None:
                return "esc"
            if ch2 in ("[", "O"):
                ch3 = _getch(0.05)
                return _ESC_SEQ.get(ch3 or "", None)
            return "esc"
        return _normalize(ch)


def _normalize(ch: str) -> str:
    if ch in ("\r", "\n"):
        return "enter"
    if ch == "\x1b":
        return "esc"
    if ch == "\x03":
        return "ctrl-c"
    if ch in ("\x7f", "\x08"):
        return "backspace"
    return ch
