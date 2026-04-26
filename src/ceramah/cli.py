"""Main CLI — entry point untuk command `ceramah`."""

from __future__ import annotations

import sys
import threading
import time
from typing import Optional

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


def _fmt(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class CeramahApp:
    def __init__(self) -> None:
        init_db()
        self.state = PlayerState.load()
        self.player = AudioPlayer()
        self._running = True
        self._search_results: list[Track] = []
        self._watcher: Optional[threading.Thread] = None

    # ── Display ───────────────────────────────────────────────────────────────

    def _header(self) -> None:
        console.clear()
        console.print(Panel(
            f"[bold green]🕌 ceramah[/bold green] [dim]v{__version__}[/dim]  "
            "[dim]YouTube audio player untuk terminal[/dim]\n"
            "[dim]Ketik [bold]help[/bold] untuk daftar perintah · "
            "[bold]q[/bold] untuk keluar[/dim]",
            border_style="green",
        ))

    def _now_playing(self) -> None:
        track = self.state.current_track()
        if not track:
            console.print("[dim]Tidak ada yang diputar saat ini.[/dim]\n")
            return
        liked = is_liked(track["video_id"])
        if self.player.is_paused:
            status = "[yellow]⏸  Pause[/yellow]"
        elif self.player.is_playing:
            status = "[green]▶  Memutar[/green]"
        else:
            status = "[dim]⏹  Berhenti[/dim]"

        heart = " [red]♥[/red]" if liked else ""
        console.print(Panel(
            f"{status}\n"
            f"[bold white]{track['title']}[/bold white]{heart}\n"
            f"[dim]{track['channel']}  ·  {track['duration']}[/dim]\n"
            f"[cyan]Posisi: {_fmt(self.player.position)}[/cyan]  "
            f"[dim]🔊 {self.state.volume}%[/dim]",
            title="Now Playing",
            border_style="green",
        ))

    def _show_queue(self) -> None:
        if not self.state.queue:
            console.print("[dim]Queue kosong. Cari dulu dengan [bold]search <kata>[/bold][/dim]\n")
            return
        t = Table(title="📋 Queue", border_style="blue", show_lines=False, expand=False)
        t.add_column("#", style="dim", width=4)
        t.add_column("Judul", max_width=52)
        t.add_column("Channel", style="dim", max_width=22)
        t.add_column("Durasi", style="cyan", width=8)
        for i, tr in enumerate(self.state.queue):
            playing = i == self.state.current_index
            marker = "▶ " if playing else "  "
            style = "bold green" if playing else "white"
            t.add_row(
                f"{marker}{i + 1}",
                Text(tr["title"][:52], style=style),
                tr["channel"][:22],
                tr["duration"],
            )
        console.print(t)

    def _show_search_results(self) -> None:
        if not self._search_results:
            return
        t = Table(border_style="cyan", show_lines=False, expand=False)
        t.add_column("#", style="dim", width=3)
        t.add_column("Judul", max_width=52)
        t.add_column("Channel", style="dim", max_width=22)
        t.add_column("Durasi", style="cyan", width=8)
        for i, tr in enumerate(self._search_results):
            t.add_row(str(i + 1), tr.title[:52], tr.channel[:22], tr.duration)
        console.print(t)

    def _show_help(self) -> None:
        t = Table(title="📖 Perintah", border_style="magenta", show_header=False, expand=False)
        t.add_column("Perintah", style="bold cyan", width=28)
        t.add_column("Fungsi")
        rows = [
            ("search <kata>",            "Cari video/ceramah di YouTube"),
            ("play <nomor>",             "Mainkan dari hasil search/likes"),
            ("add <nomor>",              "Tambah hasil search/likes ke queue"),
            ("queue",                    "Lihat antrian"),
            ("next  /  n",               "Putar track berikutnya"),
            ("prev  /  p",               "Putar track sebelumnya"),
            ("pause",                    "Pause / Resume"),
            ("goto <nomor>",             "Loncat ke nomor di queue"),
            ("remove <nomor>",           "Hapus dari queue"),
            ("volume <0-100>",           "Atur volume"),
            ("vol+  /  vol-",            "Volume naik/turun 10"),
            ("clear",                    "Kosongkan queue"),
            ("status",                   "Tampilkan now playing & queue"),
            ("─── Library ───",          ""),
            ("like",                     "Like / unlike track sekarang  ♥"),
            ("likes",                    "Daftar ceramah yang di-like"),
            ("history",                  "Riwayat 20 ceramah terakhir"),
            ("─── Playlist ───",         ""),
            ("playlists",                "Daftar semua playlist"),
            ("playlist new <nama>",      "Buat playlist baru"),
            ("playlist add <nama>",      "Tambah track sekarang ke playlist"),
            ("playlist play <nama>",     "Muat playlist ke queue & putar"),
            ("playlist show <nama>",     "Lihat isi playlist"),
            ("playlist delete <nama>",   "Hapus playlist"),
            ("─────────────",            ""),
            ("quit  /  q",               "Keluar (posisi tersimpan otomatis)"),
        ]
        for cmd, desc in rows:
            t.add_row(cmd, desc)
        console.print(t)

    # ── Playback helpers ──────────────────────────────────────────────────────

    def _load_and_play(self, track: dict, resume_pos: float = 0.0) -> None:
        with console.status("[green]Memuat stream audio...[/green]"):
            try:
                url = get_stream_url(track["url"])
            except Exception as e:
                console.print(f"[red]Gagal memuat: {e}[/red]")
                return
        self.player.play(url, start_pos=resume_pos, volume=self.state.volume)
        self.state.position_seconds = resume_pos
        self.state.save()
        upsert_track(track)
        add_history(track["video_id"])
        self._start_watcher()
        self._now_playing()

    def _start_watcher(self) -> None:
        """Thread: otomatis next setelah track selesai."""
        if self._watcher and self._watcher.is_alive():
            return

        def _watch() -> None:
            while self._running:
                time.sleep(2)
                if self.player.is_finished:
                    nxt = self.state.next_track()
                    if nxt:
                        console.print(f"\n[dim]▶ Auto-next: {nxt['title']}[/dim]")
                        self._load_and_play(nxt)
                    break

        self._watcher = threading.Thread(target=_watch, daemon=True)
        self._watcher.start()

    # ── Command dispatch ──────────────────────────────────────────────────────

    def _handle(self, raw: str) -> None:
        parts = raw.strip().split(maxsplit=1)
        if not parts:
            return
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        # search
        if cmd == "search":
            if not arg:
                console.print("[red]Tulis kata kunci.[/red]  Contoh: search firanda tauhid")
                return
            with console.status(f"[yellow]Mencari: {arg}...[/yellow]"):
                self._search_results = search(arg)
            if not self._search_results:
                console.print("[red]Tidak ada hasil.[/red]")
                return
            console.print(f'\n[bold]Hasil pencarian:[/bold] "{arg}"')
            self._show_search_results()
            console.print("[dim]Ketik [bold]play <nomor>[/bold] untuk putar, "
                          "[bold]add <nomor>[/bold] untuk masuk queue[/dim]")

        # play <n>
        elif cmd == "play":
            if not arg.isdigit():
                console.print("[red]Tulis nomor hasil search.[/red]  Contoh: play 1")
                return
            idx = int(arg) - 1
            if not self._search_results or not (0 <= idx < len(self._search_results)):
                console.print("[red]Nomor tidak valid. Search atau likes dulu.[/red]")
                return
            track = self._search_results[idx]
            if not any(t["video_id"] == track.video_id for t in self.state.queue):
                self.state.add_to_queue(track)
            self.state.current_index = next(
                i for i, t in enumerate(self.state.queue) if t["video_id"] == track.video_id
            )
            self._load_and_play(self.state.current_track())

        # add <n>
        elif cmd == "add":
            if not arg.isdigit():
                console.print("[red]Tulis nomor hasil search.[/red]  Contoh: add 2")
                return
            idx = int(arg) - 1
            if not self._search_results or not (0 <= idx < len(self._search_results)):
                console.print("[red]Nomor tidak valid. Search atau likes dulu.[/red]")
                return
            track = self._search_results[idx]
            if any(t["video_id"] == track.video_id for t in self.state.queue):
                console.print("[yellow]Sudah ada di queue.[/yellow]")
            else:
                self.state.add_to_queue(track)
                self.state.save()
                console.print(f"[green]+ Ditambah ke queue:[/green] {track.title}")

        # queue
        elif cmd == "queue":
            self._show_queue()

        # next / n
        elif cmd in ("next", "n"):
            self.state.position_seconds = 0
            nxt = self.state.next_track()
            if nxt:
                self._load_and_play(nxt)
            else:
                console.print("[yellow]Sudah di akhir queue.[/yellow]")

        # prev / p
        elif cmd in ("prev", "p"):
            self.state.position_seconds = 0
            prv = self.state.prev_track()
            if prv:
                self._load_and_play(prv)
            else:
                console.print("[yellow]Sudah di awal queue.[/yellow]")

        # pause
        elif cmd == "pause":
            paused = self.player.toggle_pause()
            if paused:
                self.state.position_seconds = self.player.position
                self.state.save()
                console.print("[yellow]⏸  Pause[/yellow]")
            else:
                console.print("[green]▶  Resume[/green]")

        # goto <n>
        elif cmd == "goto":
            if not arg.isdigit():
                console.print("[red]Tulis nomor queue.[/red]  Contoh: goto 3")
                return
            idx = int(arg) - 1
            if not (0 <= idx < len(self.state.queue)):
                console.print("[red]Nomor tidak valid.[/red]")
                return
            self.state.current_index = idx
            self.state.position_seconds = 0
            self._load_and_play(self.state.current_track())

        # remove <n>
        elif cmd == "remove":
            if not arg.isdigit():
                console.print("[red]Tulis nomor queue.[/red]  Contoh: remove 2")
                return
            idx = int(arg) - 1
            if idx == self.state.current_index:
                self.player.stop()
            title = self.state.remove_from_queue(idx)
            if title:
                console.print(f"[red]- Dihapus:[/red] {title}")
            else:
                console.print("[red]Nomor tidak valid.[/red]")

        # volume <n>
        elif cmd == "volume":
            if not arg.isdigit():
                console.print("[red]Tulis angka 0-100.[/red]  Contoh: volume 70")
                return
            self.state.volume = max(0, min(100, int(arg)))
            self.state.save()
            console.print(f"[cyan]🔊 Volume: {self.state.volume}%[/cyan]")
            if self.state.current_track() and (self.player.is_playing or self.player.is_paused):
                pos = self.player.position
                self._load_and_play(self.state.current_track(), resume_pos=pos)

        elif cmd == "vol+":
            self.state.volume = min(100, self.state.volume + 10)
            self.state.save()
            console.print(f"[cyan]🔊 Volume: {self.state.volume}%[/cyan]")

        elif cmd == "vol-":
            self.state.volume = max(0, self.state.volume - 10)
            self.state.save()
            console.print(f"[cyan]🔊 Volume: {self.state.volume}%[/cyan]")

        # clear
        elif cmd == "clear":
            self.player.stop()
            self.state.clear_queue()
            console.print("[red]Queue dikosongkan.[/red]")

        # status
        elif cmd == "status":
            self._now_playing()
            self._show_queue()

        # ── Library ───────────────────────────────────────────────────────────

        # like
        elif cmd == "like":
            track = self.state.current_track()
            if not track:
                console.print("[yellow]Tidak ada track yang sedang diputar.[/yellow]")
                return
            liked = toggle_like(track["video_id"])
            if liked:
                console.print(f"[red]♥  Liked:[/red] {track['title']}")
            else:
                console.print(f"[dim]♡  Unliked:[/dim] {track['title']}")

        # likes
        elif cmd == "likes":
            rows = get_likes()
            if not rows:
                console.print("[dim]Belum ada ceramah yang di-like.[/dim]")
                return
            self._search_results = [
                Track(title=r["title"], url=r["url"], video_id=r["video_id"],
                      duration=r["duration"], channel=r["channel"])
                for r in rows
            ]
            t = Table(title="♥ Likes", border_style="red", show_lines=False, expand=False)
            t.add_column("#", style="dim", width=3)
            t.add_column("Judul", max_width=52)
            t.add_column("Channel", style="dim", max_width=22)
            t.add_column("Durasi", style="cyan", width=8)
            for i, tr in enumerate(self._search_results):
                t.add_row(str(i + 1), tr.title[:52], tr.channel[:22], tr.duration)
            console.print(t)
            console.print("[dim]Ketik [bold]play <nomor>[/bold] atau [bold]add <nomor>[/bold][/dim]")

        # history
        elif cmd == "history":
            rows = get_history()
            if not rows:
                console.print("[dim]Belum ada riwayat.[/dim]")
                return
            t = Table(title="🕐 Riwayat", border_style="yellow", show_lines=False, expand=False)
            t.add_column("Judul", max_width=48)
            t.add_column("Channel", style="dim", max_width=22)
            t.add_column("Waktu", style="dim", width=17)
            for r in rows:
                t.add_row(r["title"][:48], r["channel"][:22], r["played_at"])
            console.print(t)

        # ── Playlist ──────────────────────────────────────────────────────────

        elif cmd == "playlists":
            rows = get_playlists()
            if not rows:
                console.print("[dim]Belum ada playlist. Buat dengan [bold]playlist new <nama>[/bold][/dim]")
                return
            t = Table(title="📂 Playlist", border_style="blue", show_lines=False, expand=False)
            t.add_column("Nama", style="bold cyan", max_width=30)
            t.add_column("Track", style="dim", width=6)
            t.add_column("Dibuat", style="dim", width=17)
            for r in rows:
                t.add_row(r["name"], str(r["track_count"]), r["created_at"])
            console.print(t)

        elif cmd == "playlist":
            sub_parts = arg.split(maxsplit=1)
            if not sub_parts:
                console.print("[red]Subperintah diperlukan.[/red]  Contoh: playlist new Kajian Pagi")
                return
            sub = sub_parts[0].lower()
            name = sub_parts[1].strip() if len(sub_parts) > 1 else ""

            if sub == "new":
                if not name:
                    console.print("[red]Tulis nama playlist.[/red]  Contoh: playlist new Kajian Pagi")
                    return
                if create_playlist(name):
                    console.print(f"[green]+ Playlist dibuat:[/green] {name}")
                else:
                    console.print(f"[yellow]Playlist '{name}' sudah ada.[/yellow]")

            elif sub == "add":
                if not name:
                    console.print("[red]Tulis nama playlist.[/red]  Contoh: playlist add Kajian Pagi")
                    return
                track = self.state.current_track()
                if not track:
                    console.print("[yellow]Tidak ada track yang sedang diputar.[/yellow]")
                    return
                upsert_track(track)
                if add_to_playlist(name, track):
                    console.print(f"[green]+ Ditambah ke '{name}':[/green] {track['title']}")
                else:
                    playlists = [r["name"] for r in get_playlists()]
                    if name not in playlists:
                        console.print(f"[red]Playlist '{name}' tidak ditemukan.[/red]")
                    else:
                        console.print(f"[yellow]Sudah ada di playlist '{name}'.[/yellow]")

            elif sub == "play":
                if not name:
                    console.print("[red]Tulis nama playlist.[/red]  Contoh: playlist play Kajian Pagi")
                    return
                tracks = get_playlist_tracks(name)
                if not tracks:
                    console.print(f"[red]Playlist '{name}' tidak ditemukan atau kosong.[/red]")
                    return
                self.player.stop()
                self.state.clear_queue()
                for r in tracks:
                    self.state.add_to_queue(Track(
                        title=r["title"], url=r["url"], video_id=r["video_id"],
                        duration=r["duration"], channel=r["channel"]
                    ))
                self.state.current_index = 0
                console.print(f"[green]▶ Memuat playlist '{name}' ({len(tracks)} track)[/green]")
                self._load_and_play(self.state.current_track())

            elif sub == "show":
                if not name:
                    console.print("[red]Tulis nama playlist.[/red]  Contoh: playlist show Kajian Pagi")
                    return
                tracks = get_playlist_tracks(name)
                if not tracks:
                    console.print(f"[red]Playlist '{name}' tidak ditemukan atau kosong.[/red]")
                    return
                t = Table(title=f"📂 {name}", border_style="blue", show_lines=False, expand=False)
                t.add_column("#", style="dim", width=3)
                t.add_column("Judul", max_width=52)
                t.add_column("Channel", style="dim", max_width=22)
                t.add_column("Durasi", style="cyan", width=8)
                for i, r in enumerate(tracks):
                    t.add_row(str(i + 1), r["title"][:52], r["channel"][:22], r["duration"])
                console.print(t)

            elif sub == "delete":
                if not name:
                    console.print("[red]Tulis nama playlist.[/red]  Contoh: playlist delete Kajian Pagi")
                    return
                if delete_playlist(name):
                    console.print(f"[red]- Playlist dihapus:[/red] {name}")
                else:
                    console.print(f"[red]Playlist '{name}' tidak ditemukan.[/red]")

            else:
                console.print(
                    f"[red]Subperintah tidak dikenal:[/red] [bold]{sub}[/bold]  "
                    "[dim](new · add · play · show · delete)[/dim]"
                )

        # help
        elif cmd == "help":
            self._show_help()

        # quit
        elif cmd in ("quit", "q", "exit"):
            raise SystemExit(0)

        else:
            console.print(
                f"[red]Perintah tidak dikenal:[/red] [bold]{cmd}[/bold]  "
                "[dim]Ketik [bold]help[/bold] untuk bantuan[/dim]"
            )

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        self._header()

        last = self.state.current_track()
        if last:
            pos = self.state.position_seconds
            console.print(
                f"[dim]Sesi terakhir:[/dim] [bold]{last['title']}[/bold] "
                f"[dim]@ {_fmt(pos)}[/dim]"
            )
            ans = console.input("[dim]Lanjutkan dari sini? (y/n): [/dim]").strip().lower()
            if ans == "y":
                self._load_and_play(last, resume_pos=pos)
            else:
                self.state.position_seconds = 0
                self.state.save()

        while True:
            try:
                raw = console.input("\n[bold green]ceramah>[/bold green] ").strip()
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
                console.print("\n[green]Sampai jumpa! بارك الله فيك 👋[/green]")
                sys.exit(0)
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")


def main() -> None:
    CeramahApp().run()
