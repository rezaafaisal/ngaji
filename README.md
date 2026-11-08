# 🕌 ngaji

> YouTube audio player untuk terminal — dengarkan ceramah & podcast tanpa buka browser.

[![PyPI version](https://img.shields.io/pypi/v/ngaji)](https://pypi.org/project/ngaji)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

```
╭──────────── Now Playing ─────────────╮
│ ▶  Memutar  ♥                        │
│                                      │
│ Ceramah Ust. Firanda - Tauhid        │
│ Firanda Andirja Official             │
│                                      │
│ ████████████░░░░░░░░  31:05 / 1:08   │
│                                      │
│ 🔊 80%                               │
╰──────────────────────────────────────╯
```

---

## Instalasi

ngaji butuh **mpv** (pemutar audio) dan **deno** (runtime JavaScript yang dipakai
yt-dlp untuk membuka stream YouTube). Installer di bawah memasang semuanya.

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/rezaafaisal/ngaji/main/install.ps1 | iex
```

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/rezaafaisal/ngaji/main/install.sh | sh
```

### Homebrew (macOS)

```bash
brew tap rezaafaisal/ngaji https://github.com/rezaafaisal/ngaji
brew install ngaji
```

### Manual (pipx / uv / pip)

```bash
# 1. dependency sistem
brew install mpv deno                         # macOS
winget install shinchiro.mpv DenoLand.Deno    # Windows (atau: scoop install extras/mpv deno)
sudo apt install mpv && curl -fsSL https://deno.land/install.sh | sh   # Ubuntu/Debian

# 2. ngaji
uv tool install ngaji      # atau: pipx install ngaji  /  pip install ngaji
```

Cek semuanya sudah siap:

```bash
ngaji doctor
```

### Update

YouTube sering berubah — kalau tiba-tiba tidak bisa memutar, update dulu:

```bash
uv tool upgrade ngaji      # atau: pipx upgrade ngaji  /  brew upgrade ngaji
```

---

## Penggunaan

```bash
ngaji
```

### Perintah

Ketik `/` di prompt untuk daftar perintah dengan autocomplete. Teks tanpa `/`
langsung dicari di YouTube. Status track yang sedang diputar tampil live di
baris bawah terminal.

| Perintah | Fungsi |
|---|---|
| `/search <kata>` | Cari ceramah (atau langsung ketik kata kuncinya) |
| `/np` | Now Playing |
| `/queue` | Lihat & kelola antrian |
| `/likes` · `/history` | Ceramah yang di-like · riwayat |
| `/playlists` | Lihat & kelola playlist |
| `/pause` (`/p`) | Pause / resume |
| `/next` (`/n`) · `/prev` | Track berikutnya · sebelumnya |
| `/seek +30` · `/seek -30` · `/seek 12:30` | Maju / mundur / lompat ke menit |
| `/stop` | Hentikan pemutaran |
| `/volume <0-100>` · `/vol+` · `/vol-` | Atur volume (langsung berlaku) |
| `/like` | Like / unlike track sekarang |
| `/playlist new\|add\|play\|show\|del <nama>` | Kelola playlist |
| `/clear` | Kosongkan queue |
| `/help` · `/quit` | Bantuan · keluar (posisi tersimpan otomatis) |

### Navigasi hasil pencarian

`↑↓` (atau `j`/`k`, angka `1-9`) pilih · `Enter` putar · `a` tambah ke queue ·
`/` cari lagi · `Esc`/`q` kembali.

Di halaman queue / likes / history / playlist, ketik nomor lalu Enter
(mis. `3` putar, `d3` hapus, `a3` tambah ke queue).

### Contoh sesi

```
ngaji› firanda tauhid          # cari, pilih dengan ↑↓, Enter untuk putar
ngaji› /seek +60
ngaji› /vol-
ngaji› /quit
```

---

## Fitur

- 🔍 **Search YouTube** langsung dari terminal
- ▶️ **Streaming audio** — tidak download, langsung putar
- 🎮 **Interactive TUI** — navigasi hasil pencarian dengan arrow keys
- ⌨️ **Slash commands** — ketik `/` untuk autocomplete perintah (seperti Claude Code)
- 📋 **Queue** — susun beberapa ceramah sekaligus
- ⏭️ **Auto-next** — otomatis lanjut ke track berikutnya
- 💾 **Resume otomatis** — keluar di tengah ceramah, lanjutkan dari posisi terakhir
- 🔁 **Auto-reconnect** — stream putus/kadaluarsa disambung ulang dari posisi yang sama
- 🪟 **Lintas platform** — macOS, Windows, Linux
- ♥️ **Likes & Playlist** — simpan ceramah favorit, buat playlist
- 🔊 **Volume & seek** — langsung berlaku tanpa restart stream

---

## Lisensi

MIT © [rezaafaisal](https://github.com/rezaafaisal)

## Rilis (untuk maintainer)

1. Naikkan `__version__` di `src/ngaji/__init__.py`, commit.
2. `git tag v0.2.0 && git push --tags` → GitHub Actions menguji (macOS/Windows/Linux) lalu publish ke PyPI.
3. Setelah terbit: `python scripts/update_formula.py` lalu commit `Formula/ngaji.rb`.
