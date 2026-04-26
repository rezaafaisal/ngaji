"""Audio player — wrapper subprocess untuk mpv."""

from __future__ import annotations

import shutil
import signal
import subprocess
import threading
import time
from typing import Optional


def _find_player() -> str:
    for cmd in ("mpv", "ffplay"):
        if shutil.which(cmd):
            return cmd
    raise RuntimeError(
        "ffplay / mpv tidak ditemukan.\n"
        "macOS : brew install ffmpeg\n"
        "Ubuntu: sudo apt install ffmpeg\n"
        "Arch  : sudo pacman -S ffmpeg\n"
        "Atau: brew install mpv (lebih berat)"
    )


class AudioPlayer:
    """Non-blocking audio player via mpv (atau ffplay sebagai fallback)."""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._paused = False
        self._start_time: float = 0.0
        self._position_offset: float = 0.0
        self._lock = threading.Lock()
        self._cmd = _find_player()

    # ── Playback ──────────────────────────────────────────────────────────────

    def play(self, stream_url: str, start_pos: float = 0.0, volume: int = 80) -> None:
        self.stop()
        self._position_offset = start_pos
        self._start_time = time.time()
        self._paused = False

        if self._cmd == "mpv":
            cmd = [
                "mpv", "--no-video", "--really-quiet",
                f"--volume={volume}",
                f"--start={int(start_pos)}",
                stream_url,
            ]
        else:
            cmd = [
                "ffplay", "-nodisp", "-autoexit",
                "-loglevel", "quiet",
                "-ss", str(int(start_pos)),
                "-volume", str(volume),
                stream_url,
            ]

        with self._lock:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

    def stop(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None
            self._paused = False

    def toggle_pause(self) -> bool:
        """Toggle pause. Return True jika sekarang paused."""
        if not self._proc or self._proc.poll() is not None:
            return self._paused
        if self._paused:
            self._proc.send_signal(signal.SIGCONT)
            # Koreksi start_time supaya posisi tidak lompat
            self._start_time = time.time() - self._position_offset
            self._paused = False
        else:
            self._position_offset = self.position
            self._proc.send_signal(signal.SIGSTOP)
            self._paused = True
        return self._paused

    # ── State ─────────────────────────────────────────────────────────────────

    @property
    def position(self) -> float:
        if self._paused:
            return self._position_offset
        return self._position_offset + (time.time() - self._start_time)

    @property
    def is_playing(self) -> bool:
        return (
            self._proc is not None
            and self._proc.poll() is None
            and not self._paused
        )

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_finished(self) -> bool:
        return self._proc is not None and self._proc.poll() is not None