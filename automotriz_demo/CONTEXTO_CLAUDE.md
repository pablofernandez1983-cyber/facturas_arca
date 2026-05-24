# Contexto para continuar el proyecto en Claude Code

Proyecto: `automotriz_demo`

Objetivo original:
- Automatizar un proceso anual que toma reportes SAP/Excel y aplica reglas de negocio para segmentar información.
- Resultado buscado: estado de resultados segmentado por negocio y coeficientes/atribuciones con trazabilidad total.
- Requisito clave: poder ver qué regla asignó cada peso, documentar excepciones y cambiar criterios año a año sin perder auditoría.

## Estado actual

El proyecto tiene:
- Datos fuente en `data/*.xlsx`.
- Reglas declarativas en YAML en `reglas/`.
- Motores Python vectorizados en `motor/`.
- Dashboard Marimo en `notebook/panel_atribucion.py`.
- App Streamlit local en `streamlit_app.py`.
- Reporte HTML estático en `reporte_datos.html`.
- CSV generados en `salida/`.

Marimo se levanta con:

```powershell
python -m marimo run notebook\panel_atribucion.py --host 127.0.0.1 --port 2718 --no-token --show-tracebacks
```

URL:

```text
http://127.0.0.1:2718
```

Scripts útiles:
- `abrir_demo.bat`: abre Marimo como app.
- `abrir_streamlit.bat`: abre la app Streamlit local para editar reglas y generar salidas DDJJ.
- `cerrar_demo.bat`: cierra procesos Marimo del demo.
- `ver_datos.bat`: genera y abre un HTML estático, sin servidor.
- `generar_reporte_html.py`: genera `reporte_datos.html` y CSV en `salida/`.

## Cambios importantes ya hechos

1. Se optimizaron los motores:
   - Antes usaban `iterrows()` y podían tardar minutos.
   - Ahora usan operaciones vectorizadas con pandas.
   - Corrida aproximada:
     - CM: ~2-3s.
     - PT: ~2-3s.

2. Se corrigió el diseño Marimo:
   - Había tabs cuyo `.value` no coincidía con las condiciones.
   - Ahora las pestañas renderizan correctamente:
     - Dashboard general.
     - Convenio Multilateral.
     - Precios de Transferencia.
     - Explorador de ledger.
     - Editor de reglas.
     - Reglas activas.

3. Se agregó regla documentada para cliente `CLI-N-0091`:
   - El dato fuente sigue diciendo entrega en `Buenos Aires`.
   - La atribución CM se hace por regla especial a `La Rioja`.
   - Regla: `ING-001A` en `reglas/cm_reglas_2025.yaml`.
   - Motivo: entrega operativa en depósito de Buenos Aires, pero destino económico/planta en La Rioja.
   - Impacto validado: 57 facturas, ARS 2.114,8MM asignados a La Rioja.

4. Se agregó soporte en `motor/cm_motor.py` para:
   - `metodo: directo_a_jurisdiccion`

5. Se agregó un primer Editor de reglas en Marimo:
   - Pestaña `Editor de reglas`.
   - Muestra reglas CM en tabla.
   - Permite seleccionar una regla.
   - Muestra impacto actual en ledger.
   - Permite editar descripción, método, condiciones YAML, parámetros YAML, responsable y notas.
   - Botón `Guardar regla en YAML`.
   - Crea backup en `reglas/cm_reglas_2025.yaml.bak`.
   - Luego se usa `Recalcular datos` para recalcular.

6. Se agregó una app Streamlit local:
   - Archivo: `streamlit_app.py`.
   - Apertura: `abrir_streamlit.bat` o `python -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501`.
   - Pensada como prototipo más cercano al usuario de impuestos.
   - Tiene pestañas:
     - Resumen.
     - Reglas CM.
     - Impacto de reglas.
     - Ledger.
     - Exportar DDJJ.
     - Fuentes.
   - Permite editar reglas CM en una tabla tipo Excel (`st.data_editor`).
   - Valida YAML de condiciones/parámetros.
   - Guarda reglas creando backup con timestamp.
   - Exporta un Excel con coeficientes CM, P&L PT, vinculadas y ledgers.

## Archivos clave

- `motor/__init__.py`
  - Carga de datos.
  - Matching vectorizado de reglas.
  - Validación de integridad.

- `motor/cm_motor.py`
  - Motor Convenio Multilateral.
  - Procesa ingresos/gastos.
  - Calcula drivers y coeficientes.
  - Soporta regla especial `directo_a_jurisdiccion`.

- `motor/pt_motor.py`
  - Motor Precios de Transferencia.
  - Segmenta P&L por negocio.
  - Marca operaciones vinculadas.

- `reglas/cm_reglas_2025.yaml`
  - Reglas CM.
  - Incluye `ING-001A`.

- `reglas/pt_reglas_2025.yaml`
  - Reglas PT / segmentación.

- `notebook/panel_atribucion.py`
  - App Marimo.
  - Tiene dashboard, visualizaciones, explorador y editor de reglas.

- `streamlit_app.py`
  - App Streamlit local orientada al flujo operativo de impuestos/DDJJ.
  - Usa el mismo motor y las mismas reglas.
  - Mejor candidato para convertir el prototipo en herramienta usable por negocio.

- `generar_reporte_html.py`
  - Genera salida estática para abrir sin Marimo.

## Cambios sesión 2026-05-24

7. Streamlit mejorado — `streamlit_app.py` ahora tiene:
   - **`@st.fragment`** en 5 tabs (CM, PT, Impacto, Ledger, Fuentes): cada tab re-corre solo su función cuando cambia un widget interno, sin recargar toda la página.
   - Shim de compatibilidad: `_fragment = getattr(st, "fragment", lambda fn: fn)` — si Streamlit < 1.37, sigue funcionando sin reactividad parcial.
   - Tab Reglas CM queda fuera del fragment a propósito: el botón Guardar hace `st.rerun()` para que los motores se recalculen.
   - Charts CM y PT reemplazados de `st.bar_chart` por `st.altair_chart` con colores y tooltips propios.
   - Tab Ledger: agregados toggles "Solo partes vinculadas" e "Incluir no computables" (igual que Marimo).
   - Bug de encoding `notas_aÃ±o` corregido.

8. Exclusivo Streamlit (no replicable en Marimo):
   - `st.data_editor` — edición inline con doble clic.
   - `st.download_button` — descarga de Excel directo desde el browser.

9. Exclusivo Marimo (no replicable en Streamlit):
   - Reactividad real: solo re-corren las celdas que dependen del widget cambiado.
   - Botón Recalcular quirúrgico: re-corre solo la celda de motores + dependientes.

## Próximos pasos sugeridos

1. Mejorar el Editor de reglas:
   - Agregar creación de nueva regla.
   - Agregar duplicar regla.
   - Agregar mover regla arriba/abajo, porque el orden importa.
   - Agregar validación visual de condiciones YAML.
   - Agregar comparación antes/después al guardar.

2. Separar más formalmente:
   - motor de reglas,
   - UI,
   - auditoría/versionado.

3. Pensar evolución:
   - Marimo sirve bien como prototipo vivo.
   - Para usuarios de negocio, evaluar Streamlit o React/FastAPI.
   - Para procesos productivos/auditables, considerar dbt/Dagster + UI propia.

## Comandos rápidos

Instalar dependencias:

```powershell
python -m pip install marimo streamlit pandas pyyaml openpyxl altair
```

Correr motores:

```powershell
python motor\cm_motor.py
python motor\pt_motor.py
```

Abrir Marimo:

```powershell
python -m marimo run notebook\panel_atribucion.py --host 127.0.0.1 --port 2718 --no-token --show-tracebacks
```

Abrir Streamlit:

```powershell
python -m streamlit run streamlit_app.py --server.address 127.0.0.1 --server.port 8501
```

Generar HTML estático:

```powershell
python generar_reporte_html.py
```
