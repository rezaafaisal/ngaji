"""YouTube search dan stream URL via yt-dlp."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yt_dlp
from yt_dlp.utils import DownloadError

from .state import Track

# Format audio yang bisa di-stream langsung via HTTP (bukan HLS/DASH fragment)
_AUDIO_FORMAT = (
    "bestaudio[protocol^=http][acodec!=none]/"
    "bestaudio/"
    "best[protocol^=http]/best"
)


class YouTubeError(RuntimeError):
    pass


class _QuietLogger:
    """Telan log yt-dlp supaya tidak merusak tampilan terminal."""

    def debug(self, msg: str) -> None: ...
    def info(self, msg: str) -> None: ...
    def warning(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...


def js_runtimes() -> dict:
    """Runtime JS yang terpasang — dibutuhkan yt-dlp untuk memecahkan
    challenge YouTube (tanpa ini stream bisa lambat / gagal)."""
    found: dict = {}
    for name in ("deno", "node", "bun", "qjs"):
        if shutil.which(name):
            found["quickjs" if name == "qjs" else name] = {}
    if "deno" not in found:
        # Installer resmi deno memasang ke ~/.deno/bin yang sering belum ada di PATH
        exe = "deno.exe" if os.name == "nt" else "deno"
        cand = Path.home() / ".deno" / "bin" / exe
        if cand.is_file():
            found = {"deno": {"path": str(cand)}} | found
    return found


def _base_opts() -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _QuietLogger(),
        "socket_timeout": 20,
        "retries": 3,
        "noplaylist": True,
    }
    runtimes = js_runtimes()
    if runtimes:
        opts["js_runtimes"] = runtimes
    return opts


def _clean_error(e: Exception) -> str:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e))
    msg = msg.replace("ERROR: ", "").strip()
    if "Sign in to confirm" in msg or "not a bot" in msg:
        return "YouTube meminta verifikasi (bot check). Coba lagi nanti atau ganti jaringan."
    if "Private video" in msg:
        return "Video ini privat."
    if "Video unavailable" in msg:
        return "Video tidak tersedia."
    if "Requested format is not available" in msg:
        return "Format audio tidak tersedia. Coba update: pip install -U ngaji yt-dlp"
    return msg.splitlines()[0][:200] if msg else "Gagal menghubungi YouTube."


def format_duration(seconds: float) -> str:
    if not seconds:
        return "LIVE"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def search(query: str, max_results: int = 10) -> list[Track]:
    """Cari YouTube, kembalikan list Track (tanpa download)."""
    opts = _base_opts() | {"extract_flat": "in_playlist", "skip_download": True}
    filtered = query if "ceramah" in query.lower() else f"{query} ceramah"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{max_results}:{filtered}", download=False)
    except DownloadError as e:
        raise YouTubeError(_clean_error(e)) from None

    results: list[Track] = []
    for entry in (info or {}).get("entries") or []:
        if not entry:
            continue
        vid = entry.get("id") or ""
        # Lewati channel / playlist yang kadang muncul di hasil search
        if len(vid) != 11 or entry.get("ie_key") not in (None, "Youtube"):
            continue
        if entry.get("live_status") in ("is_upcoming",):
            continue
        results.append(Track(
            title=entry.get("title") or "Tanpa judul",
            url=f"https://www.youtube.com/watch?v={vid}",
            video_id=vid,
            duration=format_duration(entry.get("duration") or 0),
            channel=entry.get("channel") or entry.get("uploader") or "-",
        ))
    return results


@dataclass
class Stream:
    url: str
    headers: dict = field(default_factory=dict)
    duration: Optional[float] = None


def get_stream(youtube_url: str) -> Stream:
    """Ambil direct audio stream URL + header HTTP yang dibutuhkan (tidak download)."""
    opts = _base_opts() | {"format": _AUDIO_FORMAT, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
    except DownloadError as e:
        raise YouTubeError(_clean_error(e)) from None

    if not info:
        raise YouTubeError("Tidak bisa membaca info video.")

    # Saat satu format dipilih, yt-dlp menaruh url & headernya di top-level
    fmt = info
    if not fmt.get("url") and info.get("requested_formats"):
        fmt = info["requested_formats"][0]
    url = fmt.get("url")
    if not url:
        raise YouTubeError("Stream audio tidak ditemukan.")
    return Stream(
        url=url,
        headers=dict(fmt.get("http_headers") or info.get("http_headers") or {}),
        duration=info.get("duration"),
    )


def get_stream_url(youtube_url: str) -> str:
    """Kompatibilitas lama."""
    return get_stream(youtube_url).url
