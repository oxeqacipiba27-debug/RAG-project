$PSScriptRoot = Split-Path -Parent -Path $MyInvocation.MyCommand.Definition
Set-Location $PSScriptRoot

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "  Запуск SchoolX RAG Telegram Bot (.venv)" -ForegroundColor Green
Write-Host "===================================================" -ForegroundColor Cyan

$PythonExe = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    Write-Error "[ОШИБКА] Виртуальное окружение .venv не найдено!"
    pause
    exit 1
}

& $PythonExe main.py
