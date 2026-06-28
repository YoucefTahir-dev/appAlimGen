param(
    [ValidateSet("install","uninstall","start","stop","restart","status")] [string]$Action = "status",
    [string]$ServiceName = "GestioStock",
    [string]$ProjectPath = "$PSScriptRoot\..",
    [string]$Port = "8000"
)

$projectRoot = Resolve-Path $ProjectPath
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition

function Get-SystemPython {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return $python.Path }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return $py.Path }
    throw "Python interpreter not found on PATH. Install Python 3 and retry."
}

function Get-NssmExe {
    $nssmRoot = Join-Path $projectRoot "scripts\nssm"
    $nssm64 = Join-Path $nssmRoot "win64\nssm.exe"
    $nssm32 = Join-Path $nssmRoot "win32\nssm.exe"
    if (Test-Path $nssm64) { return $nssm64 }
    if (Test-Path $nssm32) { return $nssm32 }
    return $null
}

function Download-Nssm {
    $downloadUrl = "https://nssm.cc/release/nssm-2.24.zip"
    $zipPath = Join-Path $projectRoot "scripts\nssm.zip"
    $extractPath = Join-Path $projectRoot "scripts\nssm-temp"

    Write-Host "Downloading NSSM from $downloadUrl"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $invokeArgs = @{
            Uri = $downloadUrl
            OutFile = $zipPath
            ErrorAction = 'Stop'
        }
        if ($PSVersionTable.PSVersion.Major -lt 6) { $invokeArgs['UseBasicParsing'] = $true }
        Invoke-WebRequest @invokeArgs
    }
    catch {
        Write-Warning "Invoke-WebRequest failed: $($_.Exception.Message)"
        Write-Host "Trying WebClient fallback..."
        try {
            $wc = New-Object System.Net.WebClient
            $wc.DownloadFile($downloadUrl, $zipPath)
        }
        catch {
            Write-Error "WebClient download failed: $($_.Exception.Message)"
            throw "Failed to download NSSM."
        }
    }

    if (-not (Test-Path $zipPath)) {
        throw "Failed to download NSSM; archive missing."
    }

    Remove-Item -LiteralPath $extractPath -Force -Recurse -ErrorAction SilentlyContinue
    Expand-Archive -LiteralPath $zipPath -DestinationPath $extractPath -Force
    Remove-Item -LiteralPath $zipPath -Force

    $nssmExe = Get-ChildItem -Path $extractPath -Filter nssm.exe -Recurse -File | Where-Object { $_.FullName -match "win(64|32)" } | Select-Object -First 1
    if (-not $nssmExe) {
        Remove-Item -LiteralPath $extractPath -Force -Recurse -ErrorAction SilentlyContinue
        throw "NSSM binary not found inside downloaded archive."
    }

    $targetDir = Join-Path $projectRoot "scripts\nssm\$(Split-Path $nssmExe.DirectoryName -Leaf)"
    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
    Copy-Item -Path $nssmExe.FullName -Destination $targetDir -Force
    Remove-Item -LiteralPath $extractPath -Force -Recurse -ErrorAction SilentlyContinue

    return Join-Path $targetDir "nssm.exe"
}

function Ensure-NssmExe {
    $nssmExe = Get-NssmExe
    if ($nssmExe) { return $nssmExe }
    return Download-Nssm
}

function Ensure-VenvPython {
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        throw "Virtual environment not found. Run .\scripts\ensure_venv.ps1 first."
    }
    return $venvPython
}

function ServiceExists {
    return Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
}

switch ($Action) {
    "install" {
        $nssmExe = Ensure-NssmExe
        $venvPython = Ensure-VenvPython
        $logDir = Join-Path $projectRoot "logs"
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null

        $svc = ServiceExists
        if (-not $svc) {
            Write-Host "Installing Windows service '$ServiceName' using NSSM."
            & $nssmExe install $ServiceName $venvPython -m waitress --listen=*:${Port} gestio_stock.wsgi:application
        }
        else {
            Write-Host "Service '$ServiceName' already exists. Updating configuration."
        }

        & $nssmExe set $ServiceName AppDirectory $projectRoot
        & $nssmExe set $ServiceName AppStdout (Join-Path $logDir "$ServiceName.out.log")
        & $nssmExe set $ServiceName AppStderr (Join-Path $logDir "$ServiceName.err.log")
        & $nssmExe set $ServiceName AppRotateFiles 1
        & $nssmExe set $ServiceName Start SERVICE_AUTO_START

        if (-not $svc) {
            Write-Host "Service '$ServiceName' installed."
        }
        else {
            Write-Host "Service '$ServiceName' configuration updated."
        }

        if ($svc -and $svc.Status -ne 'Stopped') {
            Write-Host "Service '$ServiceName' is already running. Restarting to apply changes."
            Restart-Service -Name $ServiceName -ErrorAction Stop
        }
        else {
            Write-Host "Starting service '$ServiceName'."
            Start-Service -Name $ServiceName -ErrorAction Stop
        }

        Write-Host "Service '$ServiceName' is now running."
    }
    "uninstall" {
        $nssmExe = Ensure-NssmExe
        $svc = ServiceExists
        if ($svc) {
            if ($svc.Status -ne 'Stopped') {
                Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
            }
            & $nssmExe remove $ServiceName confirm
            Write-Host "Service '$ServiceName' has been removed."
        } else {
            Write-Host "Service '$ServiceName' does not exist."
        }
    }
    "start" {
        Start-Service -Name $ServiceName -ErrorAction Stop
        Write-Host "Service '$ServiceName' started."
    }
    "stop" {
        Stop-Service -Name $ServiceName -ErrorAction Stop
        Write-Host "Service '$ServiceName' stopped."
    }
    "restart" {
        Restart-Service -Name $ServiceName -ErrorAction Stop
        Write-Host "Service '$ServiceName' restarted."
    }
    "status" {
        $svc = ServiceExists
        if ($svc) {
            $svc | Select-Object Name, Status, StartType, DisplayName | Format-List
        } else {
            Write-Host "Service '$ServiceName' is not installed."
        }
    }
}
