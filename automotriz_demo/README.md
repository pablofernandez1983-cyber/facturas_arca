# 🚗 Automotriz Argentina S.A. — Demo de Atribución

Demo completo de motor de reglas declarativas (YAML) + ledger trazable + dashboard
interactivo (Marimo) aplicado a:

1. **Convenio Multilateral** — coeficientes por jurisdicción
2. **Precios de Transferencia** — P&L segmentado por línea de negocio

Una empresa ficticia: fabricante de autos, pickups, utilitarios y motos con 3
plantas, 2.000 empleados, 195 clientes (incluyendo exportación a casa matriz),
105 proveedores (8 vinculados) y ~12.000 facturas + 8.000 asientos contables al año.

---

## Estructura

```
automotriz_demo/
├── data/
│   ├── generar_datos.py        # Generador de datos sintéticos
│   ├── ventas.xlsx             # 12.000 facturas
│   ├── gastos.xlsx             # 8.000 asientos
│   ├── empleados.xlsx          # 2.000 empleados
│   ├── clientes.xlsx           # Padrón clientes (con flag vinculadas)
│   ├── proveedores.xlsx        # Padrón proveedores (con flag vinculadas)
│   ├── productos.xlsx          # SKUs por segmento
│   ├── plantas.xlsx            # Catálogo de plantas
│   ├── partes_relacionadas.xlsx # Catálogo de partes vinculadas
│   ├── produccion_anual.xlsx   # Unidades, kg, horas máquina por planta/segmento
│   └── sucursales.xlsx         # m² por jurisdicción
│
├── reglas/
│   ├── cm_reglas_2025.yaml     # Reglas de Convenio Multilateral
│   └── pt_reglas_2025.yaml     # Reglas de Precios de Transferencia
│
├── motor/
│   ├── __init__.py             # Carga, matcher de reglas, validaciones comunes
│   ├── cm_motor.py             # Motor CM (produce ledger CM y coeficientes)
│   └── pt_motor.py             # Motor PT (produce ledger PT y P&L segmentado)
│
└── notebook/
    └── panel_atribucion.py     # Marimo: dashboard interactivo con tabs
```

## Cómo correr

```bash
pip install marimo pandas pyyaml openpyxl altair

# 1. Generar datos sintéticos
cd data && python generar_datos.py

# 2. Correr motores standalone (sin UI)
python motor/cm_motor.py
python motor/pt_motor.py

# 3. Levantar el dashboard interactivo
marimo edit notebook/panel_atribucion.py
```

## Arquitectura: las 3 capas

### Capa 1 — Fuentes (Excel/CSV)
Los datos crudos como salen de los sistemas (SAP, Tango, lo que sea). Ningún
script las modifica.

### Capa 2 — Reglas (YAML)
Las reglas de atribución se declaran en YAML, **separadas del código del motor**.
Una persona de finanzas puede leerlas/editarlas sin saber Python. Cada regla
documenta:
- `id`: identificador único (citable en auditoría)
- `descripcion`: qué hace
- `aplica_cuando`: condiciones de matching
- `metodo`: cómo se atribuye
- `sustento_normativo`: opcional, citación legal/regulatoria
- `responsable_decision`: quién firmó el criterio
- `notas_año`: contexto del año actual (CRÍTICO para auditoría futura)

### Capa 3 — Motor (Python)
Aplica las reglas a las fuentes y produce el **ledger**: una tabla larga donde
cada peso de origen genera 1+ filas con TODA la trazabilidad:
- monto origen
- regla aplicada
- método
- driver usado (valor + total)
- destino (jurisdicción para CM, segmento para PT)
- porcentaje
- monto asignado
- flags adicionales (computable, vinculada, flag_pt)

## El ledger en números

Con este dataset:
- **CM**: 59.712 filas de ledger (a partir de 20.000 movimientos origen)
- **PT**: 34.585 filas de ledger

Cada fila del ledger te permite responder *"¿por qué este peso terminó acá?"*
sin tener que volver a correr nada.

## Filosofía: por qué este diseño

**Sobre KNIME / Alteryx**: este enfoque es superior cuando hay muchas reglas
con excepciones porque el YAML escala mucho mejor que un workflow visual de
nodos.

**Sobre Excel multi-solapa**: el ledger reemplaza decenas de solapas
intermedias con UNA sola tabla larga. La trazabilidad inversa es instantánea
(filtrá por jurisdicción + cuenta = ves la composición exacta).

**Sobre Python "puro" con reglas hardcodeadas**: separar reglas del motor
permite que un perfil contable edite las reglas sin tocar código. Y queda
versionado en git.

## Cómo iterar reglas (uso real)

1. Abrís el Marimo y vas a la pestaña "Reglas activas" para revisar lo vigente.
2. Editás `cm_reglas_2025.yaml` o `pt_reglas_2025.yaml` con tu editor favorito.
3. El Marimo detecta el cambio y re-ejecuta todo automáticamente.
4. Ves el impacto en coeficientes / P&L en segundo plano.
5. Si te convence, commiteás el YAML en git. Si no, lo descartás.

Para el año siguiente: copiás el YAML a `_2026.yaml` y editás solo lo que
cambió, agregando `notas_año` con el motivo. El año viejo queda intacto y
auditable.
