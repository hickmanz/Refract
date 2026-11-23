# Builds the installer:  .\packaging\build.ps1 [-Version 1.2.3] [-SkipInstaller] [-WithRender]
#   -WithRender bundles Mitsuba 3 (several hundred MB) so that "Render picture" works in the installed app.
#   1. stamps the version into server\refract\__version__.py when given
#   2. builds the UI, 3. freezes the app with PyInstaller (dist\Refract\)
#   4. builds dist-installer\Refract-Setup-<version>.exe with Inno Setup (+ .sha256 and latest.json for the in-app updater)
param(
  [string]$Version = "",
  [string]$Notes = "",
  [switch]$SkipInstaller,
  [switch]$WithRender
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

# --- version ------------------------------------------------------------------
$verFile = Join-Path $root "server\refract\__version__.py"
$text = Get-Content $verFile -Raw
if ($Version) {
  $Version = $Version.TrimStart("v")
  $text = [regex]::Replace($text, '__version__ = "[^"]*"', "__version__ = `"$Version`"")
  Set-Content -Path $verFile -Value $text -NoNewline -Encoding utf8
}
$Version = [regex]::Match($text, '__version__ = "([^"]*)"').Groups[1].Value
Write-Host "Building Refract $Version" -ForegroundColor Cyan

# --- python -------------------------------------------------------------------
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py -m pip install --disable-pip-version-check -q -r server\requirements.txt -r packaging\requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# --- frontend -----------------------------------------------------------------
Push-Location frontend
try {
  npm ci --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
  npm run build
  if ($LASTEXITCODE -ne 0) { throw "UI build failed" }
} finally { Pop-Location }

# --- freeze -------------------------------------------------------------------
if ($WithRender) { $env:REFRACT_BUNDLE_MITSUBA = "1" } else { Remove-Item Env:REFRACT_BUNDLE_MITSUBA -ErrorAction SilentlyContinue }
& $py -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\refract.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

if ($SkipInstaller) { Write-Host "Done: dist\Refract\Refract.exe" -ForegroundColor Green; return }

# --- installer ----------------------------------------------------------------
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
  foreach ($p in @("$env:ProgramFiles (x86)\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
    if (Test-Path $p) { $iscc = $p; break }
  }
}
if (-not $iscc) { throw "Inno Setup 6 (iscc.exe) not found. Install it from https://jrsoftware.org/isdl.php or run with -SkipInstaller." }
& $iscc "/DAppVersion=$Version" packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

$setup = Join-Path $root "dist-installer\Refract-Setup-$Version.exe"
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
Set-Content -Path "$setup.sha256" -Value "$hash  $(Split-Path $setup -Leaf)" -Encoding ascii

# latest.json: what the in-app updater reads when its update source is a folder or web address.
# (GitHub releases need no manifest: the updater reads the release itself.)
$manifest = [ordered]@{
  version   = $Version
  file      = (Split-Path $setup -Leaf)
  sha256    = $hash
  size      = (Get-Item $setup).Length
  notes     = $Notes
  published = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}
$manifest | ConvertTo-Json | Set-Content -Path (Join-Path $root "dist-installer\latest.json") -Encoding utf8
Write-Host "Done: $setup" -ForegroundColor Green
