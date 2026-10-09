@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -m venv .venv
    if errorlevel 1 (
        echo Failed to create virtual environment. Make sure Python is installed and py.exe is available.
        pause
        exit /b 1
    )
    echo Installing dependencies...
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Failed to install dependencies.
        pause
        exit /b 1
    )
)
.venv\Scripts\python.exe main.py
