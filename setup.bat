@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -m venv .venv
    if errorlevel 1 goto error
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error
echo.
echo Dependencies are installed. Copy .env.example to .env and add your bot token.
pause
exit /b 0
:error
echo Setup failed. Check that Python 3.10+ is installed and available as "py".
pause
exit /b 1
