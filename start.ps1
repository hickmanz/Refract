# Starts Refract from source (development / no installer).
#   .\start.ps1            app window (falls back to the browser)
#   .\start.ps1 -Browser   use the default browser
#   .\start.ps1 -Setup     create the virtual environment, install the Python packages and build the UI first
param(
  [switch]$Browser,
  [switch]$Setup,
  [int]$Port = 8780
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if ($Setup -or -not (Test-Path $py)) {
  if (-not (Test-Path $py)) { python -m venv .venv }
  & $py -m pip install --disable-pip-version-check -r server\requirements.txt
  if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}
if ($Setup -or -not (Test-Path "frontend\dist\index.html")) {
  Push-Location frontend
  try {
    npm install --no-audit --no-fund
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "UI build failed" }
  } finally { Pop-Location }
}

Set-Location server
$args2 = @("-m", "refract", "--port", $Port)
if ($Browser) { $args2 += "--browser" }
& $py @args2
