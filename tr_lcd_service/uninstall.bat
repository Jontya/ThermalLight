@echo off
:: Must be run as Administrator
cd /d "%~dp0"

echo ============================================================
echo  Thermalright LCD Service - Uninstall
echo ============================================================

echo Stopping service...
python service.py stop 2>nul

echo Removing service...
python service.py remove
if %errorlevel% neq 0 (
    echo WARNING: Service removal reported an error (it may not have been installed).
    pause
    exit /b 1
)

echo.
echo Service removed successfully.
pause
