"""Generate Formula/ngaji.rb dari data PyPI (url + sha256 sdist terbaru).

Pakai setelah versi baru ngaji terbit di PyPI:
    python scripts/update_formula.py            # versi dari src/ngaji/__init__.py
    python scripts/update_formula.py 0.2.0
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Dependency runtime minimal (extras opsional yt-dlp seperti brotli/pycryptodomex
# sengaja tidak diikutkan supaya build Homebrew ringan).
RESOURCES = [
    "yt-dlp", "yt-dlp-ejs", "certifi",
    "rich", "markdown-it-py", "mdurl", "pygments",
    "prompt-toolkit", "wcwidth",
]


def sdist(name: str, version: str | None = None) -> tuple[str, str, str]:
    path = f"{name}/{version}" if version else name
    with urllib.request.urlopen(f"https://pypi.org/pypi/{path}/json", timeout=30) as r:
        data = json.load(r)
    for f in data["urls"]:
        if f["packagetype"] == "sdist":
            return data["info"]["version"], f["url"], f["digests"]["sha256"]
    raise SystemExit(f"{name}: sdist tidak ditemukan di PyPI")


def main() -> None:
    version = sys.argv[1] if len(sys.argv) > 1 else re.search(
        r'__version__ = "([^"]+)"', (ROOT / "src/ngaji/__init__.py").read_text()
    ).group(1)
    try:
        _, url, sha = sdist("ngaji", version)
    except urllib.error.HTTPError:
        print(f"  ! ngaji {version} belum ada di PyPI — url/sha256 diisi placeholder")
        url = f"https://files.pythonhosted.org/packages/source/n/ngaji/ngaji-{version}.tar.gz"
        sha = "JALANKAN_ULANG_SCRIPT_INI_SETELAH_RILIS_KE_PYPI"

    blocks = []
    for name in RESOURCES:
        v, rurl, rsha = sdist(name)
        print(f"  {name} {v}")
        blocks.append(f'  resource "{name}" do\n    url "{rurl}"\n    sha256 "{rsha}"\n  end\n')

    formula = f'''class Ngaji < Formula
  include Language::Python::Virtualenv

  desc "YouTube audio player untuk terminal — dengarkan ceramah tanpa buka browser"
  homepage "https://github.com/rezaafaisal/ngaji"
  url "{url}"
  sha256 "{sha}"
  license "MIT"

  depends_on "deno"   # runtime JS untuk solver challenge YouTube (yt-dlp-ejs)
  depends_on "mpv"
  depends_on "python@3.13"

{chr(10).join(blocks)}
  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match version.to_s, shell_output("#{{bin}}/ngaji --version")
  end
end
'''
    (ROOT / "Formula/ngaji.rb").write_text(formula)
    print(f"Formula/ngaji.rb diperbarui ke ngaji {version}")


if __name__ == "__main__":
    main()
