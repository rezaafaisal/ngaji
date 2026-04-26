"""Main CLI — entry point untuk command `ngaji`.

Page-based TUI: setiap perintah buka halaman interaktif (Esc = kembali).
Ketik / di prompt untuk daftar perintah dengan autocomplete.
"""

from __future__ import annotations

import select
import sys
import termios
import threading
import time
import tty
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import ANSI
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .db import (
    init_db, upsert_track, add_history,
    toggle_like, is_liked, get_likes,
    get_history,
    create_playlist, delete_playlist, get_playlists,
    add_to_playlist, get_playlist_tracks,
)
from .player import AudioPlayer
from .state import PlayerState, Track
from .youtube import get_stream_url, search

console = Console()

# ── Slash commands untuk autocomplete ──────────────────────────────────────────
_SLASH_CMDS: list[tuple[str, str, bool]] = [
    ("search",    "Cari ceramah di YouTube",                True),
    ("np",        "Now playing — layar player interaktif",  False),
    ("queue",     "Lihat & kelola antrian",                 False),
    ("likes",     "Daftar ceramah yang di-like",            False),
    ("history",   "Riwayat ceramah terakhir",               False),
    ("playlists", "Lihat & kelola playlist",                False),
    ("pause",     "Pause / Resume",                         False),
    ("next",      "Track berikutnya",                       False),
    ("prev",      "Track sebelumnya",                       False),
    ("volume",    "Atur volume (0-100)",                    True),
    ("vol+",      "Volume naik 10",                         False),
    ("vol-",      "Volume turun 10",                        False),
    ("like",      "Like / unlike track sekarang",           False),
    ("help",      "Bantuan perintah",                       False),
    ("quit",      "Keluar",                                 False),
]


class _SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if " " in text or not text.startswith("/"):
            return
        after = text[1:]
        for name, desc, needs_arg in _SLASH_CMDS:
            if name.startswith(after.lower()):
                suffix = " " if needs_arg else ""
                yield Completion(
                    name + suffix,
                    start_position=-len(after),
                    display=f"/{name}",
                    display_meta=desc,
                )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _fmt(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _parse_duration(s: str) -> float:
    try:
        parts = s.strip().split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        pass
    return 0.0


def _getch(timeout: float = 0.4) -> Optional[str]:
    """Baca satu keypress tanpa echo. None jika timeout.
    Arrow: '\\x1b[A' (up) '\\x1b[B' (down) '\\x1b[C' (right) '\\x1b[D' (left)."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return None
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            r2, _, _ = select.select([sys.stdin], [], [], 0.05)
            if r2:
                ch2 = sys.stdin.read(1)
                if ch2 == "[":
                    r3, _, _ = select.select([sys.stdin], [], [], 0.05)
                    if r3:
                        ch3 = sys.stdin.read(1)
                        return "\x1b[" + ch3
                return "\x1b" + ch2
            return "\x1b"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ── App ────────────────────────────────────────────────────────────────────────

class NgajiApp:
    def __init__(self) -> None:
        init_db()
        self.state = PlayerState.load()
        self.player = AudioPlayer()
        self._running = True
        self._search_results: list[Track] = []
        self._watcher: Optional[threading.Thread] = None
        self._auto_next_flag = threading.Event()
        self._session = PromptSession(
            completer=_SlashCompleter(),
            complete_while_typing=True,
        )

    # ── Shared rendering ──────────────────────────────────────────────────────

    def _header(self) -> Panel:
        return Panel(
            f"[bold green]🕌 ngaji[/bold green] [dim]v{__version__}[/dim]  "
            "[dim]YouTube audio player untuk terminal[/dim]",
            border_style="green",
        )

    def _mini_status(self) -> str:
        """One-line now playing info."""
        track = self.state.current_track()
        if not track:
            return ""
        icon = "⏸" if self.player.is_paused else "▶" if self.player.is_playing else "⏹"
        liked = " ♥" if is_liked(track["video_id"]) else ""
        return (
            f"  [dim]{icon}[/dim] [bold]{track['title'][:50]}[/bold]{liked}"
            f"  [cyan]{_fmt(self.player.position)}[/cyan]"
            f"[dim]/{track['duration']}  🔊{self.state.volume}%[/dim]"
        )

    def _progress_bar(self, width: int = 40) -> str:
        track = self.state.current_track()
        if not track:
            return ""
        total = _parse_duration(track["duration"])
        pos = self.player.position
        filled = int(min(1.0, pos / total) * width) if total > 0 else 0
        bar = "█" * filled + "░" * (width - filled)
        return (
            f"[green]{bar[:filled]}[/green][dim]{bar[filled:]}[/dim]"
            f"  [cyan]{_fmt(pos)}[/cyan] [dim]/ {track['duration']}[/dim]"
        )

    def _consume_auto_next(self) -> None:
        """Consume auto-next flag: advance ke track berikutnya (background)."""
        if self._auto_next_flag.is_set():
            self._auto_next_flag.clear()
            nxt = self.state.current_track()
            if nxt:
                self._fetch_and_play(nxt)

    # ── Playback engine ──────────────────────────────────────────────────────

    def _fetch_and_play(self, track: dict, resume_pos: float = 0.0) -> bool:
        try:
            url = get_stream_url(track["url"])
        except Exception:
            return False
        self.player.play(url, start_pos=resume_pos, volume=self.state.volume)
        self.state.position_seconds = resume_pos
        self.state.save()
        upsert_track(track)
        add_history(track["video_id"])
        self._start_watcher()
        return True

    def _start_watcher(self) -> None:
        if self._watcher and self._watcher.is_alive():
            return
        self._auto_next_flag.clear()

        def _watch() -> None:
            while self._running:
                time.sleep(1)
                if self.player.is_finished:
                    nxt = self.state.next_track()
                    if nxt:
                        self._auto_next_flag.set()
                    break

        self._watcher = threading.Thread(target=_watch, daemon=True)
        self._watcher.start()

    # ── Page: Search ──────────────────────────────────────────────────────────

    def _page_search(self, query: str = "") -> None:
        # Input query jika belum ada
        if not query:
            console.clear()
            console.print(self._header())
            status = self._mini_status()
            if status:
                console.print(status)
            console.print("\n  [bold cyan]🔍 Pencarian[/bold cyan]\n")
            try:
                query = self._session.prompt("  Kata kunci: ").strip()
            except (EOFError, KeyboardInterrupt):
                return
            if not query:
                return

        # Cari
        console.clear()
        console.print(self._header())
        console.print(f"\n  [yellow]Mencari: {query}...[/yellow]")

        try:
            self._search_results = search(query)
        except Exception as e:
            console.print(f"\n  [red]Error: {e}[/red]")
            console.print("  [dim]Tekan tombol apa saja...[/dim]")
            _getch(3.0)
            return

        if not self._search_results:
            console.print("\n  [red]Tidak ada hasil.[/red]")
            console.print("  [dim]Tekan tombol apa saja...[/dim]")
            _getch(3.0)
            return

        # Tampilkan hasil + navigasi
        cursor = 0
        items = self._search_results
        msg = ""

        while True:
            self._consume_auto_next()
            console.clear()
            console.print(self._header())
            status = self._mini_status()
            if status:
                console.print(status)

            t = Table(
                title=f'🔍 "{query}"',
                border_style="cyan", show_lines=False, expand=False,
            )
            t.add_column("", width=2)
            t.add_column("#", style="dim", width=3)
            t.add_column("Judul", max_width=50)
            t.add_column("Channel", style="dim", max_width=22)
            t.add_column("Durasi", style="cyan", width=8)

            for i, tr in enumerate(items):
                sel = i == cursor
                t.add_row(
                    "[bold green]>[/bold green]" if sel else " ",
                    str(i + 1),
                    Text(tr.title[:50], style="bold green" if sel else ""),
                    Text(tr.channel[:22], style="cyan" if sel else "dim"),
                    tr.duration,
                )
            console.print(t)

            if msg:
                console.print(f"  {msg}")
                msg = ""
            console.print(Text(
                "  ↑↓ pilih · Enter putar · a tambah queue · / cari lagi · Esc kembali",
                style="dim",
            ))

            key = _getch(0.5)
            if key is None:
                continue
            if key == "\x1b":
                return
            elif key == "\x1b[A":
                cursor = max(0, cursor - 1)
            elif key == "\x1b[B":
                cursor = min(len(items) - 1, cursor + 1)
            elif key in ("\r", "\n"):
                track = items[cursor]
                if not any(t["video_id"] == track.video_id for t in self.state.queue):
                    self.state.add_to_queue(track)
                self.state.current_index = next(
                    i for i, t in enumerate(self.state.queue) if t["video_id"] == track.video_id
                )
                console.clear()
                console.print(self._header())
                console.print(f"\n  [green]Memuat: {track.title[:50]}...[/green]")
                ok = self._fetch_and_play(self.state.current_track())
                if ok:
                    self._page_player()
                return
            elif key in ("a", "A"):
                track = items[cursor]
                if any(t["video_id"] == track.video_id for t in self.state.queue):
                    msg = "[yellow]Sudah ada di queue.[/yellow]"
                else:
                    self.state.add_to_queue(track)
                    self.state.save()
                    msg = f"[green]+ Ditambah ke queue:[/green] {track.title[:40]}"
            elif key == "/":
                return self._page_search()
            elif key == "\x03":
                raise KeyboardInterrupt

    # ── Page: Player (Now Playing) ────────────────────────────────────────────

    def _page_player(self) -> None:
        while True:
            if self._auto_next_flag.is_set():
                self._auto_next_flag.clear()
                nxt = self.state.current_track()
                if nxt:
                    console.clear()
                    console.print(self._header())
                    console.print(f"\n  [yellow]⏳ Memuat berikutnya: {nxt['title'][:40]}...[/yellow]")
                    self._fetch_and_play(nxt)
                else:
                    break

            track = self.state.current_track()
            console.clear()
            console.print(self._header())

            if not track:
                console.print("\n  [dim]Tidak ada yang diputar.[/dim]")
                console.print("\n  [dim]Esc kembali[/dim]")
                key = _getch(3.0)
                return

            # Status
            liked = is_liked(track["video_id"])
            heart = "  [red]♥[/red]" if liked else ""
            if self.player.is_paused:
                status = "[yellow]⏸  Pause[/yellow]"
            elif self.player.is_playing:
                status = "[green]▶  Memutar[/green]"
            else:
                status = "[dim]⏹  Berhenti[/dim]"

            console.print(Panel(
                f"{status}{heart}\n\n"
                f"[bold white]{track['title']}[/bold white]\n"
                f"[dim]{track['channel']}[/dim]\n\n"
                f"{self._progress_bar()}\n\n"
                f"[dim]🔊 {self.state.volume}%[/dim]",
                title="[bold]Now Playing[/bold]",
                border_style="green",
            ))

            # Queue window
            ci = self.state.current_index
            if self.state.queue:
                start = max(0, ci - 2)
                end = min(len(self.state.queue), ci + 5)
                t = Table(border_style="blue", show_lines=False, expand=False, show_header=False)
                t.add_column("", width=2)
                t.add_column("#", style="dim", width=4)
                t.add_column("Judul", max_width=48)
                t.add_column("Durasi", style="dim", width=8)
                for i in range(start, end):
                    tr = self.state.queue[i]
                    playing = i == ci
                    t.add_row(
                        "[green]▶[/green]" if playing else " ",
                        str(i + 1),
                        Text(tr["title"][:48], style="bold green" if playing else ("dim" if i < ci else "")),
                        tr["duration"],
                    )
                console.print(Panel(t, title=f"[dim]Queue {ci+1}/{len(self.state.queue)}[/dim]", border_style="blue"))

            console.print(Text(
                "  [Spasi] pause  [←→] prev/next  [+/-] vol  [l] suka  [Esc] kembali",
                style="dim",
            ))

            key = _getch(0.4)
            if key is None:
                continue
            if key == " ":
                paused = self.player.toggle_pause()
                if paused:
                    self.state.position_seconds = self.player.position
                    self.state.save()
            elif key in ("n", "N", "\x1b[C"):
                self.state.position_seconds = 0
                nxt = self.state.next_track()
                if nxt:
                    console.clear()
                    console.print(self._header())
                    console.print(f"\n  [yellow]⏳ Memuat: {nxt['title'][:40]}...[/yellow]")
                    self._fetch_and_play(nxt)
            elif key in ("p", "P", "\x1b[D"):
                self.state.position_seconds = 0
                prv = self.state.prev_track()
                if prv:
                    console.clear()
                    console.print(self._header())
                    console.print(f"\n  [yellow]⏳ Memuat: {prv['title'][:40]}...[/yellow]")
                    self._fetch_and_play(prv)
            elif key in ("+", "="):
                self.state.volume = min(100, self.state.volume + 10)
                self.state.save()
                if track and (self.player.is_playing or self.player.is_paused):
                    pos = self.player.position
                    self._fetch_and_play(track, resume_pos=pos)
            elif key == "-":
                self.state.volume = max(0, self.state.volume - 10)
                self.state.save()
                if track and (self.player.is_playing or self.player.is_paused):
                    pos = self.player.position
                    self._fetch_and_play(track, resume_pos=pos)
            elif key in ("l", "L"):
                if track:
                    toggle_like(track["video_id"])
            elif key in ("\x1b", "q", "Q"):
                break
            elif key == "\x03":
                raise KeyboardInterrupt

    # ── Page: Queue ──────────────────────────────────────────────────────────

    def _page_queue(self) -> None:
        if not self.state.queue:
            console.clear()
            console.print(self._header())
            console.print("\n  [dim]Queue kosong. Gunakan /search untuk mencari ceramah.[/dim]")
            console.print("\n  [dim]Esc kembali[/dim]")
            _getch(3.0)
            return

        cursor = max(0, self.state.current_index)
        msg = ""
        VISIBLE = 12

        while True:
            if not self.state.queue:
                return
            self._consume_auto_next()
            cursor = min(cursor, len(self.state.queue) - 1)

            # Viewport
            start = max(0, cursor - VISIBLE // 2)
            end = min(len(self.state.queue), start + VISIBLE)
            start = max(0, end - VISIBLE)

            console.clear()
            console.print(self._header())
            status = self._mini_status()
            if status:
                console.print(status)

            ci = self.state.current_index
            t = Table(
                title=f"📋 Queue ({len(self.state.queue)} track)",
                border_style="blue", show_lines=False, expand=False,
            )
            t.add_column("", width=2)
            t.add_column("", width=2)
            t.add_column("#", style="dim", width=4)
            t.add_column("Judul", max_width=46)
            t.add_column("Channel", style="dim", max_width=20)
            t.add_column("Durasi", style="cyan", width=8)

            for i in range(start, end):
                tr = self.state.queue[i]
                sel = i == cursor
                playing = i == ci
                arrow = "[bold cyan]>[/bold cyan]" if sel else " "
                play_mark = "[green]▶[/green]" if playing else " "
                if sel:
                    style = "bold cyan"
                elif playing:
                    style = "bold green"
                else:
                    style = ""
                t.add_row(
                    arrow, play_mark, str(i + 1),
                    Text(tr["title"][:46], style=style),
                    tr["channel"][:20],
                    tr["duration"],
                )
            console.print(t)

            if msg:
                console.print(f"  {msg}")
                msg = ""
            console.print(Text(
                "  ↑↓ pilih · Enter putar · d hapus · c kosongkan · Esc kembali",
                style="dim",
            ))

            key = _getch(0.5)
            if key is None:
                continue
            if key == "\x1b":
                return
            elif key == "\x1b[A":
                cursor = max(0, cursor - 1)
            elif key == "\x1b[B":
                cursor = min(len(self.state.queue) - 1, cursor + 1)
            elif key in ("\r", "\n"):
                self.state.current_index = cursor
                self.state.position_seconds = 0
                console.clear()
                console.print(self._header())
                console.print(f"\n  [green]Memuat: {self.state.queue[cursor]['title'][:40]}...[/green]")
                ok = self._fetch_and_play(self.state.current_track())
                if ok:
                    self._page_player()
                return
            elif key in ("d", "D"):
                if cursor == self.state.current_index:
                    self.player.stop()
                title = self.state.remove_from_queue(cursor)
                if title:
                    msg = f"[red]- Dihapus:[/red] {title[:40]}"
            elif key in ("c", "C"):
                self.player.stop()
                self.state.clear_queue()
                msg = "[red]Queue dikosongkan.[/red]"
                return
            elif key == "\x03":
                raise KeyboardInterrupt

    # ── Page: Likes ──────────────────────────────────────────────────────────

    def _page_likes(self) -> None:
        rows = get_likes()
        if not rows:
            console.clear()
            console.print(self._header())
            console.print("\n  [dim]Belum ada ceramah yang di-like.[/dim]")
            console.print("\n  [dim]Esc kembali[/dim]")
            _getch(3.0)
            return

        self._search_results = [
            Track(title=r["title"], url=r["url"], video_id=r["video_id"],
                  duration=r["duration"], channel=r["channel"])
            for r in rows
        ]
        cursor = 0
        items = self._search_results
        msg = ""

        while True:
            self._consume_auto_next()
            console.clear()
            console.print(self._header())
            status = self._mini_status()
            if status:
                console.print(status)

            t = Table(title="♥ Likes", border_style="red", show_lines=False, expand=False)
            t.add_column("", width=2)
            t.add_column("#", style="dim", width=3)
            t.add_column("Judul", max_width=50)
            t.add_column("Channel", style="dim", max_width=22)
            t.add_column("Durasi", style="cyan", width=8)

            for i, tr in enumerate(items):
                sel = i == cursor
                t.add_row(
                    "[bold red]>[/bold red]" if sel else " ",
                    str(i + 1),
                    Text(tr.title[:50], style="bold" if sel else ""),
                    Text(tr.channel[:22], style="red" if sel else "dim"),
                    tr.duration,
                )
            console.print(t)

            if msg:
                console.print(f"  {msg}")
                msg = ""
            console.print(Text(
                "  ↑↓ pilih · Enter putar · a tambah queue · Esc kembali",
                style="dim",
            ))

            key = _getch(0.5)
            if key is None:
                continue
            if key == "\x1b":
                return
            elif key == "\x1b[A":
                cursor = max(0, cursor - 1)
            elif key == "\x1b[B":
                cursor = min(len(items) - 1, cursor + 1)
            elif key in ("\r", "\n"):
                track = items[cursor]
                if not any(t["video_id"] == track.video_id for t in self.state.queue):
                    self.state.add_to_queue(track)
                self.state.current_index = next(
                    i for i, t in enumerate(self.state.queue) if t["video_id"] == track.video_id
                )
                console.clear()
                console.print(self._header())
                console.print(f"\n  [green]Memuat: {track.title[:40]}...[/green]")
                ok = self._fetch_and_play(self.state.current_track())
                if ok:
                    self._page_player()
                return
            elif key in ("a", "A"):
                track = items[cursor]
                if any(t["video_id"] == track.video_id for t in self.state.queue):
                    msg = "[yellow]Sudah ada di queue.[/yellow]"
                else:
                    self.state.add_to_queue(track)
                    self.state.save()
                    msg = f"[green]+ Ditambah ke queue[/green]"
            elif key == "\x03":
                raise KeyboardInterrupt

    # ── Page: History ─────────────────────────────────────────────────────────

    def _page_history(self) -> None:
        rows = get_history()
        if not rows:
            console.clear()
            console.print(self._header())
            console.print("\n  [dim]Belum ada riwayat.[/dim]")
            console.print("\n  [dim]Esc kembali[/dim]")
            _getch(3.0)
            return

        cursor = 0
        while True:
            self._consume_auto_next()
            console.clear()
            console.print(self._header())

            t = Table(title="🕐 Riwayat", border_style="yellow", show_lines=False, expand=False)
            t.add_column("", width=2)
            t.add_column("Judul", max_width=46)
            t.add_column("Channel", style="dim", max_width=20)
            t.add_column("Waktu", style="dim", width=17)

            for i, r in enumerate(rows):
                sel = i == cursor
                t.add_row(
                    "[bold yellow]>[/bold yellow]" if sel else " ",
                    Text(r["title"][:46], style="bold" if sel else ""),
                    r["channel"][:20],
                    r["played_at"],
                )
            console.print(t)
            console.print(Text("  ↑↓ scroll · Esc kembali", style="dim"))

            key = _getch(30.0)
            if key is None or key == "\x1b":
                return
            elif key == "\x1b[A":
                cursor = max(0, cursor - 1)
            elif key == "\x1b[B":
                cursor = min(len(rows) - 1, cursor + 1)
            elif key == "\x03":
                raise KeyboardInterrupt

    # ── Page: Playlists ──────────────────────────────────────────────────────

    def _page_playlists(self) -> None:
        while True:
            rows = get_playlists()
            if not rows:
                console.clear()
                console.print(self._header())
                console.print("\n  [dim]Belum ada playlist.[/dim]")
                console.print("\n  [dim]n buat baru · Esc kembali[/dim]")
                key = _getch(30.0)
                if key == "n":
                    self._playlist_create()
                    continue
                return

            cursor = 0
            while True:
                self._consume_auto_next()
                console.clear()
                console.print(self._header())

                t = Table(title="📂 Playlist", border_style="blue", show_lines=False, expand=False)
                t.add_column("", width=2)
                t.add_column("Nama", style="bold cyan", max_width=28)
                t.add_column("Track", style="dim", width=6)
                t.add_column("Dibuat", style="dim", width=17)

                for i, r in enumerate(rows):
                    sel = i == cursor
                    t.add_row(
                        "[bold cyan]>[/bold cyan]" if sel else " ",
                        Text(r["name"][:28], style="bold" if sel else ""),
                        str(r["track_count"]),
                        r["created_at"],
                    )
                console.print(t)
                console.print(Text(
                    "  ↑↓ pilih · Enter lihat · p putar · n buat baru · d hapus · Esc kembali",
                    style="dim",
                ))

                key = _getch(30.0)
                if key is None:
                    continue
                if key == "\x1b":
                    return
                elif key == "\x1b[A":
                    cursor = max(0, cursor - 1)
                elif key == "\x1b[B":
                    cursor = min(len(rows) - 1, cursor + 1)
                elif key in ("\r", "\n"):
                    self._page_playlist_detail(rows[cursor]["name"])
                    break  # re-fetch playlists
                elif key in ("p", "P"):
                    self._playlist_play(rows[cursor]["name"])
                    return
                elif key == "n":
                    self._playlist_create()
                    break
                elif key in ("d", "D"):
                    name = rows[cursor]["name"]
                    delete_playlist(name)
                    break
                elif key == "\x03":
                    raise KeyboardInterrupt

    def _page_playlist_detail(self, name: str) -> None:
        tracks = get_playlist_tracks(name)
        if not tracks:
            return
        cursor = 0
        msg = ""

        while True:
            console.clear()
            console.print(self._header())

            t = Table(title=f"📂 {name}", border_style="blue", show_lines=False, expand=False)
            t.add_column("", width=2)
            t.add_column("#", style="dim", width=3)
            t.add_column("Judul", max_width=48)
            t.add_column("Channel", style="dim", max_width=20)
            t.add_column("Durasi", style="cyan", width=8)

            for i, r in enumerate(tracks):
                sel = i == cursor
                t.add_row(
                    "[bold blue]>[/bold blue]" if sel else " ",
                    str(i + 1),
                    Text(r["title"][:48], style="bold" if sel else ""),
                    r["channel"][:20],
                    r["duration"],
                )
            console.print(t)

            if msg:
                console.print(f"  {msg}")
                msg = ""
            console.print(Text(
                "  ↑↓ pilih · Enter putar semua · a tambah now playing · Esc kembali",
                style="dim",
            ))

            key = _getch(30.0)
            if key is None:
                continue
            if key == "\x1b":
                return
            elif key == "\x1b[A":
                cursor = max(0, cursor - 1)
            elif key == "\x1b[B":
                cursor = min(len(tracks) - 1, cursor + 1)
            elif key in ("\r", "\n"):
                self._playlist_play(name)
                return
            elif key in ("a", "A"):
                track = self.state.current_track()
                if track:
                    upsert_track(track)
                    if add_to_playlist(name, track):
                        msg = f"[green]+ Ditambah ke '{name}'[/green]"
                        tracks = get_playlist_tracks(name)
                    else:
                        msg = "[yellow]Sudah ada di playlist.[/yellow]"
                else:
                    msg = "[yellow]Tidak ada track yang diputar.[/yellow]"
            elif key == "\x03":
                raise KeyboardInterrupt

    def _playlist_create(self) -> None:
        console.clear()
        console.print(self._header())
        console.print("\n  [bold cyan]📂 Buat Playlist Baru[/bold cyan]\n")
        try:
            name = self._session.prompt("  Nama: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if name:
            if create_playlist(name):
                console.print(f"\n  [green]+ Playlist dibuat: {name}[/green]")
            else:
                console.print(f"\n  [yellow]Playlist '{name}' sudah ada.[/yellow]")
            _getch(1.5)

    def _playlist_play(self, name: str) -> None:
        tracks = get_playlist_tracks(name)
        if not tracks:
            return
        self.player.stop()
        self.state.clear_queue()
        for r in tracks:
            self.state.add_to_queue(Track(
                title=r["title"], url=r["url"], video_id=r["video_id"],
                duration=r["duration"], channel=r["channel"]
            ))
        self.state.current_index = 0
        console.clear()
        console.print(self._header())
        console.print(f"\n  [green]▶ Memuat playlist '{name}' ({len(tracks)} track)...[/green]")
        ok = self._fetch_and_play(self.state.current_track())
        if ok:
            self._page_player()

    # ── Page: Help ────────────────────────────────────────────────────────────

    def _page_help(self) -> None:
        console.clear()
        console.print(self._header())

        t = Table(title="📖 Perintah", border_style="magenta", show_header=False, expand=False)
        t.add_column("Perintah", style="bold cyan", width=22)
        t.add_column("Fungsi")
        rows = [
            ("/search",     "Cari ceramah di YouTube"),
            ("/np",         "Now playing — layar player interaktif"),
            ("/queue",      "Lihat & kelola antrian"),
            ("/likes",      "Daftar ceramah yang di-like"),
            ("/history",    "Riwayat ceramah terakhir"),
            ("/playlists",  "Lihat & kelola playlist"),
            ("──────",      ""),
            ("/pause",      "Pause / Resume"),
            ("/next",       "Track berikutnya"),
            ("/prev",       "Track sebelumnya"),
            ("/volume N",   "Atur volume (0-100)"),
            ("/vol+  /vol-","Volume naik/turun 10"),
            ("/like",       "Like / unlike track sekarang"),
            ("──────",      ""),
            ("/help",       "Halaman ini"),
            ("/quit",       "Keluar (posisi tersimpan otomatis)"),
        ]
        for cmd, desc in rows:
            t.add_row(cmd, desc)
        console.print(t)

        console.print("\n  [dim]Ketik [bold]/[/bold] di prompt untuk autocomplete perintah[/dim]")
        console.print("  [dim]Di dalam halaman: [bold]Esc[/bold] untuk kembali[/dim]")
        console.print("\n  [dim]Tekan tombol apa saja untuk kembali...[/dim]")

        _getch(60.0)

    # ── Command dispatch ──────────────────────────────────────────────────────

    def _handle(self, raw: str) -> None:
        if raw.startswith("/"):
            raw = raw[1:]
        parts = raw.strip().split(maxsplit=1)
        if not parts:
            return
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        # Pages
        if cmd == "search":
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

        # Playlist subcommands
        elif cmd == "playlist":
            sub_parts = arg.split(maxsplit=1)
            sub = sub_parts[0].lower()
            name = sub_parts[1].strip() if len(sub_parts) > 1 else ""
            if sub == "new":
                if name:
                    if create_playlist(name):
                        console.print(f"  [green]+ Playlist dibuat: {name}[/green]")
                    else:
                        console.print(f"  [yellow]Sudah ada.[/yellow]")
                else:
                    self._playlist_create()
            elif sub == "add" and name:
                track = self.state.current_track()
                if track:
                    upsert_track(track)
                    if add_to_playlist(name, track):
                        console.print(f"  [green]+ Ditambah ke '{name}'[/green]")
                    else:
                        console.print(f"  [yellow]Gagal/sudah ada.[/yellow]")
                else:
                    console.print("  [yellow]Tidak ada track yang diputar.[/yellow]")
            elif sub == "play" and name:
                self._playlist_play(name)
            elif sub == "show" and name:
                self._page_playlist_detail(name)
            elif sub == "delete" and name:
                if delete_playlist(name):
                    console.print(f"  [red]- Dihapus: {name}[/red]")

        # Instant actions
        elif cmd == "pause":
            paused = self.player.toggle_pause()
            if paused:
                self.state.position_seconds = self.player.position
                self.state.save()
                console.print("  [yellow]⏸  Pause[/yellow]")
            else:
                console.print("  [green]▶  Resume[/green]")

        elif cmd in ("next", "n"):
            self.state.position_seconds = 0
            nxt = self.state.next_track()
            if nxt:
                console.print(f"  [green]Memuat: {nxt['title'][:40]}...[/green]")
                ok = self._fetch_and_play(nxt)
                if ok:
                    self._page_player()
            else:
                console.print("  [yellow]Sudah di akhir queue.[/yellow]")

        elif cmd in ("prev", "p"):
            self.state.position_seconds = 0
            prv = self.state.prev_track()
            if prv:
                console.print(f"  [green]Memuat: {prv['title'][:40]}...[/green]")
                ok = self._fetch_and_play(prv)
                if ok:
                    self._page_player()
            else:
                console.print("  [yellow]Sudah di awal queue.[/yellow]")

        elif cmd == "volume" and arg.isdigit():
            self.state.volume = max(0, min(100, int(arg)))
            self.state.save()
            console.print(f"  [cyan]🔊 Volume: {self.state.volume}%[/cyan]")

        elif cmd == "vol+":
            self.state.volume = min(100, self.state.volume + 10)
            self.state.save()
            console.print(f"  [cyan]🔊 Volume: {self.state.volume}%[/cyan]")

        elif cmd == "vol-":
            self.state.volume = max(0, self.state.volume - 10)
            self.state.save()
            console.print(f"  [cyan]🔊 Volume: {self.state.volume}%[/cyan]")

        elif cmd == "like":
            track = self.state.current_track()
            if track:
                liked = toggle_like(track["video_id"])
                console.print(f"  [red]♥ Liked[/red]" if liked else "  [dim]♡ Unliked[/dim]")
            else:
                console.print("  [yellow]Tidak ada track.[/yellow]")

        elif cmd in ("quit", "q", "exit"):
            raise SystemExit(0)

        elif cmd == "clear":
            self.player.stop()
            self.state.clear_queue()
            console.print("  [red]Queue dikosongkan.[/red]")

        else:
            console.print(
                f"  [red]Perintah tidak dikenal:[/red] [bold]{cmd}[/bold]\n"
                "  [dim]Ketik [bold]/[/bold] untuk daftar perintah[/dim]"
            )

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        console.clear()
        console.print(self._header())

        # Resume sesi terakhir
        last = self.state.current_track()
        if last:
            pos = self.state.position_seconds
            console.print(
                f"\n  [dim]Sesi terakhir:[/dim] [bold]{last['title']}[/bold]"
                f"\n  [dim]@ {_fmt(pos)}[/dim]"
            )
            console.print("\n  [dim]Lanjutkan? (y/n)[/dim] ", end="")
            key = _getch(10.0)
            if key in ("y", "Y"):
                console.print("[green]ya[/green]")
                console.print(f"\n  [green]Memuat...[/green]")
                ok = self._fetch_and_play(last, resume_pos=pos)
                if ok:
                    self._page_player()
            else:
                console.print("[dim]tidak[/dim]")
                self.state.position_seconds = 0
                self.state.save()

        while True:
            self._consume_auto_next()

            # Mini status
            status = self._mini_status()
            if status:
                console.print(status)

            try:
                raw = self._session.prompt(
                    ANSI("\033[1;32mngaji>\033[0m "),
                ).strip()
            except (EOFError, KeyboardInterrupt):
                raw = "quit"

            if not raw:
                continue

            try:
                self._handle(raw)
            except SystemExit:
                if self.player.is_playing or self.player.is_paused:
                    self.state.position_seconds = self.player.position
                self.state.save()
                self.player.stop()
                self._running = False
                console.print("\n  [green]Sampai jumpa! بارك الله فيك 👋[/green]")
                sys.exit(0)
            except Exception as e:
                console.print(f"  [red]Error: {e}[/red]")


def main() -> None:
    NgajiApp().run()
