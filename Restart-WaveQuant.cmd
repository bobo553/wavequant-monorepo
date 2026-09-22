@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\restart-wavequant.ps1"
if errorlevel 1 (
    echo.
    echo WaveQuant restart failed. Review the message above.
    pause
    exit /b 1
)
endlocal
