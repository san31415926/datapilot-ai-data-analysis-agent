[CmdletBinding()]
param(
    [string]$Model = "qwen2.5:3b",
    [int]$Port = 8502
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $systemPython = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $systemPython) {
        throw "Python was not found. Install Python 3.11 or newer first."
    }

    Write-Host "Creating the virtual environment..."
    & $systemPython.Source -m venv .venv
}

Write-Host "Checking Python dependencies..."
& $pythonPath -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed. Check the network or run pip install -r requirements.txt manually."
}

$ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
if ($null -eq $ollamaCommand) {
    Write-Warning "Ollama was not found. The page will start, but local model analysis will be unavailable."
    Write-Warning "Install Ollama and run: ollama pull $Model"
} else {
    $ollamaReady = $false
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 | Out-Null
        $ollamaReady = $true
    } catch {
        Write-Host "Starting the Ollama service..."
        Start-Process -FilePath $ollamaCommand.Source -ArgumentList "serve" -WindowStyle Hidden
        for ($attempt = 0; $attempt -lt 15; $attempt++) {
            Start-Sleep -Seconds 1
            try {
                Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 | Out-Null
                $ollamaReady = $true
                break
            } catch {
                $ollamaReady = $false
            }
        }
    }

    if ($ollamaReady) {
        $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5
        $modelNames = @($tags.models | ForEach-Object { [string]$_.name })
        if ($modelNames -notcontains $Model) {
            Write-Host "Preparing the local model $Model ..."
            & $ollamaCommand.Source pull $Model
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Model download failed. The page will start, but model analysis will be unavailable."
            }
        }
    } else {
        Write-Warning "The Ollama service could not be started. The page will still start."
    }
}

function Test-PortInUse([int]$CandidatePort) {
    try {
        return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $CandidatePort -ErrorAction SilentlyContinue)
    } catch {
        return $false
    }
}

while (Test-PortInUse $Port) {
    $Port++
}

Write-Host "DataPilot is starting at http://127.0.0.1:$Port"
& $pythonPath -m streamlit run app.py --server.port $Port
exit $LASTEXITCODE
