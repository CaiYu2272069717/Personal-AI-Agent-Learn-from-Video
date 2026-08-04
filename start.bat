@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Python virtual environment not found: .venv
    echo Run: python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)
call ".venv\Scripts\activate.bat"
python -c "from src.app import create_app; app = create_app(); print('App OK')"
if %errorlevel% neq 0 (
    echo.
    echo === APP IMPORT FAILED ===
    pause
    exit /b 1
)
echo.
echo Starting server...
python main.py
pause
