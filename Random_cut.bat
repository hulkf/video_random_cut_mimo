@echo off
cd /d "%~dp0"
python main.py
if errorlevel 1 (
    echo.
    echo Program exited with errors. Press any key to close...
    pause >nul
)
