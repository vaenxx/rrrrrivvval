@echo off
cd /d "%~dp0"
if not exist ".env" (
    echo Missing .env. Copy .env.example to .env and add your Discord bot token.
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    echo Missing virtual environment. Run setup.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" main.py
pause
