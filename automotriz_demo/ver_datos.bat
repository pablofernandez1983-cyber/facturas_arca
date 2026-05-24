@echo off
REM ============================================================
REM Genera y abre una pagina HTML estatica con los datos del demo.
REM No deja servidores ni procesos de fondo.
REM ============================================================

setlocal
cd /d "%~dp0"

echo Generando reporte de datos...
python generar_reporte_html.py
if errorlevel 1 (
    echo.
    echo ERROR: no se pudo generar el reporte.
    pause
    exit /b 1
)

start "" "%~dp0reporte_datos.html"
