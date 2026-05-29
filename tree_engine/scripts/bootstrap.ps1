<#
Bootstrap a fresh T.R.E.E. checkout through interactive workspace setup.

Usage:
  .\tree_engine\scripts\bootstrap.ps1
  .\tree_engine\scripts\bootstrap.ps1 -Dev
  .\tree_engine\scripts\bootstrap.ps1 -SkipSetup
  .\tree_engine\scripts\bootstrap.ps1 -SkipEmbeddingStart
#>

param(
    [switch]$Dev,
    [switch]$SkipSetup,
    [switch]$SkipEmbeddingStart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
Set-Location $ProjectRoot

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

function Test-Python312 {
    param([string]$Command)
    try {
        & $Command -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Find-Python {
    if ($env:TREE_PYTHON) {
        if (Test-Python312 $env:TREE_PYTHON) {
            return $env:TREE_PYTHON
        }
        throw "TREE_PYTHON is set but is not Python >= 3.12: $env:TREE_PYTHON"
    }

    foreach ($Candidate in @("py", "python")) {
        if (Get-Command $Candidate -ErrorAction SilentlyContinue) {
            if ($Candidate -eq "py") {
                try {
                    & py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" *> $null
                    if ($LASTEXITCODE -eq 0) {
                        return "py -3.12"
                    }
                } catch {}
            } elseif (Test-Python312 $Candidate) {
                return $Candidate
            }
        }
    }

    throw "Python >= 3.12 was not found. Install Python 3.12+, then rerun this script."
}

function Invoke-Python {
    param(
        [string]$PythonCommand,
        [string[]]$Arguments
    )
    if ($PythonCommand -eq "py -3.12") {
        & py -3.12 @Arguments
    } else {
        & $PythonCommand @Arguments
    }
}

function Test-EmbeddingReady {
    & $VenvPython -c "from pathlib import Path; from tree.services import embedding_health; raise SystemExit(0 if embedding_health(Path.cwd())[0] else 1)" *> $null
    return $LASTEXITCODE -eq 0
}

function Test-EmbeddingProcess {
    & $VenvPython -c "from pathlib import Path; from tree.services import service_status; raise SystemExit(0 if service_status(Path.cwd(), 'embedding').running else 1)" *> $null
    return $LASTEXITCODE -eq 0
}

function Get-EmbeddingLogPath {
    $Result = & $VenvPython -c "from pathlib import Path; from tree.io import paths; print(paths.service_log_path(Path.cwd(), 'embedding'))"
    return $Result
}

function Start-EmbeddingWithProgress {
    Write-Step "Starting embedding server in the background"
    & $VenvPython -m tree.cli start-embedding --no-wait

    $LogPath = Get-EmbeddingLogPath
    New-Item -ItemType File -Force -Path $LogPath | Out-Null
    Write-Host "Showing embedding server log while it starts. First launch may download ~4.3 GB."
    $LogJob = Start-Job -ScriptBlock {
        param($Path)
        Get-Content -Path $Path -Wait -Tail 40
    } -ArgumentList $LogPath

    try {
        while (-not (Test-EmbeddingReady)) {
            Receive-Job $LogJob | ForEach-Object { Write-Host $_ }
            if (-not (Test-EmbeddingProcess)) {
                throw "Embedding server exited before becoming ready. Check $LogPath"
            }
            Start-Sleep -Seconds 2
        }
        Receive-Job $LogJob | ForEach-Object { Write-Host $_ }
    } finally {
        Stop-Job $LogJob -ErrorAction SilentlyContinue
        Remove-Job $LogJob -ErrorAction SilentlyContinue
    }
    Write-Host ""
    Write-Host "Embedding server is ready."
}

if (-not (Test-Path "pyproject.toml")) {
    throw "Run this script from a cloned tree checkout, or use tree_engine\scripts\bootstrap.ps1 from the project root."
}
if (-not (Test-Path "tree_engine")) {
    throw "tree_engine\ is missing. The checkout looks incomplete."
}
if ($ProjectRoot.Path -match '([\\/])\.Trash([\\/]|$)') {
    throw "This checkout is inside a Trash directory: $($ProjectRoot.Path). Move or clone the project into a normal workspace before running bootstrap."
}
$ParentRoot = Split-Path -Parent $ProjectRoot.Path
if ((Test-Path (Join-Path $ParentRoot "pyproject.toml")) -and (Test-Path (Join-Path $ParentRoot "tree_engine"))) {
    throw "This looks like a nested tree checkout: $($ProjectRoot.Path) inside $ParentRoot. Run bootstrap from the outer checkout or clone into an empty directory."
}

New-Item -ItemType Directory -Force -Path "raw_materials", "finished_outputs", ".tree\runtime" | Out-Null

$Python = Find-Python
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Step "Creating .venv with $Python"
    Invoke-Python $Python @("-m", "venv", ".venv")
}

$Extras = if ($Dev) { "rag,dev" } else { "rag" }
$env:PYTHONPATH = "$ProjectRoot\tree_engine;$env:PYTHONPATH"

Write-Step "Device profile"
Write-Host "Project root: $ProjectRoot"
Write-Host "System: Windows $env:PROCESSOR_ARCHITECTURE"
Write-Host "Python: $Python"
Write-Host "Embedding device hint: Windows default"

Write-Step "Installing Python package and dependencies"
& $VenvPython -m pip install -U pip
& $VenvPython -m pip install ".[${Extras}]"

Write-Step "Verifying CLI and embedding imports"
& $VenvPython -c "import tree, ingest, rag; print('packages ok')"
& $VenvPython -c "import llama_cpp, huggingface_hub, fastapi, uvicorn; print('embedding deps ok')"
& $VenvPython -m tree.cli --help *> $null

if (-not $SkipSetup) {
    Write-Step "Starting workspace setup"
    & $VenvPython -m tree.cli setup
} else {
    Write-Step "Skipping workspace setup"
}

if (-not $SkipEmbeddingStart) {
    Start-EmbeddingWithProgress
} else {
    Write-Step "Skipping embedding server startup"
}

Write-Host ""
Write-Host "Bootstrap complete."
Write-Host ""
Write-Host "Next:"
Write-Host "  1. Put course files into raw_materials\"
Write-Host "  2. Open the TREE interactive CLI:"
Write-Host "       .\.venv\Scripts\tre.exe"
Write-Host "  3. Type slash commands inside TREE:"
Write-Host "       /continue"
Write-Host "       /status"
Write-Host "       /stop"
Write-Host "       /quit"
Write-Host ""
Write-Host "Tip:"
Write-Host "  After running: .\.venv\Scripts\Activate.ps1"
Write-Host "  you can use: tre"
