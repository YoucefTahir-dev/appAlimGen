$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$projectRoot = Resolve-Path "$scriptDir\.."
Set-Location $projectRoot

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    Write-Host "Using Python from venv: $venvPython"
    & $venvPython manage.py runserver 0.0.0.0:8000
} else {
    Write-Host "Using system Python"
    & python manage.py runserver 0.0.0.0:8000
}
