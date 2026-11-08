# Installer ngaji untuk Windows
#   irm https://raw.githubusercontent.com/rezaafaisal/ngaji/main/install.ps1 | iex
#
# Memasang: mpv (pemutar audio), deno (runtime JS untuk yt-dlp), uv, lalu ngaji.

$ErrorActionPreference = "Stop"

function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

function Install-Pkg($name, $wingetId, $scoopName, $scoopBucket) {
    if (Have $name) { Write-Host "  [ok] $name sudah terpasang" -ForegroundColor Green; return }
    Write-Host "  Memasang $name ..." -ForegroundColor Cyan
    if (Have "scoop") {
        if ($scoopBucket) { scoop bucket add $scoopBucket 2>$null | Out-Null }
        scoop install $scoopName
    } elseif (Have "winget") {
        winget install --id $wingetId -e --accept-source-agreements --accept-package-agreements
    } else {
        Write-Host "  [!] Pasang $name manual (winget/scoop tidak ditemukan)." -ForegroundColor Yellow
    }
    Refresh-Path
}

Write-Host "`n== ngaji installer ==`n" -ForegroundColor Green

Install-Pkg "mpv"  "shinchiro.mpv"  "mpv"  "extras"
Install-Pkg "deno" "DenoLand.Deno"  "deno" $null

if (-not (Have "uv")) {
    Write-Host "  Memasang uv ..." -ForegroundColor Cyan
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

Write-Host "  Memasang ngaji ..." -ForegroundColor Cyan
uv tool install --upgrade --python 3.12 ngaji
uv tool update-shell 2>$null | Out-Null

Write-Host "`nSelesai! Buka terminal baru lalu jalankan:  ngaji" -ForegroundColor Green
Write-Host "Cek dependency:  ngaji doctor`n"
