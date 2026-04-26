"""YouTube search dan stream URL via yt-dlp."""

from __future__ import annotations

import yt_dlp

from .state import Track


def search(query: str, max_results: int = 8) -> list[Track]:
    """Cari YouTube, kembalikan list Track (tanpa download)."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "default_search": "ytsearch",
    }
    results: list[Track] = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        filtered = query if "ceramah" in query.lower() else f"{query} ceramah"
        info = ydl.extract_info(f"ytsearch{max_results}:{filtered}", download=False)
        for entry in (info.get("entries") or []):
            if not entry:
                continue
            dur_sec = int(entry.get("duration") or 0)
            m, s = divmod(dur_sec, 60)
            h, m = divmod(m, 60)
            duration = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
            results.append(Track(
                title=entry.get("title", "Unknown"),
                url=f"https://www.youtube.com/watch?v={entry['id']}",
                video_id=entry["id"],
                duration=duration,
                channel=entry.get("channel") or entry.get("uploader", "Unknown"),
            ))
    return results


def get_stream_url(youtube_url: str) -> str:
    """Ambil direct audio stream URL (tidak download file)."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio[ext=m4a]/bestaudio/best",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=False)
        formats = info.get("formats", [])
        audio_only = [
            f for f in formats
            if f.get("vcodec") == "none" and f.get("acodec") != "none"
        ]
        if audio_only:
            best = sorted(audio_only, key=lambda x: x.get("abr") or 0, reverse=True)[0]
            return best["url"]
        return info["url"]