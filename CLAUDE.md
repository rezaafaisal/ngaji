# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Ceramah** is a Python CLI tool for streaming YouTube audio in the terminal, primarily designed for listening to Islamic lectures (ceramah = lecture in Indonesian/Malay). It streams audio directly without downloading files.

## Development Commands

**Install locally for development:**
```bash
pip install -e .
```

**Run the app:**
```bash
ceramah
```

**Build distribution package:**
```bash
hatch build
```

**System dependencies required:**
- `mpv` — primary audio player (`brew install mpv` on macOS)
- `ffplay` (from ffmpeg) — fallback if mpv unavailable

## Architecture

The app is structured around four modules in `src/ceramah/`:

- **`cli.py`** — Main entry point (`CeramahApp`). Owns the REPL loop, renders Rich UI panels, dispatches user commands, and coordinates the other modules.
- **`player.py`** — `AudioPlayer` class wraps an `mpv` subprocess. Manages play/pause (via SIGSTOP/SIGCONT signals), stop, and position tracking. A background thread monitors playback completion to auto-advance the queue.
- **`state.py`** — `PlayerState` dataclass holds the queue, current track index, playback position, and volume. Persists to `~/.ceramah_state.json` and is loaded on startup (offering a resume prompt if a previous session exists).
- **`youtube.py`** — `search(query)` returns `Track` objects via yt-dlp; `get_stream_url(url)` extracts a direct m4a stream URL without downloading.

### Data flow

```
User input → cli.py → youtube.py (search/stream URL)
                    → state.py (queue management, persistence)
                    → player.py → mpv subprocess → audio output
```

## Key Design Decisions

- **Streaming only**: `get_stream_url()` extracts a direct audio URL; mpv streams it without saving to disk.
- **Pause/resume via signals**: `SIGSTOP`/`SIGCONT` are sent to the mpv process — not kill/restart — to preserve stream position.
- **Auto-next**: A daemon thread in `player.py` waits for the mpv process to exit naturally and signals `cli.py` to advance the queue.
- **State persistence**: On quit (or crash recovery), queue + position are written to `~/.ceramah_state.json` and reloaded next session.
- **UI language**: All user-facing text is in Indonesian/Malay (the target user base).

## Release Process

Releases are automated via `.github/workflows/release.yml`: pushing a tag matching `v*` triggers a PyPI publish. The Homebrew formula at `Formula/ceramah.rb` must be updated manually after each release.
