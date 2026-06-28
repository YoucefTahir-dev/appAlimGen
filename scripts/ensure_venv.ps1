$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$projectRoot = Resolve-Path "$scriptDir\.."
$venvPath = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

function Get-SystemPython {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return $python.Path }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return $py.Path }
    throw "Python interpreter not found on PATH. Install Python 3 and retry."
}

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment at $venvPath"
    $systemPython = Get-SystemPython
    & $systemPython -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment."
    }
}

Write-Host "Installing dependencies from requirements.txt"
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip."
}
& $venvPython -m pip install -r (Join-Path $projectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed."
}

Write-Host "Virtual environment is ready. Use .\scripts\manage_windows_service.ps1 to install the Windows service."
