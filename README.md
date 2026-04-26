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
  [Spasi] pause  [←→] prev/next
  [+/-] vol  [l] suka  [Esc] kembali
```

---

## Instalasi

### 1. Install dependency sistem

**macOS:**
```bash
brew install ffmpeg
```

**Ubuntu/Debian:**
```bash
sudo apt install ffmpeg
```

**Arch Linux:**
```bash
sudo pacman -S ffmpeg
```

> `ffmpeg` sudah include `ffplay` yang digunakan ngaji untuk streaming audio.
> Alternatif: `brew install mpv` jika sudah punya atau prefer mpv.

### 2. Install ngaji

**Via Homebrew (macOS):**
```bash
brew tap rezaafaisal/ngaji
brew install ngaji
```

**Via pip:**
```bash
pip install ngaji
```

---

## Penggunaan

```bash
ngaji
```

### Perintah

Ketik `/` di prompt untuk melihat daftar perintah dengan autocomplete (seperti Claude Code).

| Perintah | Fungsi |
|---|---|
| `/search` | Cari ceramah di YouTube (halaman interaktif) |
| `/np` | Now Playing — layar player dengan kontrol keyboard |
| `/queue` | Lihat & kelola antrian (↑↓ navigasi, Enter putar, d hapus) |
| `/likes` | Daftar ceramah yang di-like |
| `/history` | Riwayat ceramah terakhir |
| `/playlists` | Lihat & kelola playlist |
| `/pause` | Pause / Resume |
| `/next` | Track berikutnya |
| `/prev` | Track sebelumnya |
| `/volume <0-100>` | Atur volume |
| `/vol+` / `/vol-` | Volume naik/turun 10 |
| `/like` | Like / unlike track sekarang |
| `/help` | Bantuan |
| `/quit` | Keluar (posisi tersimpan otomatis) |

### Navigasi

Setiap perintah membuka **halaman interaktif** — tekan `Esc` untuk kembali ke prompt.

- **Hasil pencarian & likes**: `↑↓` pilih, `Enter` putar, `a` tambah ke queue
- **Queue**: `↑↓` pilih, `Enter` putar, `d` hapus, `c` kosongkan
- **Player**: `Spasi` pause, `←→` prev/next, `+/-` volume, `l` like
- **Playlist**: `↑↓` pilih, `Enter` lihat isi, `p` putar, `n` buat baru, `d` hapus

### Contoh sesi

```
ngaji> /search firanda tauhid
  🔍 pilih dengan ↑↓, Enter untuk putar

ngaji> /np
  ▶ layar player interaktif (Esc kembali)

ngaji> /queue
  📋 kelola antrian (Esc kembali)
```

---

## Fitur

- 🔍 **Search YouTube** langsung dari terminal
- ▶️ **Streaming audio** — tidak download, langsung putar
- 🎮 **Interactive TUI** — navigasi dengan arrow keys, setiap perintah adalah halaman
- ⌨️ **Slash commands** — ketik `/` untuk autocomplete perintah (seperti Claude Code)
- 📋 **Queue** — susun beberapa ceramah sekaligus
- ⏭️ **Auto-next** — otomatis lanjut ke track berikutnya
- 💾 **Resume otomatis** — keluar di tengah ceramah, lanjutkan dari posisi terakhir
- ♥️ **Likes & Playlist** — simpan ceramah favorit, buat playlist
- 🔊 **Volume control**

---

## Lisensi

MIT © [rezaafaisal](https://github.com/rezaafaisal)
