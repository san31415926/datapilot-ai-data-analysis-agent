@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_datapilot.ps1" %*
if errorlevel 1 pause
endlocal
