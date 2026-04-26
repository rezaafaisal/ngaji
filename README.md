# 🕌 ceramah

> YouTube audio player untuk terminal — dengarkan ceramah & podcast tanpa buka browser.

[![PyPI version](https://img.shields.io/pypi/v/ceramah)](https://pypi.org/project/ceramah)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

```
╭─ Now Playing ──────────────────────────────────────╮
│ ▶  Memutar                                         │
│ Ceramah Ust. Firanda - Keutamaan Tauhid            │
│ Firanda Andirja Official  ·  1:08:42               │
│ Posisi: 0:31:05   🔊 80%                           │
╰────────────────────────────────────────────────────╯
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

> `ffmpeg` sudah include `ffplay` yang digunakan ceramah untuk streaming audio.
> Alternatif: `brew install mpv` jika sudah punya atau prefer mpv.

### 2. Install ceramah

**Via Homebrew (macOS):**
```bash
brew tap rezaafaisal/ceramah
brew install ceramah
```

**Via pip:**
```bash
pip install ceramah
```

---

## Penggunaan

```bash
ceramah
```

### Perintah

| Perintah | Fungsi |
|---|---|
| `search <kata>` | Cari video/ceramah di YouTube |
| `play <nomor>` | Mainkan dari hasil search |
| `add <nomor>` | Tambah ke queue (tanpa langsung main) |
| `queue` | Lihat antrian |
| `next` / `n` | Track berikutnya |
| `prev` / `p` | Track sebelumnya |
| `pause` | Pause / Resume |
| `goto <nomor>` | Loncat ke nomor di queue |
| `remove <nomor>` | Hapus dari queue |
| `volume <0-100>` | Atur volume |
| `vol+` / `vol-` | Volume naik/turun 10 |
| `clear` | Kosongkan queue |
| `status` | Now playing & queue |
| `quit` / `q` | Keluar |

### Contoh sesi

```
ceramah> search firanda tauhid
ceramah> play 1
ceramah> search firanda sifat shalat nabi
ceramah> add 2
ceramah> queue
ceramah> pause
ceramah> volume 70
ceramah> quit
```

---

## Fitur

- 🔍 **Search YouTube** langsung dari terminal
- ▶️ **Streaming audio** — tidak download, langsung putar
- 📋 **Queue** — susun beberapa ceramah sekaligus
- ⏭️ **Auto-next** — otomatis lanjut ke track berikutnya
- 💾 **Resume otomatis** — keluar di tengah ceramah, buka lagi dilanjutkan dari posisi terakhir
- 🔊 **Volume control**

---

## Lisensi

MIT © [rezaafaisal](https://github.com/rezaafaisal)
