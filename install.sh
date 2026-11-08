#!/bin/sh
# Installer ngaji untuk macOS / Linux
#   curl -fsSL https://raw.githubusercontent.com/rezaafaisal/ngaji/main/install.sh | sh
#
# Memasang: mpv (pemutar audio), deno (runtime JS untuk yt-dlp), uv, lalu ngaji.
set -e

have() { command -v "$1" >/dev/null 2>&1; }
say() { printf '  \033[36m%s\033[0m\n' "$1"; }

printf '\n\033[32m== ngaji installer ==\033[0m\n\n'

install_pkg() {
  if have "$1"; then printf '  \033[32m[ok]\033[0m %s sudah terpasang\n' "$1"; return; fi
  say "Memasang $1 ..."
  if have brew; then brew install "$1"
  elif have apt-get; then sudo apt-get install -y "$1"
  elif have dnf; then sudo dnf install -y "$1"
  elif have pacman; then sudo pacman -S --noconfirm "$1"
  else printf '  \033[33m[!] Pasang %s manual.\033[0m\n' "$1"; return 1
  fi
}

install_pkg mpv || true

if ! have deno && ! have node && ! have bun; then
  if have brew; then brew install deno
  else say "Memasang deno ..."; curl -fsSL https://deno.land/install.sh | sh -s -- -y
  fi
fi

if ! have uv; then
  say "Memasang uv ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="$HOME/.local/bin:$PATH"
fi

say "Memasang ngaji ..."
# Pakai PyPI; kalau belum terbit, ambil langsung dari GitHub
if ! uv tool install --upgrade --python 3.12 ngaji 2>/dev/null; then
  say "ngaji belum ada di PyPI, memasang dari GitHub ..."
  uv tool install --force --python 3.12 "ngaji @ https://github.com/rezaafaisal/ngaji/archive/refs/heads/main.zip"
fi
uv tool update-shell >/dev/null 2>&1 || true

printf '\n\033[32mSelesai!\033[0m Buka terminal baru lalu jalankan:  ngaji\n'
printf 'Cek dependency:  ngaji doctor\n\n'
