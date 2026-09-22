@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ===================================================
echo   Запуск SchoolX RAG Telegram Bot (.venv)
echo ===================================================
if not exist ".venv\Scripts\python.exe" (
    echo [ОШИБКА] Виртуальное окружение .venv не найдено!
    pause
    exit /b 1
)

".venv\Scripts\python.exe" main.py
pause
