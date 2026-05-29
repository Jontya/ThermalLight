@echo off
:: Must be run as Administrator
cd /d "%~dp0"

echo ============================================================
echo  Thermalright LCD Service - Install
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

echo Registering Windows service...
python service.py install
if %errorlevel% neq 0 (
    echo ERROR: Service registration failed.
    pause
    exit /b 1
)

echo Starting service...
python service.py start
if %errorlevel% neq 0 (
    echo ERROR: Service failed to start. Check the log:
    echo   C:\ProgramData\TRLCDService\service.log
    pause
    exit /b 1
)

echo.
echo Service installed and started successfully.
echo Log file: C:\ProgramData\TRLCDService\service.log
pause
