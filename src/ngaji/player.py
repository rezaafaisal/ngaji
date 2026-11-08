"""Audio player — wrapper subprocess mpv, dikontrol via JSON IPC.

IPC dipakai (bukan SIGSTOP/SIGCONT) supaya:
- pause/resume, volume, dan seek bekerja di macOS, Linux, *dan* Windows
- posisi diambil langsung dari mpv (akurat walau buffering)
- mpv tidak ikut membaca keyboard dari terminal
"""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Optional

IS_WINDOWS = sys.platform == "win32"

_counter = itertools.count()


class PlayerNotFound(RuntimeError):
    pass


def install_hint() -> str:
    if IS_WINDOWS:
        return (
            "Install mpv lalu buka terminal baru:\n"
            "  scoop bucket add extras && scoop install mpv\n"
            "  (atau unduh dari https://mpv.io/installation/ dan tambahkan ke PATH)"
        )
    if sys.platform == "darwin":
        return "Install mpv:  brew install mpv"
    return (
        "Install mpv:\n"
        "  Ubuntu/Debian: sudo apt install mpv\n"
        "  Fedora       : sudo dnf install mpv\n"
        "  Arch         : sudo pacman -S mpv"
    )


def find_mpv() -> Optional[str]:
    path = shutil.which("mpv")
    if path:
        return path
    if IS_WINDOWS:
        # Lokasi umum kalau user belum menambahkan mpv ke PATH
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        for base in (
            os.path.join(program_files, "MPV Player"),  # winget: shinchiro.mpv
            program_files,
            os.path.expandvars(r"%USERPROFILE%\scoop\apps\mpv\current"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\mpv"),
        ):
            for cand in (os.path.join(base, "mpv.exe"), os.path.join(base, "mpv", "mpv.exe")):
                if os.path.isfile(cand):
                    return cand
    return None


class _IPC:
    """Koneksi request/response pendek ke mpv (satu koneksi per perintah)."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._req = itertools.count(1)
        self._lock = threading.Lock()

    def command(self, *args: Any, timeout: float = 1.0) -> Optional[dict]:
        req_id = next(self._req)
        payload = (json.dumps({"command": list(args), "request_id": req_id}) + "\n").encode()
        with self._lock:
            try:
                return self._win(payload, req_id) if IS_WINDOWS else self._unix(payload, req_id, timeout)
            except (OSError, ValueError):
                return None

    def _unix(self, payload: bytes, req_id: int, timeout: float) -> Optional[dict]:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(self.path)
            s.sendall(payload)
            buf = b""
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                chunk = s.recv(65536)
                if not chunk:
                    return None
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    if line.strip():
                        msg = json.loads(line)
                        if msg.get("request_id") == req_id:
                            return msg
        return None

    def _win(self, payload: bytes, req_id: int) -> Optional[dict]:
        with open(self.path, "r+b", buffering=0) as pipe:
            pipe.write(payload)
            for _ in range(200):
                line = pipe.readline()
                if not line:
                    return None
                if line.strip():
                    msg = json.loads(line)
                    if msg.get("request_id") == req_id:
                        return msg
        return None

    def get(self, prop: str) -> Any:
        r = self.command("get_property", prop)
        if r and r.get("error") == "success":
            return r.get("data")
        return None

    def set(self, prop: str, value: Any) -> bool:
        r = self.command("set_property", prop, value)
        return bool(r and r.get("error") == "success")


class AudioPlayer:
    """Non-blocking audio player berbasis mpv."""

    def __init__(self) -> None:
        mpv = find_mpv()
        if not mpv:
            raise PlayerNotFound("mpv tidak ditemukan.\n" + install_hint())
        self._mpv = mpv
        self._proc: Optional[subprocess.Popen] = None
        self._ipc: Optional[_IPC] = None
        self._paused = False
        self._last_pos = 0.0
        self._duration: Optional[float] = None
        self._volume = 80
        self._lock = threading.RLock()

    # ── Playback ──────────────────────────────────────────────────────────────

    def play(
        self,
        stream_url: str,
        start_pos: float = 0.0,
        volume: int = 80,
        headers: Optional[dict] = None,
        title: str = "",
    ) -> None:
        with self._lock:
            self.stop()
            if IS_WINDOWS:
                ipc_path = rf"\\.\pipe\ngaji-{os.getpid()}-{next(_counter)}"
            else:
                ipc_path = os.path.join(tempfile.gettempdir(), f"ngaji-{os.getpid()}-{next(_counter)}.sock")
                with contextlib.suppress(OSError):
                    os.unlink(ipc_path)

            cmd = [
                self._mpv,
                "--no-video",
                "--no-terminal",          # jangan baca keyboard / tulis ke terminal kita
                "--no-config",            # hindari config user yang aneh-aneh
                "--ytdl=no",              # URL sudah direct, tidak perlu ytdl_hook
                "--idle=no",
                "--audio-display=no",
                "--cache=yes",
                "--demuxer-max-bytes=64MiB",
                "--demuxer-readahead-secs=60",
                "--network-timeout=30",
                "--force-seekable=yes",
                f"--input-ipc-server={ipc_path}",
                f"--volume={int(volume)}",
            ]
            if start_pos > 0:
                cmd.append(f"--start={start_pos:.1f}")
            if title:
                cmd.append(f"--force-media-title={title}")
            headers = dict(headers or {})
            ua = headers.pop("User-Agent", None)
            if ua:
                cmd.append(f"--user-agent={ua}")
            fields = [f"{k}: {v}" for k, v in headers.items() if "," not in str(v)]
            if fields:
                cmd.append("--http-header-fields=" + ",".join(fields))
            cmd += ["--", stream_url]

            kwargs: dict[str, Any] = dict(
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            if IS_WINDOWS:
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            else:
                kwargs["start_new_session"] = True  # Ctrl+C di terminal tidak langsung membunuh mpv

            self._proc = subprocess.Popen(cmd, **kwargs)
            self._ipc = _IPC(ipc_path)
            self._paused = False
            self._last_pos = float(start_pos)
            self._duration = None
            self._volume = int(volume)

    def stop(self) -> None:
        with self._lock:
            proc, ipc = self._proc, self._ipc
            self._proc = None
            self._ipc = None
            self._paused = False
            if proc and proc.poll() is None:
                if ipc:
                    ipc.command("quit", timeout=0.5)
                try:
                    proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            if ipc and not IS_WINDOWS:
                with contextlib.suppress(OSError):
                    os.unlink(ipc.path)

    def toggle_pause(self) -> bool:
        """Toggle pause. Return True jika sekarang paused."""
        with self._lock:
            if not self.is_active:
                return self._paused
            target = not self._paused
            if self._ipc and self._ipc.set("pause", target):
                self._paused = target
                self._refresh_position()
            return self._paused

    def set_volume(self, volume: int) -> None:
        with self._lock:
            self._volume = int(volume)
            if self.is_active and self._ipc:
                self._ipc.set("volume", self._volume)

    def seek(self, seconds: float, relative: bool = True) -> bool:
        with self._lock:
            if not (self.is_active and self._ipc):
                return False
            mode = "relative" if relative else "absolute"
            r = self._ipc.command("seek", seconds, mode)
            ok = bool(r and r.get("error") == "success")
            if ok:
                time.sleep(0.15)
                self._refresh_position()
            return ok

    def poll_ended(self) -> Optional[int]:
        """Jika mpv berhenti sendiri (track selesai / error), kembalikan exit code
        sekali saja. None jika masih jalan atau memang tidak ada yang diputar."""
        with self._lock:
            if self._proc is None or self._proc.poll() is None:
                return None
            rc = self._proc.returncode
            ipc = self._ipc
            self._proc = None
            self._ipc = None
            self._paused = False
            if ipc and not IS_WINDOWS:
                with contextlib.suppress(OSError):
                    os.unlink(ipc.path)
            return rc

    # ── State ─────────────────────────────────────────────────────────────────

    def _refresh_position(self) -> None:
        if not self._ipc:
            return
        pos = self._ipc.get("time-pos")
        if isinstance(pos, (int, float)):
            self._last_pos = float(pos)
        if self._duration is None:
            dur = self._ipc.get("duration")
            if isinstance(dur, (int, float)) and dur > 0:
                self._duration = float(dur)

    @property
    def position(self) -> float:
        with self._lock:
            if self.is_active and not self._paused:
                self._refresh_position()
            return self._last_pos

    @property
    def duration(self) -> Optional[float]:
        return self._duration

    @property
    def is_active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def is_playing(self) -> bool:
        return self.is_active and not self._paused

    @property
    def is_paused(self) -> bool:
        return self.is_active and self._paused

