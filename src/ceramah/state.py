"""State persistence — queue, posisi, volume disimpan di ~/.ceramah_state.json"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

STATE_FILE = Path.home() / ".ceramah_state.json"


@dataclass
class Track:
    title: str
    url: str
    video_id: str
    duration: str
    channel: str


@dataclass
class PlayerState:
    queue: list[dict] = field(default_factory=list)
    current_index: int = -1
    position_seconds: float = 0.0
    volume: int = 80

    # ── Persistence ──────────────────────────────────────────────────────────

    def save(self) -> None:
        with open(STATE_FILE, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls) -> "PlayerState":
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE) as f:
                    data = json.load(f)
                return cls(**data)
            except Exception:
                pass
        return cls()

    # ── Queue helpers ─────────────────────────────────────────────────────────

    def add_to_queue(self, track: Track) -> None:
        self.queue.append(asdict(track))

    def current_track(self) -> Optional[dict]:
        if 0 <= self.current_index < len(self.queue):
            return self.queue[self.current_index]
        return None

    def next_track(self) -> Optional[dict]:
        if self.current_index + 1 < len(self.queue):
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
            title = self.queue[idx]["title"]
            self.queue.pop(idx)
            if self.current_index > idx:
                self.current_index -= 1
            elif self.current_index == idx:
                self.current_index = min(self.current_index, len(self.queue) - 1)
            self.save()
            return title
        return None

    def clear_queue(self) -> None:
        self.queue = []
        self.current_index = -1
        self.position_seconds = 0.0
        self.save()