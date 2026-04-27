"""Main CLI — entry point untuk command `ngaji`.

REPL sederhana: setiap perintah print hasilnya sekali, input via prompt biasa.
Ketik / di prompt untuk daftar perintah dengan autocomplete.
"""

from __future__ import annotations

import contextlib
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
    ("np",        "Now playing — status sekarang",          False),
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


def _read_key(timeout: float = 0.5) -> Optional[str]:
    """Baca satu keypress — terminal harus sudah dalam cbreak mode.
    Arrow: '\\x1b[A' up · '\\x1b[B' down · '\\x1b[C' right · '\\x1b[D' left."""
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


@contextlib.contextmanager
def _page_mode():
    """Cbreak mode untuk sesi navigasi: echo OFF, input char-by-char, output normal.
    Mencegah karakter tombol bocor ke terminal selama rendering."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    try:
        yield
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
        self._watcher_gen = 0
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
        self._auto_next_flag.clear()
        self._watcher_gen += 1
        gen = self._watcher_gen

        def _watch() -> None:
            while self._running:
                time.sleep(1)
                if self._watcher_gen != gen:
                    break
                if self.player.is_finished:
                    nxt = self.state.next_track()
                    if nxt:
                        self._auto_next_flag.set()
                    break

        self._watcher = threading.Thread(target=_watch, daemon=True)
        self._watcher.start()

    # ── Pages ─────────────────────────────────────────────────────────────────

    def _page_search(self, query: str = "") -> None:
        while True:  # outer loop: cari lagi dengan '/' tanpa rekursi
            # Input query — di luar _page_mode agar prompt_toolkit berjalan normal
            if not query:
                console.print(self._header())
                try:
                    query = self._session.prompt("  Kata kunci: ").strip()
                except (EOFError, KeyboardInterrupt):
                    return
                if not query:
                    return

            console.print(f"\n  [yellow]Mencari: {query}...[/yellow]")
            try:
                self._search_results = search(query)
            except Exception as e:
                console.print(f"  [red]Error: {e}[/red]")
                return

            if not self._search_results:
                console.print("  [red]Tidak ada hasil.[/red]")
                return

            # Navigasi interaktif — _page_mode matikan echo selama render
            cursor = 0
            items = self._search_results
            msg = ""
            do_new_search = False

            with _page_mode():
                while True:
                    self._consume_auto_next()
                    console.clear()
                    console.print(self._header())
                    s = self._mini_status()
                    if s:
                        console.print(s)

                    tbl = Table(
                        title=f'🔍 "{query}"',
                        border_style="cyan", show_lines=False, expand=False,
                    )
                    tbl.add_column("", width=2)
                    tbl.add_column("#", style="dim", width=3)
                    tbl.add_column("Judul", max_width=50)
                    tbl.add_column("Channel", style="dim", max_width=22)
                    tbl.add_column("Durasi", style="cyan", width=8)
                    for i, tr in enumerate(items):
                        sel = i == cursor
                        tbl.add_row(
                            "[bold green]>[/bold green]" if sel else " ",
                            str(i + 1),
                            Text(tr.title[:50], style="bold green" if sel else ""),
                            Text(tr.channel[:22], style="cyan" if sel else "dim"),
                            tr.duration,
                        )
                    console.print(tbl)

                    if msg:
                        console.print(f"  {msg}")
                        msg = ""
                    console.print(Text(
                        "  ↑↓ pilih · Enter putar · a tambah queue · / cari lagi · Esc kembali",
                        style="dim",
                    ))

                    key = _read_key(0.5)
                    if key is None:
                        continue
                    elif key == "\x1b":
                        return
                    elif key == "\x1b[A":
                        cursor = max(0, cursor - 1)
                    elif key == "\x1b[B":
                        cursor = min(len(items) - 1, cursor + 1)
                    elif key in ("\r", "\n"):
                        track = items[cursor]
                        if not any(q["video_id"] == track.video_id for q in self.state.queue):
                            self.state.add_to_queue(track)
                        self.state.current_index = next(
                            idx for idx, q in enumerate(self.state.queue)
                            if q["video_id"] == track.video_id
                        )
                        console.clear()
                        console.print(self._header())
                        console.print(f"\n  [green]Memuat: {track.title[:50]}...[/green]")
                        self._fetch_and_play(self.state.current_track())
                        return
                    elif key in ("a", "A"):
                        track = items[cursor]
                        if any(q["video_id"] == track.video_id for q in self.state.queue):
                            msg = "[yellow]Sudah ada di queue.[/yellow]"
                        else:
                            self.state.add_to_queue(track)
                            self.state.save()
                            msg = f"[green]+ Ditambah:[/green] {track.title[:40]}"
                    elif key == "/":
                        do_new_search = True
                        break
                    elif key == "\x03":
                        raise KeyboardInterrupt

            if not do_new_search:
                return
            query = ""  # reset → outer loop kembali ke input prompt

    def _page_player(self) -> None:
        track = self.state.current_track()
        if not track:
            console.print("  [dim]Tidak ada yang diputar.[/dim]")
            return

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
            f"[dim]🔊 {self.state.volume}%   "
            f"Queue: {self.state.current_index + 1}/{len(self.state.queue)}[/dim]",
            title="[bold]Now Playing[/bold]",
            border_style="green",
        ))

    def _page_queue(self) -> None:
        if not self.state.queue:
            console.print("  [dim]Queue kosong.[/dim]")
            return

        ci = self.state.current_index
        t = Table(
            title=f"📋 Queue ({len(self.state.queue)} track)",
            border_style="blue", show_lines=False, expand=False,
        )
        t.add_column("", width=2)
        t.add_column("#", style="dim", width=3)
        t.add_column("Judul", max_width=48)
        t.add_column("Channel", style="dim", max_width=20)
        t.add_column("Durasi", style="cyan", width=8)
        for i, tr in enumerate(self.state.queue):
            playing = i == ci
            t.add_row(
                "[green]▶[/green]" if playing else " ",
                str(i + 1),
                Text(tr["title"][:48], style="bold green" if playing else ""),
                tr["channel"][:20],
                tr["duration"],
            )
        console.print(t)

        try:
            raw = self._session.prompt(
                "  Pilih nomor putar, d<nomor> hapus, c kosongkan (Enter batal): "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            return

        if not raw:
            return

        if raw.lower() == "c":
            self.player.stop()
            self.state.clear_queue()
            console.print("  [red]Queue dikosongkan.[/red]")
            return

        if raw.lower().startswith("d"):
            num_str = raw[1:].strip()
            if num_str.isdigit():
                idx = int(num_str) - 1
                if idx == self.state.current_index:
                    self.player.stop()
                title = self.state.remove_from_queue(idx)
                if title:
                    console.print(f"  [red]- Dihapus:[/red] {title[:50]}")
            return

        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(self.state.queue):
                self.state.current_index = idx
                self.state.position_seconds = 0
                console.print(f"  [green]Memuat: {self.state.queue[idx]['title'][:50]}...[/green]")
                self._fetch_and_play(self.state.current_track())

    def _page_likes(self) -> None:
        rows = get_likes()
        if not rows:
            console.print("  [dim]Belum ada ceramah yang di-like.[/dim]")
            return

        t = Table(title="♥ Likes", border_style="red", show_lines=False, expand=False)
        t.add_column("#", style="dim", width=3)
        t.add_column("Judul", max_width=50)
        t.add_column("Channel", style="dim", max_width=22)
        t.add_column("Durasi", style="cyan", width=8)
        for i, r in enumerate(rows):
            t.add_row(str(i + 1), r["title"][:50], r["channel"][:22], r["duration"])
        console.print(t)

        try:
            raw = self._session.prompt(
                "  Pilih nomor putar, a<nomor> tambah queue (Enter batal): "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            return

        if not raw:
            return

        if raw.lower().startswith("a"):
            num_str = raw[1:].strip()
            if num_str.isdigit():
                idx = int(num_str) - 1
                if 0 <= idx < len(rows):
                    r = rows[idx]
                    track = Track(title=r["title"], url=r["url"], video_id=r["video_id"],
                                  duration=r["duration"], channel=r["channel"])
                    if any(q["video_id"] == track.video_id for q in self.state.queue):
                        console.print("  [yellow]Sudah ada di queue.[/yellow]")
                    else:
                        self.state.add_to_queue(track)
                        self.state.save()
                        console.print(f"  [green]+ Ditambah ke queue[/green]")
            return

        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(rows):
                r = rows[idx]
                track = Track(title=r["title"], url=r["url"], video_id=r["video_id"],
                              duration=r["duration"], channel=r["channel"])
                if not any(q["video_id"] == track.video_id for q in self.state.queue):
                    self.state.add_to_queue(track)
                self.state.current_index = next(
                    i for i, q in enumerate(self.state.queue) if q["video_id"] == track.video_id
                )
                console.print(f"  [green]Memuat: {track.title[:50]}...[/green]")
                self._fetch_and_play(self.state.current_track())

    def _page_history(self) -> None:
        rows = get_history()
        if not rows:
            console.print("  [dim]Belum ada riwayat.[/dim]")
            return

        t = Table(title="🕐 Riwayat", border_style="yellow", show_lines=False, expand=False)
        t.add_column("#", style="dim", width=3)
        t.add_column("Judul", max_width=46)
        t.add_column("Channel", style="dim", max_width=20)
        t.add_column("Waktu", style="dim", width=17)
        for i, r in enumerate(rows):
            t.add_row(str(i + 1), r["title"][:46], r["channel"][:20], r["played_at"])
        console.print(t)

    def _page_playlists(self) -> None:
        rows = get_playlists()
        if not rows:
            console.print("  [dim]Belum ada playlist. Gunakan /playlist new <nama>[/dim]")
            return

        t = Table(title="📂 Playlist", border_style="blue", show_lines=False, expand=False)
        t.add_column("#", style="dim", width=3)
        t.add_column("Nama", style="bold cyan", max_width=28)
        t.add_column("Track", style="dim", width=6)
        t.add_column("Dibuat", style="dim", width=17)
        for i, r in enumerate(rows):
            t.add_row(str(i + 1), r["name"][:28], str(r["track_count"]), r["created_at"])
        console.print(t)

        try:
            raw = self._session.prompt(
                "  Pilih nomor lihat, p<nomor> putar, d<nomor> hapus (Enter batal): "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            return

        if not raw:
            return

        if raw.lower().startswith("p"):
            num_str = raw[1:].strip()
            if num_str.isdigit():
                idx = int(num_str) - 1
                if 0 <= idx < len(rows):
                    self._playlist_play(rows[idx]["name"])
            return

        if raw.lower().startswith("d"):
            num_str = raw[1:].strip()
            if num_str.isdigit():
                idx = int(num_str) - 1
                if 0 <= idx < len(rows):
                    name = rows[idx]["name"]
                    delete_playlist(name)
                    console.print(f"  [red]- Dihapus: {name}[/red]")
            return

        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(rows):
                self._page_playlist_detail(rows[idx]["name"])

    def _page_playlist_detail(self, name: str) -> None:
        tracks = get_playlist_tracks(name)
        if not tracks:
            console.print(f"  [dim]Playlist '{name}' kosong.[/dim]")
            return

        t = Table(title=f"📂 {name}", border_style="blue", show_lines=False, expand=False)
        t.add_column("#", style="dim", width=3)
        t.add_column("Judul", max_width=50)
        t.add_column("Channel", style="dim", max_width=20)
        t.add_column("Durasi", style="cyan", width=8)
        for i, r in enumerate(tracks):
            t.add_row(str(i + 1), r["title"][:50], r["channel"][:20], r["duration"])
        console.print(t)

        try:
            raw = self._session.prompt(
                "  Enter putar semua, a tambah now playing ke sini (batal Enter): "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            return

        if not raw:
            return

        if raw.lower() == "a":
            track = self.state.current_track()
            if track:
                upsert_track(track)
                if add_to_playlist(name, track):
                    console.print(f"  [green]+ Ditambah ke '{name}'[/green]")
                else:
                    console.print("  [yellow]Sudah ada di playlist.[/yellow]")
            else:
                console.print("  [yellow]Tidak ada track yang diputar.[/yellow]")
        elif raw.lower() in ("p", "play", "putar", ""):
            self._playlist_play(name)

    def _playlist_create(self, name: str = "") -> None:
        if not name:
            try:
                name = self._session.prompt("  Nama playlist: ").strip()
            except (EOFError, KeyboardInterrupt):
                return
        if name:
            if create_playlist(name):
                console.print(f"  [green]+ Playlist dibuat: {name}[/green]")
            else:
                console.print(f"  [yellow]Playlist '{name}' sudah ada.[/yellow]")

    def _playlist_play(self, name: str) -> None:
        tracks = get_playlist_tracks(name)
        if not tracks:
            console.print(f"  [yellow]Playlist '{name}' kosong.[/yellow]")
            return
        self.player.stop()
        self.state.clear_queue()
        for r in tracks:
            self.state.add_to_queue(Track(
                title=r["title"], url=r["url"], video_id=r["video_id"],
                duration=r["duration"], channel=r["channel"]
            ))
        self.state.current_index = 0
        console.print(f"  [green]▶ Memuat playlist '{name}' ({len(tracks)} track)...[/green]")
        self._fetch_and_play(self.state.current_track())

    # ── Help ─────────────────────────────────────────────────────────────────

    def _page_help(self) -> None:
        t = Table(title="📖 Perintah", border_style="magenta", show_header=False, expand=False)
        t.add_column("Perintah", style="bold cyan", width=22)
        t.add_column("Fungsi")
        rows = [
            ("/search [kata]",  "Cari ceramah di YouTube"),
            ("/np",             "Status track sekarang"),
            ("/queue",          "Lihat & kelola antrian"),
            ("/likes",          "Daftar ceramah yang di-like"),
            ("/history",        "Riwayat ceramah terakhir"),
            ("/playlists",      "Lihat & kelola playlist"),
            ("──────",          ""),
            ("/pause",          "Pause / Resume"),
            ("/next",           "Track berikutnya"),
            ("/prev",           "Track sebelumnya"),
            ("/volume N",       "Atur volume (0-100)"),
            ("/vol+  /vol-",    "Volume naik/turun 10"),
            ("/like",           "Like / unlike track sekarang"),
            ("──────",          ""),
            ("/playlist new N", "Buat playlist baru"),
            ("/playlist add N", "Tambah track ke playlist"),
            ("/playlist play N","Putar playlist"),
            ("/playlist del N", "Hapus playlist"),
            ("──────",          ""),
            ("/help",           "Halaman ini"),
            ("/quit",           "Keluar"),
        ]
        for cmd, desc in rows:
            t.add_row(cmd, desc)
        console.print(t)
        console.print("  [dim]Ketik [bold]/[/bold] untuk autocomplete[/dim]")

    # ── Command dispatch ──────────────────────────────────────────────────────

    def _handle(self, raw: str) -> None:
        if raw.startswith("/"):
            raw = raw[1:]
        parts = raw.strip().split(maxsplit=1)
        if not parts:
            return
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

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
            if sub in ("new", "baru"):
                self._playlist_create(name)
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
            elif sub in ("play", "putar") and name:
                self._playlist_play(name)
            elif sub in ("show", "lihat") and name:
                self._page_playlist_detail(name)
            elif sub in ("delete", "del", "hapus") and name:
                if delete_playlist(name):
                    console.print(f"  [red]- Dihapus: {name}[/red]")

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
                console.print(f"  [green]Memuat: {nxt['title'][:50]}...[/green]")
                self._fetch_and_play(nxt)
            else:
                console.print("  [yellow]Sudah di akhir queue.[/yellow]")

        elif cmd in ("prev", "p"):
            self.state.position_seconds = 0
            prv = self.state.prev_track()
            if prv:
                console.print(f"  [green]Memuat: {prv['title'][:50]}...[/green]")
                self._fetch_and_play(prv)
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
                f"  [red]Perintah tidak dikenal:[/red] [bold]{cmd}[/bold]  "
                "[dim](ketik /help)[/dim]"
            )

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        console.print(self._header())

        # Resume sesi terakhir
        last = self.state.current_track()
        if last:
            pos = self.state.position_seconds
            console.print(
                f"\n  [dim]Sesi terakhir:[/dim] [bold]{last['title']}[/bold]"
                f"  [dim]@ {_fmt(pos)}[/dim]"
            )
            try:
                ans = self._session.prompt("  Lanjutkan? (y/n): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = "n"
            if ans == "y":
                console.print(f"  [green]Memuat...[/green]")
                self._fetch_and_play(last, resume_pos=pos)
            else:
                self.state.position_seconds = 0
                self.state.save()

        while True:
            self._consume_auto_next()

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
