"""State persistence — queue, posisi, volume disimpan di ~/.ngaji_state.json"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional

STATE_FILE = Path.home() / ".ngaji_state.json"
_save_lock = threading.Lock()


@dataclass
class Track:
    title: str
    url: str
    video_id: str
    duration: str
    channel: str

    @classmethod
    def from_row(cls, row) -> "Track":
        return cls(**{f.name: row[f.name] for f in fields(cls)})


_TRACK_KEYS = {f.name for f in fields(Track)}


@dataclass
class PlayerState:
    queue: list[dict] = field(default_factory=list)
    current_index: int = -1
    position_seconds: float = 0.0
    volume: int = 80

    # ── Persistence ──────────────────────────────────────────────────────────

    def save(self) -> None:
        with _save_lock:
            data = json.dumps(asdict(self), indent=2, ensure_ascii=False)
            # Tulis atomik supaya file tidak korup kalau app tertutup paksa
            fd, tmp = tempfile.mkstemp(dir=STATE_FILE.parent, prefix=".ngaji_state.")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(data)
                os.replace(tmp, STATE_FILE)
            except OSError:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    @classmethod
    def load(cls) -> "PlayerState":
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        queue = [
            {k: str(q.get(k, "")) for k in _TRACK_KEYS}
            for q in data.get("queue") or []
            if isinstance(q, dict) and q.get("video_id") and q.get("url")
        ]
        st = cls(queue=queue)
        try:
            st.current_index = int(data.get("current_index", -1))
            st.position_seconds = max(0.0, float(data.get("position_seconds", 0.0)))
            st.volume = max(0, min(100, int(data.get("volume", 80))))
        except (TypeError, ValueError):
            pass
        if not (0 <= st.current_index < len(st.queue)):
            st.current_index = -1 if not st.queue else 0
            st.position_seconds = 0.0
        return st

    # ── Queue helpers ─────────────────────────────────────────────────────────

    def index_of(self, video_id: str) -> int:
        for i, q in enumerate(self.queue):
            if q["video_id"] == video_id:
                return i
        return -1

    def add_to_queue(self, track: Track) -> int:
        """Tambah ke queue (tanpa duplikat). Return index track di queue."""
        idx = self.index_of(track.video_id)
        if idx >= 0:
            return idx
        self.queue.append(asdict(track))
        return len(self.queue) - 1

    def current_track(self) -> Optional[dict]:
        if 0 <= self.current_index < len(self.queue):
            return self.queue[self.current_index]
        return None

    def has_next(self) -> bool:
        return self.current_index + 1 < len(self.queue)

    def next_track(self) -> Optional[dict]:
        if self.has_next():
            self.current_index += 1
            self.position_seconds = 0.0
            self.save()
            return self.current_track()
        return None

    def prev_track(self) -> Optional[dict]:
        if self.current_index - 1 >= 0:
            self.current_index -= 1
            self.position_seconds = 0.0
            self.save()
            return self.current_track()
        return None

    def remove_from_queue(self, idx: int) -> Optional[str]:
        if 0 <= idx < len(self.queue):
            title = self.queue.pop(idx)["title"]
            if self.current_index > idx:
                self.current_index -= 1
            elif self.current_index == idx:
                self.position_seconds = 0.0
                if self.current_index >= len(self.queue):
                    self.current_index = len(self.queue) - 1
            self.save()
            return title
        return None

    def clear_queue(self) -> None:
        self.queue = []
        self.current_index = -1
        self.position_seconds = 0.0
        self.save()
