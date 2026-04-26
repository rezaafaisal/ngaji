"""Database SQLite untuk history, likes, dan playlist (~/.ngaji.db)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_FILE = Path.home() / ".ngaji.db"


@contextmanager
def _conn():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS tracks (
                video_id TEXT PRIMARY KEY,
                title    TEXT NOT NULL,
                channel  TEXT NOT NULL,
                duration TEXT NOT NULL,
                url      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS history (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id         TEXT NOT NULL REFERENCES tracks(video_id),
                played_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS likes (
                video_id  TEXT PRIMARY KEY REFERENCES tracks(video_id),
                liked_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS playlists (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS playlist_tracks (
                playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
                video_id    TEXT    NOT NULL REFERENCES tracks(video_id),
                position    INTEGER NOT NULL,
                PRIMARY KEY (playlist_id, video_id)
            );
        """)


# ── Tracks ────────────────────────────────────────────────────────────────────

def upsert_track(track: dict) -> None:
    with _conn() as con:
        con.execute("""
            INSERT INTO tracks (video_id, title, channel, duration, url)
            VALUES (:video_id, :title, :channel, :duration, :url)
            ON CONFLICT(video_id) DO UPDATE SET
                title=excluded.title, channel=excluded.channel,
                duration=excluded.duration, url=excluded.url
        """, track)


# ── History ───────────────────────────────────────────────────────────────────

def add_history(video_id: str) -> None:
    with _conn() as con:
        con.execute("INSERT INTO history (video_id) VALUES (?)", (video_id,))


def get_history(limit: int = 20) -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute("""
            SELECT t.video_id, t.title, t.channel, t.duration, t.url, h.played_at
            FROM history h
            JOIN tracks t ON t.video_id = h.video_id
            ORDER BY h.played_at DESC
            LIMIT ?
        """, (limit,)).fetchall()


# ── Likes ─────────────────────────────────────────────────────────────────────

def toggle_like(video_id: str) -> bool:
    """Toggle like. Return True jika sekarang liked."""
    with _conn() as con:
        exists = con.execute(
            "SELECT 1 FROM likes WHERE video_id = ?", (video_id,)
        ).fetchone()
        if exists:
            con.execute("DELETE FROM likes WHERE video_id = ?", (video_id,))
            return False
        con.execute("INSERT INTO likes (video_id) VALUES (?)", (video_id,))
        return True


def is_liked(video_id: str) -> bool:
    with _conn() as con:
        return bool(con.execute(
            "SELECT 1 FROM likes WHERE video_id = ?", (video_id,)
        ).fetchone())


def get_likes() -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute("""
            SELECT t.video_id, t.title, t.channel, t.duration, t.url
            FROM likes l
            JOIN tracks t ON t.video_id = l.video_id
            ORDER BY l.liked_at DESC
        """).fetchall()


# ── Playlists ─────────────────────────────────────────────────────────────────

def create_playlist(name: str) -> bool:
    """Return True jika berhasil, False jika nama sudah ada."""
    try:
        with _conn() as con:
            con.execute("INSERT INTO playlists (name) VALUES (?)", (name,))
        return True
    except sqlite3.IntegrityError:
        return False


def delete_playlist(name: str) -> bool:
    with _conn() as con:
        cur = con.execute("DELETE FROM playlists WHERE name = ?", (name,))
        return cur.rowcount > 0


def get_playlists() -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute("""
            SELECT p.name, COUNT(pt.video_id) AS track_count, p.created_at
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
            GROUP BY p.id
            ORDER BY p.name
        """).fetchall()


def add_to_playlist(playlist_name: str, track: dict) -> bool:
    """Return True jika berhasil, False jika playlist tidak ada atau track duplikat."""
    with _conn() as con:
        row = con.execute(
            "SELECT id FROM playlists WHERE name = ?", (playlist_name,)
        ).fetchone()
        if not row:
            return False
        playlist_id = row["id"]
        if con.execute(
            "SELECT 1 FROM playlist_tracks WHERE playlist_id = ? AND video_id = ?",
            (playlist_id, track["video_id"])
        ).fetchone():
            return False
        max_pos = con.execute(
            "SELECT COALESCE(MAX(position), 0) FROM playlist_tracks WHERE playlist_id = ?",
            (playlist_id,)
        ).fetchone()[0]
        con.execute(
            "INSERT INTO playlist_tracks (playlist_id, video_id, position) VALUES (?, ?, ?)",
            (playlist_id, track["video_id"], max_pos + 1)
        )
        return True


def get_playlist_tracks(playlist_name: str) -> list[sqlite3.Row]:
    with _conn() as con:
        return con.execute("""
            SELECT t.video_id, t.title, t.channel, t.duration, t.url
            FROM playlist_tracks pt
            JOIN playlists p ON p.id = pt.playlist_id
            JOIN tracks t ON t.video_id = pt.video_id
            WHERE p.name = ?
            ORDER BY pt.position
        """, (playlist_name,)).fetchall()
