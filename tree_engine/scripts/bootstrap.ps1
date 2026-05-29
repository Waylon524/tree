<#
Bootstrap a fresh T.R.E.E. checkout through interactive workspace setup.

Usage:
  .\tree_engine\scripts\bootstrap.ps1
  .\tree_engine\scripts\bootstrap.ps1 -Dev
  .\tree_engine\scripts\bootstrap.ps1 -SkipSetup
#>

param(
    [switch]$Dev,
    [switch]$SkipSetup
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

if (-not (Test-Path "pyproject.toml")) {
    throw "Run this script from a cloned tree checkout, or use tree_engine\scripts\bootstrap.ps1 from the project root."
}
if (-not (Test-Path "tree_engine")) {
    throw "tree_engine\ is missing. The checkout looks incomplete."
}

New-Item -ItemType Directory -Force -Path "raw_materials", "finished_outputs", "tree_engine\.runtime" | Out-Null

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

Write-Host ""
Write-Host "Bootstrap complete."
Write-Host ""
Write-Host "Next:"
Write-Host "  1. Start the embedding server in one terminal:"
Write-Host "       tree_engine\scripts\start-embed-server.bat"
Write-Host "  2. Open another terminal, activate .venv, then run:"
Write-Host "       .\.venv\Scripts\Activate.ps1"
Write-Host "       tree-run run"
