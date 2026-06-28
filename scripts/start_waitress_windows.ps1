$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$projectRoot = Resolve-Path "$scriptDir\.."
Set-Location $projectRoot

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Virtual environment not found. Run .\scripts\ensure_venv.ps1 first."
}

Write-Host "Starting Waitress on 0.0.0.0:8000"
& $venvPython -m waitress --listen=*:8000 gestio_stock.wsgi:application
