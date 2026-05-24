@echo off
REM ============================================================
REM Automotriz Demo - Setup automatico para Windows
REM Hace doble clic y listo: instala deps, fixea rutas, abre Marimo
REM ============================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo ============================================================
echo  AUTOMOTRIZ DEMO - Setup automatico
echo ============================================================
echo.

REM --- 1. Verificar Python ---
echo [1/5] Verificando Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: Python no esta instalado o no esta en el PATH.
    echo  Instalalo desde https://www.python.org/downloads/
    echo  IMPORTANTE: durante la instalacion, marca "Add Python to PATH"
    echo.
    pause
    exit /b 1
)
python --version

REM --- 2. Instalar dependencias ---
echo.
echo [2/5] Instalando dependencias (marimo, pandas, etc)...
echo Esto puede tardar 1-2 minutos la primera vez...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet marimo pandas pyyaml openpyxl altair
if errorlevel 1 (
    echo  ERROR: fallo la instalacion. Probando sin --quiet para ver el error...
    python -m pip install marimo pandas pyyaml openpyxl altair
    pause
    exit /b 1
)
echo Dependencias OK.

REM --- 3. Fixear rutas hardcodeadas en el notebook ---
echo.
echo [3/5] Ajustando rutas para tu sistema...
python -c "from pathlib import Path; import re; p=Path('notebook/panel_atribucion.py'); t=p.read_text(encoding='utf-8'); t=re.sub(r'BASE = Path\\(\"/home/claude/automotriz_demo\"\\)', 'BASE = Path(__file__).parent.parent', t); p.write_text(t, encoding='utf-8'); print('Rutas ajustadas.')"

REM --- 4. Verificar que los datos esten generados ---
echo.
echo [4/5] Verificando datos...
if not exist "data\ventas.xlsx" (
    echo Los datos no estan generados. Generando ahora...
    cd data
    python generar_datos.py
    cd ..
) else (
    echo Datos OK ^(ventas.xlsx encontrado^).
)

REM --- 5. Abrir Marimo ---
echo.
echo [5/5] Abriendo el dashboard...
echo.
echo ============================================================
echo  Se va a abrir tu navegador en http://localhost:2718
echo  Si no se abre solo, copia esa URL a mano.
echo.
echo  La primera carga tarda unos segundos.
echo  Para CERRAR, vuelve a esta ventana y presiona Ctrl+C.
echo ============================================================
echo.

python -m marimo run notebook\panel_atribucion.py --host 127.0.0.1 --port 2718 --no-token

pause
