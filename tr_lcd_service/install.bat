@echo off
cd /d "%~dp0"

echo ============================================================
echo  Thermalright LCD Tray App - Install
echo ============================================================

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found in PATH. Install Python 3.10+ and try again.
    pause
    exit /b 1
)

echo Installing Python dependencies...
python -m pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo ERROR: pip install failed.
    pause
    exit /b 1
)

echo Registering startup entry...
pythonw tray.py --register-startup
if %errorlevel% neq 0 (
    echo WARNING: Could not register startup entry. You can add it manually.
)

echo Launching tray app...
start "" pythonw tray.py

echo.
echo Done. The LCD icon will appear in the notification area.
echo Right-click it and choose "Change Image..." to set your image.
echo Log file: C:\ProgramData\TRLCDService\service.log
pause
