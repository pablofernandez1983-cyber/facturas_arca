"""
Motor de Precios de Transferencia - Segmentacion por linea de negocio.

Genera P&L segmentado en Autos / Pickups / Utilitarios / Motos,
y marca todas las operaciones con partes vinculadas.
"""
from __future__ import annotations
import ast
import pandas as pd
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from motor import LEDGER_COLUMNS, asignar_primera_regla, cargar_fuentes, cargar_reglas


SEGMENTOS = ["AUT", "PKP", "UTI", "MOT"]


def _ledger_base(df: pd.DataFrame, **cols) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for col in LEDGER_COLUMNS:
        out[col] = None
    for col, value in cols.items():
        out[col] = value(df) if callable(value) else value
    return out


def _finalizar_ledger(partes: list[pd.DataFrame]) -> pd.DataFrame:
    partes = [p for p in partes if not p.empty]
    if not partes:
        return pd.DataFrame(columns=LEDGER_COLUMNS)
    ledger = pd.concat(partes, ignore_index=True)
    ledger = ledger.sort_values(["_source_pos", "_alloc_order"], kind="stable")
    ledger = ledger[LEDGER_COLUMNS].reset_index(drop=True)
    ledger["ledger_id"] = range(1, len(ledger) + 1)
    return ledger


def _driver_alloc(driver: pd.Series, driver_nombre: str) -> pd.DataFrame:
    total = driver.sum()
    alloc = driver[driver != 0].rename("driver_valor").reset_index()
    alloc.columns = ["destino", "driver_valor"]
    alloc["driver_total"] = total
    alloc["driver_nombre"] = driver_nombre
    alloc["porcentaje"] = (alloc["driver_valor"] / total * 100).round(3)
    alloc["_alloc_order"] = range(len(alloc))
    return alloc


def _parse_produce(value) -> list[str]:
    if isinstance(value, str):
        try:
            return ast.literal_eval(value)
        except Exception:
            return []
    if isinstance(value, list):
        return value
    return []


def calcular_drivers_pt(fuentes: dict, ledger_ingresos: pd.DataFrame) -> dict:
    produccion = fuentes["produccion_anual"]
    ventas = fuentes["ventas"]

    uni_por_seg = produccion.groupby("segmento")["unidades_producidas"].sum()
    horas_por_seg = produccion.groupby("segmento")["horas_maquina"].sum()
    energia_por_seg = produccion.groupby("segmento")["kg_consumo_energia"].sum()

    ing_por_seg = ledger_ingresos[ledger_ingresos.computable].groupby("destino")["monto_asignado"].sum()

    v_nac = ventas[~ventas.es_exportacion].groupby("segmento")["monto_total"].sum()
    v_exp = ventas[ventas.es_exportacion].groupby("segmento")["monto_total"].sum()

    return {
        "unidades_producidas_por_segmento": uni_por_seg,
        "horas_maquina_por_segmento": horas_por_seg,
        "kg_consumo_energia_por_segmento": energia_por_seg,
        "ingresos_por_segmento": ing_por_seg,
        "ventas_nacionales_por_segmento": v_nac,
        "ventas_export_por_segmento": v_exp,
    }


def procesar_ingresos_pt(fuentes: dict, reglas_ingresos: list[dict]) -> pd.DataFrame:
    ventas = asignar_primera_regla(fuentes["ventas"], reglas_ingresos)
    ledger = _ledger_base(
        ventas, tipo="Ingreso", fuente="ventas",
        id_origen=lambda d: d.factura_id, fecha=lambda d: d.fecha,
        descripcion=lambda d: d.segmento + " - " + d.sku,
        monto_origen=lambda d: d.monto_total,
        regla_id=lambda d: d._regla_id, regla_desc=lambda d: d._regla_desc,
        metodo=lambda d: d._metodo,
        destino=lambda d: d.segmento, porcentaje=100,
        monto_asignado=lambda d: d.monto_total, computable=True,
        vinculada=lambda d: d.es_a_vinculada.astype(bool),
        flag_pt=lambda d: pd.Series(
            pd.NA,
            index=d.index,
            dtype="object",
        ).mask(
            d.es_a_vinculada, "venta_exportacion_vinculada"
        ).mask(
            (~d.es_a_vinculada) & d.es_exportacion, "venta_exportacion_terceros"
        ),
        _source_pos=lambda d: d._source_pos, _alloc_order=0,
    )
    return _finalizar_ledger([ledger])


def _alloc_directo_planta(
    subset: pd.DataFrame,
    regla: dict,
    fuentes: dict,
    drivers: dict,
) -> pd.DataFrame:
    plantas = fuentes["plantas"].copy()
    plantas["_segmentos"] = plantas["produce"].apply(_parse_produce)
    planta_segmentos = plantas.set_index("planta")["_segmentos"].to_dict()
    prod = fuentes["produccion_anual"]
    driver_name = regla.get("driver_multisegmento", "unidades_producidas")

    rows = []
    for planta in subset["planta_imputacion"].drop_duplicates():
        segs = planta_segmentos.get(planta, [])
        if not segs:
            alloc = _driver_alloc(
                drivers["ingresos_por_segmento"].reindex(SEGMENTOS).fillna(0),
                "ingresos_por_segmento (fallback)",
            )
        elif len(segs) == 1:
            alloc = pd.DataFrame({
                "destino": [segs[0]],
                "porcentaje": [100],
                "driver_nombre": [None],
                "driver_valor": [None],
                "driver_total": [None],
                "_alloc_order": [0],
            })
        else:
            intra = prod[prod.planta == planta].set_index("segmento")[driver_name]
            alloc = _driver_alloc(intra, f"{driver_name} (intra-planta {planta})")
        alloc = alloc.copy()
        alloc["planta_imputacion"] = planta
        rows.append(alloc)

    if not rows:
        return pd.DataFrame()
    return subset.merge(pd.concat(rows, ignore_index=True), on="planta_imputacion", how="inner")


def procesar_gastos_pt(fuentes: dict, reglas_gastos: list[dict], drivers: dict) -> pd.DataFrame:
    gastos = asignar_primera_regla(fuentes["gastos"], reglas_gastos)
    partes = []

    def base(df: pd.DataFrame, **extra) -> pd.DataFrame:
        cols = dict(
            tipo="Gasto", fuente="gastos",
            id_origen=lambda d: d.asiento_id, fecha=lambda d: d.fecha,
            cuenta=lambda d: d.cuenta, descripcion=lambda d: d.descripcion_cuenta,
            monto_origen=lambda d: d.monto,
            regla_id=lambda d: d._regla_id, regla_desc=lambda d: d._regla_desc,
            metodo=lambda d: d._metodo, vinculada=lambda d: d.es_a_vinculada.astype(bool),
            flag_pt=lambda d: d._flag_pt if "_flag_pt" in d else None,
            _source_pos=lambda d: d._source_pos, _alloc_order=0,
        )
        cols.update(extra)
        return _ledger_base(df, **cols)

    sin_regla = gastos[gastos["_regla_ix"].isna()]
    partes.append(base(sin_regla, regla_id="SIN_REGLA", computable=False))

    for ix, regla in enumerate(reglas_gastos):
        metodo = regla["metodo"]
        subset = gastos[gastos["_regla_ix"].eq(ix)].copy()
        if subset.empty:
            continue
        subset["_flag_pt"] = regla.get("flag_pt")

        if metodo == "directo_por_planta_a_segmento":
            expanded = _alloc_directo_planta(subset, regla, fuentes, drivers)
        elif metodo == "prorrateo_global_por_driver":
            expanded = subset.merge(_driver_alloc(drivers[regla["driver"]], regla["driver"]), how="cross")
        elif metodo == "prorrateo_global_por_ingresos":
            expanded = subset.merge(_driver_alloc(drivers["ingresos_por_segmento"], "ingresos_por_segmento"), how="cross")
        elif metodo == "prorrateo_global_por_ventas_nacionales":
            expanded = subset.merge(_driver_alloc(drivers["ventas_nacionales_por_segmento"], "ventas_nacionales_por_segmento"), how="cross")
        elif metodo == "prorrateo_global_por_ventas_export":
            expanded = subset.merge(_driver_alloc(drivers["ventas_export_por_segmento"], "ventas_export_por_segmento"), how="cross")
        elif metodo == "prorrateo_global_por_empleados":
            expanded = subset.merge(_driver_alloc(
                drivers["unidades_producidas_por_segmento"],
                "unidades_producidas_por_segmento (proxy empleados)",
            ), how="cross")
        else:
            continue

        if expanded.empty:
            continue
        monto_por_pct = expanded.monto * expanded.porcentaje / 100
        driver_valor = pd.to_numeric(expanded["driver_valor"], errors="coerce")
        driver_total = pd.to_numeric(expanded["driver_total"], errors="coerce")
        monto_por_driver = expanded.monto * driver_valor / driver_total
        monto = monto_por_driver.where(driver_valor.notna(), monto_por_pct)
        partes.append(base(
            expanded, driver_nombre=lambda d: d.driver_nombre,
            driver_valor=lambda d: d.driver_valor, driver_total=lambda d: d.driver_total,
            destino=lambda d: d.destino, porcentaje=lambda d: d.porcentaje,
            monto_asignado=monto, computable=True, _alloc_order=lambda d: d._alloc_order,
        ))

    return _finalizar_ledger(partes)


# =============================================================================
# P&L SEGMENTADO
# =============================================================================

def construir_pnl_segmentado(ledger: pd.DataFrame) -> pd.DataFrame:
    """P&L con filas = tipo de concepto, columnas = segmentos."""
    comp = ledger[ledger.computable].copy()
    comp.loc[comp.tipo == "Gasto", "monto_asignado"] *= -1
    pivot = comp.pivot_table(
        index=["tipo", "descripcion"],
        columns="destino",
        values="monto_asignado",
        aggfunc="sum",
        fill_value=0,
    )
    pivot["Total"] = pivot.sum(axis=1)
    return pivot


def resumen_pnl(ledger: pd.DataFrame) -> pd.DataFrame:
    """P&L resumido por segmento."""
    comp = ledger[ledger.computable].copy()
    ing = comp[comp.tipo == "Ingreso"].groupby("destino")["monto_asignado"].sum()
    gto = comp[comp.tipo == "Gasto"].groupby("destino")["monto_asignado"].sum()
    df = pd.DataFrame({"Ingresos": ing, "Gastos": -gto, "Resultado": ing - gto}).fillna(0)
    df["Margen_%"] = (df.Resultado / df.Ingresos * 100).round(2)
    return df


# =============================================================================
# OPERACIONES VINCULADAS (REPORTE PT)
# =============================================================================

def resumen_operaciones_vinculadas(ledger: pd.DataFrame) -> pd.DataFrame:
    vinc = ledger[ledger.vinculada].copy()
    if vinc.empty:
        return pd.DataFrame()
    resumen = vinc.groupby(["tipo", "flag_pt", "destino"], dropna=False).agg(
        n_operaciones=("id_origen", "nunique"),
        monto_total=("monto_asignado", "sum"),
    ).reset_index()
    return resumen


def run_pt(data_dir: str | Path, reglas_path: str | Path) -> dict:
    fuentes = cargar_fuentes(data_dir)
    reglas = cargar_reglas(reglas_path)

    print(f"Procesando ingresos ({len(fuentes['ventas']):,} facturas)...")
    ledger_ing = procesar_ingresos_pt(fuentes, reglas["ingresos"])

    print("Calculando drivers...")
    drivers = calcular_drivers_pt(fuentes, ledger_ing)

    print(f"Procesando gastos ({len(fuentes['gastos']):,} asientos)...")
    ledger_gto = procesar_gastos_pt(fuentes, reglas["costos_y_gastos"], drivers)

    ledger = pd.concat([ledger_ing, ledger_gto], ignore_index=True)

    return {
        "ledger": ledger,
        "pnl_resumen": resumen_pnl(ledger),
        "pnl_detalle": construir_pnl_segmentado(ledger),
        "vinculadas": resumen_operaciones_vinculadas(ledger),
        "drivers": drivers,
        "fuentes": fuentes,
    }


if __name__ == "__main__":
    BASE = Path(__file__).parent.parent
    out = run_pt(BASE / "data", BASE / "reglas" / "pt_reglas_2025.yaml")
    print(f"\nLedger total: {len(out['ledger']):,} filas")
    print(f"\nP&L Resumen por segmento:")
    print(out["pnl_resumen"].round(0))
    print(f"\nOperaciones con partes vinculadas:")
    print(out["vinculadas"].round(0))
