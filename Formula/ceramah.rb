class Ceramah < Formula
  include Language::Python::Virtualenv

  desc "YouTube audio player untuk terminal — dengarkan ceramah tanpa buka browser"
  homepage "https://github.com/rezaafaisal/ceramah"
  url "https://files.pythonhosted.org/packages/source/c/ceramah/ceramah-0.1.0.tar.gz"
  # Ganti sha256 di bawah setelah upload ke PyPI:
  # jalankan: curl -sL <url> | sha256sum
  sha256 "GANTI_DENGAN_SHA256_SETELAH_UPLOAD_KE_PYPI"
  license "MIT"

  depends_on "mpv"
  depends_on "python@3.12"

  resource "yt-dlp" do
    url "https://files.pythonhosted.org/packages/source/y/yt_dlp/yt_dlp-2024.1.0.tar.gz"
    sha256 "GANTI_SHA256_YT_DLP"
  end

  resource "rich" do
    url "https://files.pythonhosted.org/packages/source/r/rich/rich-13.7.0.tar.gz"
    sha256 "GANTI_SHA256_RICH"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "ceramah", shell_output("#{bin}/ceramah --help 2>&1", 1)
  end
end