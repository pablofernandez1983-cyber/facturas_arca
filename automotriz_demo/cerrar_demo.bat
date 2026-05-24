@echo off
REM ============================================================
REM Cierra procesos de Marimo asociados a este demo.
REM ============================================================

setlocal
cd /d "%~dp0"

echo Cerrando procesos de Automotriz Demo...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'marimo' -and $_.CommandLine -match 'panel_atribucion' } | ForEach-Object { Write-Host ('Cerrando PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }"

echo Listo.
pause
