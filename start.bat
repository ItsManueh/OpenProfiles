@echo off
rem Launcher: no arguments opens the interface; "dev" opens development mode (dev.py);
rem any other arguments go to the command line (cli.py).
setlocal
cd /d "%~dp0"
chcp 65001 >nul

if not exist ".venv\Scripts\python.exe" (
    echo Creating the Python environment...
    python -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :error
)
".venv\Scripts\python.exe" -c "import playwright, PySide6" 2>nul || (
    echo Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)

if "%~1"=="" (
    start "" ".venv\Scripts\pythonw.exe" gui.py
) else if /i "%~1"=="dev" (
    ".venv\Scripts\python.exe" dev.py
) else (
    ".venv\Scripts\python.exe" cli.py %*
)
goto :eof

:error
echo.
echo [!] Could not set up the environment. Make sure Python 3.10+ is installed.
pause
