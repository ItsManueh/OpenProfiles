@echo off
rem Builds the portable single-file executable: dist\OpenProfiles.exe
rem It bundles Python, the libraries, the Playwright driver, the fonts and the icon.
rem The browsers are not bundled: the .exe downloads them on its first run into
rem %LOCALAPPDATA%\OpenProfiles, where it also keeps profiles, sessions and logs.
setlocal
cd /d "%~dp0"
chcp 65001 >nul

if not exist ".venv\Scripts\python.exe" (
    echo Run start.bat once first, to create the Python environment.
    goto :error
)
".venv\Scripts\python.exe" -c "import PyInstaller" 2>nul || (
    echo Installing PyInstaller...
    ".venv\Scripts\python.exe" -m pip install pyinstaller || goto :error
)

".venv\Scripts\python.exe" -m PyInstaller gui.py ^
    --noconfirm --clean --onefile --windowed ^
    --name OpenProfiles ^
    --icon "%~dp0assets\icon.ico" ^
    --add-data "%~dp0fonts;fonts" ^
    --add-data "%~dp0assets;assets" ^
    --exclude-module tkinter ^
    --distpath "%~dp0dist" --workpath "%~dp0build" --specpath "%~dp0build" || goto :error

echo.
echo Done: dist\OpenProfiles.exe
goto :eof

:error
echo.
echo [!] The build failed.
exit /b 1
