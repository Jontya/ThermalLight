@echo off
cd /d "%~dp0"

echo ============================================================
echo  Thermalright LCD Tray App - Uninstall
echo ============================================================

echo Removing startup entry...
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v TRLCDTray /f >nul 2>&1

echo Closing tray app (if running)...
taskkill /FI "WINDOWTITLE eq TRLCDTray" /F >nul 2>&1
:: pythonw processes have no window title, kill by image name as fallback
:: (user may have other pythonw processes; we warn rather than force-kill all)
echo NOTE: If the tray icon is still visible, right-click it and choose Exit.

echo.
echo Uninstall complete.
pause
