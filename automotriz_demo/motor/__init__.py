"""
Motor de aplicación de reglas declarativas (YAML) sobre fuentes de datos.

Diseño:
- Cada ingreso o gasto de origen genera 1+ filas en el ledger.
- Cada fila del ledger documenta: origen, regla aplicada, método, driver,
  destino (jurisdicción o segmento), porcentaje y monto asignado.
- Esto permite trazabilidad total y validaciones automáticas.
"""

from __future__ import annotations
from pathlib import Path
import pandas as pd
import yaml
from typing import Callable


# =============================================================================
# CARGA DE DATOS
# =============================================================================

def cargar_fuentes(data_dir: str | Path) -> dict[str, pd.DataFrame]:
    """Carga todos los .xlsx de la carpeta data como dataframes."""
    data_dir = Path(data_dir)
    fuentes = {}
    for f in sorted(data_dir.glob("*.xlsx")):
        fuentes[f.stem] = pd.read_excel(f)
    return fuentes


def cargar_reglas(yaml_path: str | Path) -> dict:
    with open(yaml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# =============================================================================
# MATCHER DE REGLAS
# =============================================================================

def fila_matchea(fila: pd.Series, condiciones: dict) -> bool:
    """¿La fila cumple todas las condiciones de 'aplica_cuando'?"""
    if not condiciones:
        return True  # regla universal
    for campo, valor_esperado in condiciones.items():
        # Casos especiales
        if campo == "tiene_planta":
            tiene = pd.notna(fila.get("planta_imputacion"))
            if tiene != valor_esperado:
                return False
            continue
        valor_real = fila.get(campo)
        if isinstance(valor_esperado, list):
            if valor_real not in valor_esperado:
                return False
        else:
            if valor_real != valor_esperado:
                return False
    return True


def primera_regla_que_matchea(fila: pd.Series, reglas: list[dict]) -> dict | None:
    for regla in reglas:
        if fila_matchea(fila, regla.get("aplica_cuando", {})):
            return regla
    return None


def mascara_condiciones(df: pd.DataFrame, condiciones: dict) -> pd.Series:
    """Evalua condiciones YAML sobre todo un DataFrame."""
    mask = pd.Series(True, index=df.index)
    if not condiciones:
        return mask
    for campo, valor_esperado in condiciones.items():
        if campo == "tiene_planta":
            tiene = df["planta_imputacion"].notna()
            mask &= tiene == valor_esperado
        elif isinstance(valor_esperado, list):
            mask &= df[campo].isin(valor_esperado)
        else:
            mask &= df[campo] == valor_esperado
    return mask


def asignar_primera_regla(df: pd.DataFrame, reglas: list[dict]) -> pd.DataFrame:
    """Agrega columnas de regla respetando el orden YAML (primera regla gana)."""
    out = df.copy()
    out["_source_pos"] = range(len(out))
    out["_regla_ix"] = pd.NA
    out["_regla_id"] = pd.NA
    out["_regla_desc"] = pd.NA
    out["_metodo"] = pd.NA

    for ix, regla in enumerate(reglas):
        pendientes = out["_regla_ix"].isna()
        mask = pendientes & mascara_condiciones(out, regla.get("aplica_cuando", {}))
        out.loc[mask, "_regla_ix"] = ix
        out.loc[mask, "_regla_id"] = regla.get("id")
        out.loc[mask, "_regla_desc"] = regla.get("descripcion")
        out.loc[mask, "_metodo"] = regla.get("metodo")

    return out


# =============================================================================
# UTILIDADES PARA EL LEDGER
# =============================================================================

def fila_ledger(**kwargs) -> dict:
    """Plantilla de fila estándar del ledger."""
    base = {
        "ledger_id": None,
        "tipo": None,              # Ingreso / Costo / Gasto
        "fuente": None,            # ventas / gastos
        "id_origen": None,         # factura_id o asiento_id
        "fecha": None,
        "cuenta": None,
        "descripcion": None,
        "monto_origen": 0.0,
        "regla_id": None,
        "regla_desc": None,
        "metodo": None,
        "driver_nombre": None,
        "driver_valor": None,
        "driver_total": None,
        "destino": None,           # jurisdicción o segmento (según motor)
        "porcentaje": None,
        "monto_asignado": 0.0,
        "computable": True,
        "flag_pt": None,
        "vinculada": False,
    }
    base.update(kwargs)
    return base


LEDGER_COLUMNS = list(fila_ledger().keys())


def validar_integridad(ledger: pd.DataFrame, fuente: pd.DataFrame, id_col: str, monto_col: str, nombre: str) -> pd.DataFrame:
    """Para cada id_origen, suma de monto_asignado en ledger == monto_origen."""
    asignado = ledger.groupby("id_origen", as_index=False)["monto_asignado"].sum()
    asignado = asignado.rename(columns={"monto_asignado": "asignado"})
    original = fuente[[id_col, monto_col]].rename(columns={id_col: "id_origen", monto_col: "original"})
    merged = original.merge(asignado, on="id_origen", how="left").fillna(0)
    # Restamos las filas no computables (que tienen asignado=0 pero original≠0)
    no_comp = ledger[ledger.computable == False].id_origen.unique()
    merged["no_computable"] = merged.id_origen.isin(no_comp)
    merged["diferencia"] = merged.original - merged.asignado
    # ok si: (i) es no computable y asignado=0, o (ii) diferencia chica
    merged["ok"] = (
        ((merged.no_computable) & (merged.asignado.abs() < 1)) |
        (merged.diferencia.abs() < 1)
    )
    if not merged.ok.all():
        n_malas = (~merged.ok).sum()
        print(f"[{nombre}] ⚠ {n_malas} filas con diferencia. Ejemplos:")
        print(merged[~merged.ok].head())
    return merged
