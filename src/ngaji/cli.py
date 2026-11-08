"""Main CLI — entry point untuk command `ngaji`.

REPL sederhana: setiap perintah print hasilnya sekali, input via prompt biasa.
Ketik / di prompt untuk daftar perintah dengan autocomplete.
Status track yang sedang diputar tampil live di toolbar bawah.
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
import threading
import time
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import ANSI
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .db import (
    add_history, add_to_playlist, create_playlist, delete_playlist, get_history,
    get_likes, get_playlist_tracks, get_playlists, init_db, is_liked, toggle_like,
    upsert_track,
)
from .keys import raw_mode, read_key
from .player import AudioPlayer, PlayerNotFound, find_mpv, install_hint
from .state import PlayerState, Track
from .youtube import YouTubeError, get_stream, js_runtimes, search

console = Console()

# ── Slash commands untuk autocomplete ──────────────────────────────────────────
_SLASH_CMDS: list[tuple[str, str, bool]] = [
    ("search",    "Cari ceramah di YouTube",                True),
    ("np",        "Now playing — status sekarang",          False),
    ("queue",     "Lihat & kelola antrian",                 False),
    ("likes",     "Daftar ceramah yang di-like",            False),
    ("history",   "Riwayat ceramah terakhir",               False),
    ("playlists", "Lihat & kelola playlist",                False),
    ("playlist",  "new|add|play|show|del <nama>",           True),
    ("pause",     "Pause / Resume",                         False),
    ("next",      "Track berikutnya",                       False),
    ("prev",      "Track sebelumnya",                       False),
    ("seek",      "Lompat: +30, -30, atau 12:30",           True),
    ("stop",      "Hentikan pemutaran",                     False),
    ("volume",    "Atur volume (0-100)",                    True),
    ("vol+",      "Volume naik 10",                         False),
    ("vol-",      "Volume turun 10",                        False),
    ("like",      "Like / unlike track sekarang",           False),
    ("clear",     "Kosongkan queue",                        False),
    ("help",      "Bantuan perintah",                       False),
    ("quit",      "Keluar",                                 False),
]

_MAX_RETRIES = 2


class _SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if " " in text or not text.startswith("/"):
            return
        after = text[1:].lower()
        for name, desc, needs_arg in _SLASH_CMDS:
            if name.startswith(after):
                yield Completion(
                    name + (" " if needs_arg else ""),
                    start_position=-len(after),
                    display=f"/{name}",
                    display_meta=desc,
                )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _fmt(seconds: float) -> str:
    h, rem = divmod(int(max(0, seconds)), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _parse_duration(s: str) -> float:
    try:
        total = 0
        for part in s.strip().split(":"):
            total = total * 60 + int(part)
        return float(total)
    except (ValueError, AttributeError):
        return 0.0


def _parse_index(raw: str, prefix: str, n: int) -> Optional[int]:
    """'d3' / 'd 3' → 2 (0-based) jika valid."""
    num = raw[len(prefix):].strip()
    if num.isdigit() and 1 <= int(num) <= n:
        return int(num) - 1
    return None


def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# ── App ────────────────────────────────────────────────────────────────────────

class NgajiApp:
    def __init__(self) -> None:
        init_db()
        self.state = PlayerState.load()
        self.player = AudioPlayer()
        self._running = True
        self._lock = threading.RLock()
        self._notice = ""
        self._notice_until = 0.0
        self._retries = 0
        self._loading = False
        self._session: PromptSession = PromptSession(
            completer=_SlashCompleter(),
            complete_while_typing=True,
        )

    # ── Notifikasi & status ───────────────────────────────────────────────────

    def _notify(self, msg: str, secs: float = 8.0) -> None:
        self._notice = msg
        self._notice_until = time.monotonic() + secs

    def _total_seconds(self, track: dict) -> float:
        return self.player.duration or _parse_duration(track.get("duration", ""))

    def _status_icon(self) -> str:
        if self._loading:
            return "…"
        if self.player.is_paused:
            return "⏸"
        if self.player.is_playing:
            return "▶"
        return "⏹"

    def _position(self) -> float:
        if self.player.is_active:
            return self.player.position
        return self.state.position_seconds

    def _toolbar(self):
        track = self.state.current_track()
        parts: list[tuple[str, str]] = []
        if track:
            total = self._total_seconds(track)
            parts += [
                ("bold", f" {self._status_icon()} "),
                ("", _trunc(track["title"], 48)),
                ("", f"  {_fmt(self._position())}/{_fmt(total) if total else track['duration']}"),
                ("", f"  🔊{self.state.volume}%"),
                ("", f"  [{self.state.current_index + 1}/{len(self.state.queue)}]"),
            ]
        else:
            parts.append(("", " Ketik /search <kata kunci> untuk mulai · /help bantuan"))
        if self._notice and time.monotonic() < self._notice_until:
            parts.append(("", f"  · {self._notice}"))
        return parts

    def _header(self) -> Panel:
        return Panel(
            f"[bold green]🕌 ngaji[/bold green] [dim]v{__version__}[/dim]  "
            "[dim]YouTube audio player untuk terminal[/dim]",
            border_style="green",
        )

    def _mini_status(self) -> str:
        track = self.state.current_track()
        if not track:
            return ""
        liked = " [red]♥[/red]" if is_liked(track["video_id"]) else ""
        total = self._total_seconds(track)
        return (
            f"  [dim]{self._status_icon()}[/dim] [bold]{_trunc(track['title'], 50)}[/bold]{liked}"
            f"  [cyan]{_fmt(self._position())}[/cyan]"
            f"[dim]/{_fmt(total) if total else track['duration']}  🔊{self.state.volume}%[/dim]"
        )

    def _progress_bar(self, width: int = 40) -> str:
        track = self.state.current_track()
        if not track:
            return ""
        total = self._total_seconds(track)
        pos = self._position()
        filled = int(min(1.0, pos / total) * width) if total > 0 else 0
        return (
            f"[green]{'█' * filled}[/green][dim]{'░' * (width - filled)}[/dim]"
            f"  [cyan]{_fmt(pos)}[/cyan] [dim]/ {_fmt(total) if total else track['duration']}[/dim]"
        )

    # ── Playback engine ──────────────────────────────────────────────────────

    def _fetch_and_play(self, track: Optional[dict], resume_pos: float = 0.0,
                        quiet: bool = False, retry: bool = False) -> bool:
        """Ambil stream URL lalu putar. quiet=True untuk panggilan dari thread background."""
        if not track:
            return False
        with self._lock:
            if not retry:
                self._retries = 0
            self._loading = True
            if not quiet:
                console.print(f"  [green]Memuat:[/green] {_trunc(track['title'], 60)} …")
            try:
                stream = get_stream(track["url"])
                self.player.play(
                    stream.url, start_pos=resume_pos, volume=self.state.volume,
                    headers=stream.headers, title=track["title"],
                )
            except YouTubeError as e:
                self._report(f"Gagal memutar: {e}", quiet)
                return False
            except Exception as e:  # noqa: BLE001 — jangan sampai REPL mati
                self._report(f"Gagal memutar: {e}", quiet)
                return False
            finally:
                self._loading = False

            self.state.position_seconds = resume_pos
            self.state.save()
            if not retry:
                try:
                    upsert_track(track)
                    add_history(track["video_id"])
                except Exception:  # noqa: BLE001
                    pass
            return True

    def _report(self, msg: str, quiet: bool) -> None:
        if quiet:
            self._notify(msg, 15)
        else:
            console.print(f"  [red]{msg}[/red]")

    def _on_track_end(self, rc: int) -> None:
        """Dipanggil watcher ketika mpv berhenti sendiri."""
        track = self.state.current_track()
        if not track:
            return
        pos = self.player.position
        total = self._total_seconds(track)
        cut_short = rc != 0 or (total > 0 and pos < total - 15)

        # Stream putus di tengah (URL kadaluarsa / jaringan) → ambil URL baru & lanjut
        if cut_short and self._retries < _MAX_RETRIES:
            self._retries += 1
            self._notify(f"Stream terputus, menyambung ulang ({self._retries}/{_MAX_RETRIES})…")
            if self._fetch_and_play(track, resume_pos=max(0.0, pos - 2), quiet=True, retry=True):
                return

        if cut_short and self._retries >= _MAX_RETRIES and rc != 0:
            self._notify("Track gagal diputar, lanjut ke berikutnya.", 10)

        nxt = self.state.next_track()
        if nxt:
            self._fetch_and_play(nxt, quiet=True)
        else:
            self.state.position_seconds = 0.0
            self.state.save()
            self._notify("Queue selesai.", 10)

    def _watch(self) -> None:
        last_save = time.monotonic()
        while self._running:
            time.sleep(0.5)
            try:
                with self._lock:
                    rc = self.player.poll_ended()
                    if rc is not None:
                        self._on_track_end(rc)
                        continue
                    if self.player.is_active:
                        pos = self.player.position  # juga menyegarkan cache posisi
                        if time.monotonic() - last_save > 5:
                            self.state.position_seconds = pos
                            self.state.save()
                            last_save = time.monotonic()
            except Exception as e:  # noqa: BLE001
                self._notify(f"Error: {e}")

    def _play_track(self, track: Track) -> None:
        self.state.current_index = self.state.add_to_queue(track)
        self.state.position_seconds = 0.0
        self.state.save()
        self._fetch_and_play(self.state.current_track())

    # ── Pages ─────────────────────────────────────────────────────────────────

    def _page_search(self, query: str = "") -> None:
        while True:  # cari lagi dengan '/' tanpa rekursi
            if not query:
                try:
                    query = self._session.prompt("  Kata kunci: ").strip()
                except (EOFError, KeyboardInterrupt):
                    return
                if not query:
                    return

            with console.status(f"[yellow]Mencari: {query} …[/yellow]"):
                try:
                    items = search(query)
                except YouTubeError as e:
                    console.print(f"  [red]Gagal mencari: {e}[/red]")
                    return
                except Exception as e:  # noqa: BLE001
                    console.print(f"  [red]Gagal mencari: {e}[/red]")
                    return

            if not items:
                console.print("  [red]Tidak ada hasil.[/red]")
                return

            if not sys.stdin.isatty():
                self._render_search(query, items, -1, "")
                return

            action, cursor = self._search_nav(query, items)
            if action == "play":
                self._play_track(items[cursor])
                return
            if action != "again":
                return
            query = ""

    def _render_search(self, query: str, items: list[Track], cursor: int, msg: str):
        tbl = Table(title=f'🔍 "{query}"', border_style="cyan", expand=False)
        tbl.add_column("", width=1)
        tbl.add_column("#", style="dim", width=3, justify="right")
        tbl.add_column("Judul", max_width=56, no_wrap=True)
        tbl.add_column("Channel", max_width=22, no_wrap=True)
        tbl.add_column("Durasi", style="cyan", width=8, justify="right")
        queued = {q["video_id"] for q in self.state.queue}
        for i, tr in enumerate(items):
            sel = i == cursor
            mark = " [dim]+[/dim]" if tr.video_id in queued else ""
            tbl.add_row(
                "[bold green]›[/bold green]" if sel else " ",
                str(i + 1),
                Text.from_markup(f"{Text(_trunc(tr.title, 54)).markup}{mark}",
                                 style="bold green" if sel else ""),
                Text(_trunc(tr.channel, 22), style="cyan" if sel else "dim"),
                tr.duration,
            )
        parts: list = []
        s = self._mini_status()
        if s:
            parts.append(Text.from_markup(s))
        parts.append(tbl)
        if msg:
            parts.append(Text.from_markup(f"  {msg}"))
        parts.append(Text(
            "  ↑↓ pilih · Enter putar · a tambah queue · / cari lagi · Esc/q kembali",
            style="dim",
        ))
        group = Group(*parts)
        if cursor < 0:
            console.print(group)
        return group

    def _search_nav(self, query: str, items: list[Track]) -> tuple[str, int]:
        cursor, msg = 0, ""
        last_draw = 0.0
        with raw_mode(), Live(
            self._render_search(query, items, cursor, msg),
            console=console, auto_refresh=False, transient=True,
        ) as live:
            while True:
                key = read_key(0.25)
                if key is None:
                    if time.monotonic() - last_draw >= 1.0:  # update mini status
                        live.update(self._render_search(query, items, cursor, msg), refresh=True)
                        last_draw = time.monotonic()
                    continue
                if key in ("esc", "q", "ctrl-c"):
                    return "back", cursor
                if key in ("up", "k"):
                    cursor = (cursor - 1) % len(items)
                elif key in ("down", "j"):
                    cursor = (cursor + 1) % len(items)
                elif key == "enter":
                    return "play", cursor
                elif key.isdigit() and key != "0" and int(key) <= len(items):
                    cursor = int(key) - 1
                elif key in ("a", "A"):
                    tr = items[cursor]
                    if self.state.index_of(tr.video_id) >= 0:
                        msg = "[yellow]Sudah ada di queue.[/yellow]"
                    else:
                        self.state.add_to_queue(tr)
                        if self.state.current_index < 0:
                            self.state.current_index = 0
                        self.state.save()
                        msg = f"[green]+ Ditambah ke queue:[/green] {Text(_trunc(tr.title, 40)).markup}"
                elif key == "/":
                    return "again", cursor
                live.update(self._render_search(query, items, cursor, msg), refresh=True)
                last_draw = time.monotonic()

    def _page_player(self) -> None:
        track = self.state.current_track()
        if not track:
            console.print("  [dim]Tidak ada yang diputar.[/dim]")
            return
        heart = "  [red]♥[/red]" if is_liked(track["video_id"]) else ""
        if self._loading:
            status = "[yellow]…  Memuat[/yellow]"
        elif self.player.is_paused:
            status = "[yellow]⏸  Pause[/yellow]"
        elif self.player.is_playing:
            status = "[green]▶  Memutar[/green]"
        else:
            status = "[dim]⏹  Berhenti[/dim]"

        console.print(Panel(
            f"{status}{heart}\n\n"
            f"[bold]{Text(track['title']).markup}[/bold]\n"
            f"[dim]{Text(track['channel']).markup}[/dim]\n\n"
            f"{self._progress_bar()}\n\n"
            f"[dim]🔊 {self.state.volume}%   "
            f"Queue: {self.state.current_index + 1}/{len(self.state.queue)}[/dim]",
            title="[bold]Now Playing[/bold]",
            border_style="green",
            expand=False,
        ))

    def _track_table(self, title: str, rows, style: str, current: int = -1,
                     extra: Optional[tuple[str, str]] = None) -> Table:
        t = Table(title=title, border_style=style, expand=False)
        t.add_column("", width=1)
        t.add_column("#", style="dim", width=3, justify="right")
        t.add_column("Judul", max_width=52, no_wrap=True)
        t.add_column("Channel", style="dim", max_width=22, no_wrap=True)
        t.add_column(extra[0] if extra else "Durasi", style="cyan" if not extra else "dim",
                     justify="right")
        for i, r in enumerate(rows):
            playing = i == current
            t.add_row(
                "[green]▶[/green]" if playing else " ",
                str(i + 1),
                Text(_trunc(r["title"], 52), style="bold green" if playing else ""),
                _trunc(r["channel"], 22),
                r[extra[1]] if extra else r["duration"],
            )
        return t

    def _ask(self, text: str) -> str:
        try:
            return self._session.prompt(text).strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    def _page_queue(self) -> None:
        q = self.state.queue
        if not q:
            console.print("  [dim]Queue kosong.[/dim]")
            return
        console.print(self._track_table(
            f"📋 Queue ({len(q)} track)", q, "blue", self.state.current_index))

        raw = self._ask("  Nomor = putar · d<nomor> hapus · c kosongkan · Enter batal: ").lower()
        if not raw:
            return
        if raw == "c":
            self._clear_queue()
            return
        if raw.startswith("d"):
            idx = _parse_index(raw, "d", len(q))
            if idx is None:
                console.print("  [yellow]Nomor tidak valid.[/yellow]")
                return
            with self._lock:
                if idx == self.state.current_index:
                    self.player.stop()
                title = self.state.remove_from_queue(idx)
            if title:
                console.print(f"  [red]- Dihapus:[/red] {Text(_trunc(title, 50)).markup}")
            return
        idx = _parse_index(raw, "", len(q))
        if idx is None:
            console.print("  [yellow]Nomor tidak valid.[/yellow]")
            return
        self.state.current_index = idx
        self.state.position_seconds = 0
        self._fetch_and_play(self.state.current_track())

    def _page_likes(self) -> None:
        rows = get_likes()
        if not rows:
            console.print("  [dim]Belum ada ceramah yang di-like. Ketik /like saat memutar.[/dim]")
            return
        console.print(self._track_table("♥ Likes", rows, "red"))

        raw = self._ask("  Nomor = putar · a<nomor> tambah queue · Enter batal: ").lower()
        if not raw:
            return
        if raw.startswith("a"):
            idx = _parse_index(raw, "a", len(rows))
            if idx is None:
                console.print("  [yellow]Nomor tidak valid.[/yellow]")
                return
            track = Track.from_row(rows[idx])
            if self.state.index_of(track.video_id) >= 0:
                console.print("  [yellow]Sudah ada di queue.[/yellow]")
            else:
                self.state.add_to_queue(track)
                if self.state.current_index < 0:
                    self.state.current_index = 0
                self.state.save()
                console.print("  [green]+ Ditambah ke queue[/green]")
            return
        idx = _parse_index(raw, "", len(rows))
        if idx is None:
            console.print("  [yellow]Nomor tidak valid.[/yellow]")
            return
        self._play_track(Track.from_row(rows[idx]))

    def _page_history(self) -> None:
        rows = get_history()
        if not rows:
            console.print("  [dim]Belum ada riwayat.[/dim]")
            return
        console.print(self._track_table("🕐 Riwayat", rows, "yellow",
                                        extra=("Waktu", "played_at")))
        raw = self._ask("  Nomor = putar · Enter batal: ")
        idx = _parse_index(raw, "", len(rows)) if raw else None
        if idx is not None:
            self._play_track(Track.from_row(rows[idx]))

    def _page_playlists(self) -> None:
        rows = get_playlists()
        if not rows:
            console.print("  [dim]Belum ada playlist. Gunakan /playlist new <nama>[/dim]")
            return

        t = Table(title="📂 Playlist", border_style="blue", expand=False)
        t.add_column("#", style="dim", width=3, justify="right")
        t.add_column("Nama", style="bold cyan", max_width=28)
        t.add_column("Track", style="dim", justify="right")
        t.add_column("Dibuat", style="dim")
        for i, r in enumerate(rows):
            t.add_row(str(i + 1), _trunc(r["name"], 28), str(r["track_count"]), r["created_at"])
        console.print(t)

        raw = self._ask("  Nomor = lihat · p<nomor> putar · d<nomor> hapus · Enter batal: ").lower()
        if not raw:
            return
        for prefix in ("p", "d"):
            if raw.startswith(prefix):
                idx = _parse_index(raw, prefix, len(rows))
                if idx is None:
                    console.print("  [yellow]Nomor tidak valid.[/yellow]")
                    return
                name = rows[idx]["name"]
                if prefix == "p":
                    self._playlist_play(name)
                else:
                    delete_playlist(name)
                    console.print(f"  [red]- Dihapus: {Text(name).markup}[/red]")
                return
        idx = _parse_index(raw, "", len(rows))
        if idx is not None:
            self._page_playlist_detail(rows[idx]["name"])

    def _page_playlist_detail(self, name: str) -> None:
        tracks = get_playlist_tracks(name)
        if not tracks:
            console.print(f"  [dim]Playlist '{Text(name).markup}' kosong. "
                          f"Putar sesuatu lalu /playlist add {Text(name).markup}[/dim]")
            return
        console.print(self._track_table(f"📂 {Text(name).markup}", tracks, "blue"))

        raw = self._ask("  p putar semua · a tambah now playing ke sini · Enter batal: ").lower()
        if raw == "a":
            self._playlist_add(name)
        elif raw in ("p", "play", "putar"):
            self._playlist_play(name)

    def _playlist_create(self, name: str = "") -> None:
        name = name or self._ask("  Nama playlist: ")
        if not name:
            return
        if create_playlist(name):
            console.print(f"  [green]+ Playlist dibuat: {Text(name).markup}[/green]")
        else:
            console.print(f"  [yellow]Playlist '{Text(name).markup}' sudah ada.[/yellow]")

    def _playlist_add(self, name: str) -> None:
        track = self.state.current_track()
        if not track:
            console.print("  [yellow]Tidak ada track yang diputar.[/yellow]")
            return
        if not any(r["name"] == name for r in get_playlists()):
            console.print(f"  [yellow]Playlist '{Text(name).markup}' tidak ada. "
                          f"Buat dulu: /playlist new {Text(name).markup}[/yellow]")
            return
        upsert_track(track)
        if add_to_playlist(name, track):
            console.print(f"  [green]+ Ditambah ke '{Text(name).markup}'[/green]")
        else:
            console.print("  [yellow]Sudah ada di playlist.[/yellow]")

    def _playlist_play(self, name: str) -> None:
        tracks = get_playlist_tracks(name)
        if not tracks:
            console.print(f"  [yellow]Playlist '{Text(name).markup}' kosong / tidak ada.[/yellow]")
            return
        with self._lock:
            self.player.stop()
            self.state.clear_queue()
            for r in tracks:
                self.state.add_to_queue(Track.from_row(r))
            self.state.current_index = 0
            self.state.save()
        console.print(f"  [green]▶ Playlist '{Text(name).markup}' ({len(tracks)} track)[/green]")
        self._fetch_and_play(self.state.current_track())

    def _clear_queue(self) -> None:
        with self._lock:
            self.player.stop()
            self.state.clear_queue()
        console.print("  [red]Queue dikosongkan.[/red]")

    # ── Help ─────────────────────────────────────────────────────────────────

    def _page_help(self) -> None:
        t = Table(title="📖 Perintah", border_style="magenta", show_header=False, expand=False)
        t.add_column("Perintah", style="bold cyan")
        t.add_column("Fungsi")
        rows = [
            ("/search [kata]",   "Cari ceramah di YouTube"),
            ("/np",              "Status track sekarang"),
            ("/queue",           "Lihat & kelola antrian"),
            ("/likes",           "Daftar ceramah yang di-like"),
            ("/history",         "Riwayat ceramah terakhir"),
            ("/playlists",       "Lihat & kelola playlist"),
            ("", ""),
            ("/pause  (/p)",     "Pause / Resume"),
            ("/next   (/n)",     "Track berikutnya"),
            ("/prev",            "Track sebelumnya"),
            ("/seek +30 | -30 | 12:30", "Maju / mundur / lompat ke menit"),
            ("/stop",            "Hentikan pemutaran"),
            ("/volume N",        "Atur volume (0-100)"),
            ("/vol+  /vol-",     "Volume naik/turun 10"),
            ("/like",            "Like / unlike track sekarang"),
            ("/clear",           "Kosongkan queue"),
            ("", ""),
            ("/playlist new N",  "Buat playlist baru"),
            ("/playlist add N",  "Tambah track sekarang ke playlist"),
            ("/playlist play N", "Putar playlist"),
            ("/playlist show N", "Lihat isi playlist"),
            ("/playlist del N",  "Hapus playlist"),
            ("", ""),
            ("/help",            "Halaman ini"),
            ("/quit  (/q)",      "Keluar (posisi tersimpan)"),
        ]
        for cmd, desc in rows:
            t.add_row(cmd, desc)
        console.print(t)
        console.print("  [dim]Ketik [bold]/[/bold] untuk autocomplete. "
                      "Teks tanpa / langsung dicari.[/dim]")

    # ── Command dispatch ──────────────────────────────────────────────────────

    def _set_volume(self, vol: int) -> None:
        self.state.volume = max(0, min(100, vol))
        self.player.set_volume(self.state.volume)
        self.state.save()
        console.print(f"  [cyan]🔊 Volume: {self.state.volume}%[/cyan]")

    def _seek(self, arg: str) -> None:
        arg = arg.strip()
        if not arg:
            console.print("  [yellow]Contoh: /seek +30, /seek -60, /seek 12:30[/yellow]")
            return
        if not self.player.is_active:
            console.print("  [yellow]Tidak ada yang diputar.[/yellow]")
            return
        try:
            if arg[0] in "+-":
                ok = self.player.seek(float(arg), relative=True)
            elif ":" in arg:
                ok = self.player.seek(_parse_duration(arg), relative=False)
            else:
                ok = self.player.seek(float(arg), relative=False)
        except ValueError:
            console.print("  [yellow]Format seek tidak valid.[/yellow]")
            return
        if ok:
            console.print(f"  [cyan]⏩ {_fmt(self.player.position)}[/cyan]")
        else:
            console.print("  [yellow]Gagal seek.[/yellow]")

    def _handle(self, raw: str) -> None:
        if not raw.startswith("/"):
            # Teks biasa = langsung search
            self._page_search(raw)
            return
        parts = raw[1:].strip().split(maxsplit=1)
        if not parts:
            return
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("search", "s", "cari"):
            self._page_search(arg)
        elif cmd in ("np", "player", "status"):
            self._page_player()
        elif cmd == "queue":
            self._page_queue()
        elif cmd == "likes":
            self._page_likes()
        elif cmd == "history":
            self._page_history()
        elif cmd in ("playlists", "playlist") and not arg:
            self._page_playlists()
        elif cmd == "help":
            self._page_help()

        elif cmd == "playlist":
            sub, _, name = arg.partition(" ")
            sub, name = sub.lower(), name.strip()
            if sub in ("new", "baru"):
                self._playlist_create(name)
            elif sub in ("add", "tambah") and name:
                self._playlist_add(name)
            elif sub in ("play", "putar") and name:
                self._playlist_play(name)
            elif sub in ("show", "lihat") and name:
                self._page_playlist_detail(name)
            elif sub in ("delete", "del", "hapus") and name:
                if delete_playlist(name):
                    console.print(f"  [red]- Dihapus: {Text(name).markup}[/red]")
                else:
                    console.print(f"  [yellow]Playlist '{Text(name).markup}' tidak ada.[/yellow]")
            else:
                console.print("  [yellow]Pakai: /playlist new|add|play|show|del <nama>[/yellow]")

        elif cmd in ("pause", "p", "resume"):
            track = self.state.current_track()
            if not self.player.is_active:
                if track:  # player mati (mis. setelah /stop) → putar lagi dari posisi terakhir
                    self._fetch_and_play(track, resume_pos=self.state.position_seconds)
                else:
                    console.print("  [yellow]Tidak ada yang diputar.[/yellow]")
                return
            paused = self.player.toggle_pause()
            self.state.position_seconds = self.player.position
            self.state.save()
            console.print("  [yellow]⏸  Pause[/yellow]" if paused else "  [green]▶  Resume[/green]")

        elif cmd in ("next", "n"):
            nxt = self.state.next_track()
            if nxt:
                self._fetch_and_play(nxt)
            else:
                console.print("  [yellow]Sudah di akhir queue.[/yellow]")

        elif cmd in ("prev", "b"):
            prv = self.state.prev_track()
            if prv:
                self._fetch_and_play(prv)
            else:
                console.print("  [yellow]Sudah di awal queue.[/yellow]")

        elif cmd == "seek":
            self._seek(arg)

        elif cmd == "stop":
            with self._lock:
                if self.player.is_active:
                    self.state.position_seconds = self.player.position
                self.player.stop()
                self.state.save()
            console.print("  [dim]⏹  Berhenti. /pause untuk lanjut.[/dim]")

        elif cmd in ("volume", "vol"):
            if arg.lstrip("+-").isdigit():
                v = int(arg)
                self._set_volume(self.state.volume + v if arg[0] in "+-" else v)
            else:
                console.print(f"  [cyan]🔊 Volume: {self.state.volume}%[/cyan] "
                              "[dim](/volume 0-100)[/dim]")
        elif cmd == "vol+":
            self._set_volume(self.state.volume + 10)
        elif cmd == "vol-":
            self._set_volume(self.state.volume - 10)

        elif cmd == "like":
            track = self.state.current_track()
            if track:
                upsert_track(track)
                liked = toggle_like(track["video_id"])
                console.print("  [red]♥ Liked[/red]" if liked else "  [dim]♡ Unliked[/dim]")
            else:
                console.print("  [yellow]Tidak ada track.[/yellow]")

        elif cmd in ("quit", "q", "exit", "keluar"):
            raise SystemExit(0)

        elif cmd == "clear":
            self._clear_queue()

        else:
            console.print(
                f"  [red]Perintah tidak dikenal:[/red] [bold]{Text(cmd).markup}[/bold]  "
                "[dim](ketik /help)[/dim]"
            )

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _shutdown(self) -> None:
        self._running = False
        with self._lock:
            if self.player.is_active:
                self.state.position_seconds = self.player.position
            self.state.save()
            self.player.stop()

    def run(self) -> None:
        console.print(self._header())
        if not js_runtimes():
            console.print(
                "  [yellow]⚠ Runtime JavaScript (deno/node) tidak ditemukan — "
                "stream YouTube bisa lambat atau gagal.[/yellow]\n"
                "  [dim]Install deno: https://deno.com  (jalankan `ngaji doctor` untuk cek)[/dim]"
            )

        last = self.state.current_track()
        if last:
            pos = self.state.position_seconds
            console.print(
                f"\n  [dim]Sesi terakhir:[/dim] [bold]{Text(last['title']).markup}[/bold]"
                f"  [dim]@ {_fmt(pos)}[/dim]"
            )
            ans = self._ask("  Lanjutkan? (Y/n): ").lower()
            if ans in ("", "y", "ya", "yes"):
                self._fetch_and_play(last, resume_pos=pos)
            else:
                self.state.position_seconds = 0
                self.state.save()
        else:
            console.print("  [dim]Ketik [bold]/search <kata kunci>[/bold] atau langsung "
                          "ketik nama ustadz/tema. [bold]/help[/bold] untuk bantuan.[/dim]")

        threading.Thread(target=self._watch, daemon=True, name="ngaji-watch").start()

        try:
            while True:
                try:
                    raw = self._session.prompt(
                        ANSI("\033[1;32mngaji›\033[0m "),
                        bottom_toolbar=self._toolbar,
                        refresh_interval=1.0,
                    ).strip()
                except KeyboardInterrupt:
                    continue  # Ctrl+C membatalkan input, bukan keluar
                except EOFError:  # Ctrl+D
                    break
                if not raw:
                    continue
                try:
                    self._handle(raw)
                except SystemExit:
                    break
                except KeyboardInterrupt:
                    console.print("  [dim]Dibatalkan.[/dim]")
                except Exception as e:  # noqa: BLE001
                    console.print(f"  [red]Error: {e}[/red]")
        finally:
            self._shutdown()
        console.print("\n  [green]Sampai jumpa! بارك الله فيك 👋[/green]")


# ── Entry point ────────────────────────────────────────────────────────────────

def _doctor() -> int:
    import yt_dlp.version

    ok = True
    t = Table(title="ngaji doctor", show_header=False, expand=False)
    t.add_column("", style="bold")
    t.add_column("")
    t.add_row("ngaji", __version__)
    t.add_row("Python", f"{platform.python_version()} ({sys.platform})")
    t.add_row("yt-dlp", yt_dlp.version.__version__)
    try:
        import yt_dlp_ejs  # noqa: F401
        t.add_row("yt-dlp-ejs", "[green]terpasang[/green]")
    except ImportError:
        ok = False
        t.add_row("yt-dlp-ejs", "[red]tidak ada[/red] — pip install -U \"yt-dlp[default]\"")

    mpv = find_mpv()
    if mpv:
        try:
            out = subprocess.run([mpv, "--version"], capture_output=True, text=True, timeout=10)
            ver = (out.stdout.splitlines() or ["?"])[0]
        except Exception:  # noqa: BLE001
            ver = "?"
        t.add_row("mpv", f"[green]{ver}[/green]\n[dim]{mpv}[/dim]")
    else:
        ok = False
        t.add_row("mpv", f"[red]tidak ditemukan[/red]\n{install_hint()}")

    rt = js_runtimes()
    if rt:
        t.add_row("JS runtime", "[green]" + ", ".join(rt) + "[/green]")
    else:
        ok = False
        hint = ("winget install DenoLand.Deno" if sys.platform == "win32"
                else "brew install deno" if sys.platform == "darwin"
                else "curl -fsSL https://deno.land/install.sh | sh")
        t.add_row("JS runtime", f"[red]tidak ada[/red] — {hint}")
    console.print(t)
    console.print("  [green]Semua siap ✓[/green]" if ok else "  [yellow]Ada yang perlu dipasang.[/yellow]")
    return 0 if ok else 1


def _setup_windows_console() -> None:
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def main(argv: Optional[list[str]] = None) -> None:
    _setup_windows_console()
    parser = argparse.ArgumentParser(
        prog="ngaji",
        description="YouTube audio player untuk terminal — dengarkan ceramah tanpa buka browser.",
    )
    parser.add_argument("-V", "--version", action="version", version=f"ngaji {__version__}")
    parser.add_argument("command", nargs="?", choices=["doctor"],
                        help="doctor: cek dependency (mpv, yt-dlp, runtime JS)")
    args = parser.parse_args(argv)

    if args.command == "doctor":
        sys.exit(_doctor())

    try:
        app = NgajiApp()
    except PlayerNotFound as e:
        console.print(f"[red]✗ {e}[/red]")
        sys.exit(1)

    try:
        app.run()
    except KeyboardInterrupt:
        app._shutdown()


if __name__ == "__main__":
    main()
