@echo off
REM ============================================================
REM Abre la app Streamlit local para editar reglas y generar salidas DDJJ.
REM ============================================================

setlocal
cd /d "%~dp0"

echo Abriendo Streamlit en http://localhost:8501 ...
python -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501

pause
