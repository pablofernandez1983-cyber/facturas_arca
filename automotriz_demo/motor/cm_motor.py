"""
Motor de Convenio Multilateral.
Lee ventas + gastos + auxiliares, aplica reglas YAML, genera ledger CM.
Output: ledger granular + coeficientes por jurisdiccion.
"""
from __future__ import annotations
import pandas as pd
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from motor import LEDGER_COLUMNS, asignar_primera_regla, cargar_fuentes, cargar_reglas


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


# =============================================================================
# CALCULO DE DRIVERS (calculados una vez al inicio)
# =============================================================================

def calcular_drivers_cm(fuentes: dict, ledger_ingresos: pd.DataFrame) -> dict:
    """
    Calcula los drivers que se usan para prorratear gastos.
    Retorna dict {nombre_driver: pd.Series(jurisdiccion -> valor)}
    """
    empleados = fuentes["empleados"]
    produccion = fuentes["produccion_anual"]
    plantas = fuentes["plantas"]

    emp_por_jur = empleados.groupby("jurisdiccion_trabajo").size()

    plantas_jur = plantas.set_index("planta")["jurisdiccion"]
    prod_con_jur = produccion.copy()
    prod_con_jur["jurisdiccion"] = prod_con_jur.planta.map(plantas_jur)
    horas_por_jur = prod_con_jur.groupby("jurisdiccion")["horas_maquina"].sum()

    ing_nac = ledger_ingresos[ledger_ingresos.computable & ledger_ingresos.destino.notna()]
    ing_por_jur = ing_nac.groupby("destino")["monto_asignado"].sum()

    ventas = fuentes["ventas"]
    ventas_exp = ventas[ventas.es_exportacion]
    ventas_exp_por_planta = ventas_exp.groupby("planta_origen")["monto_total"].sum()
    ventas_exp_por_jur = ventas_exp_por_planta.rename(index=plantas_jur).groupby(level=0).sum()

    return {
        "empleados_por_jurisdiccion": emp_por_jur,
        "horas_maquina_por_jurisdiccion": horas_por_jur,
        "ingresos_por_jurisdiccion": ing_por_jur,
        "ventas_export_por_jurisdiccion": ventas_exp_por_jur,
    }


# =============================================================================
# PROCESAMIENTO DE INGRESOS
# =============================================================================

def procesar_ingresos_cm(fuentes: dict, reglas_ingresos: list[dict]) -> pd.DataFrame:
    ventas = asignar_primera_regla(fuentes["ventas"], reglas_ingresos)
    partes = []

    sin_regla = ventas[ventas["_regla_ix"].isna()]
    partes.append(_ledger_base(
        sin_regla, tipo="Ingreso", fuente="ventas",
        id_origen=lambda d: d.factura_id, fecha=lambda d: d.fecha,
        descripcion=lambda d: d.segmento + " " + d.sku,
        monto_origen=lambda d: d.monto_total,
        regla_id="SIN_REGLA", regla_desc="No matcheo ninguna regla",
        computable=False, _source_pos=lambda d: d._source_pos, _alloc_order=0,
    ))

    excl = ventas[ventas["_metodo"] == "excluir"]
    partes.append(_ledger_base(
        excl, tipo="Ingreso", fuente="ventas",
        id_origen=lambda d: d.factura_id, fecha=lambda d: d.fecha,
        descripcion=lambda d: d.segmento + " " + d.sku + " (exportacion)",
        monto_origen=lambda d: d.monto_total,
        regla_id=lambda d: d._regla_id, regla_desc=lambda d: d._regla_desc,
        metodo="excluir", computable=False,
        vinculada=lambda d: d.es_a_vinculada.astype(bool),
        _source_pos=lambda d: d._source_pos, _alloc_order=0,
    ))

    for ix, regla in enumerate(reglas_ingresos):
        if regla["metodo"] not in ("directo_por_campo", "directo_a_jurisdiccion"):
            continue
        directo = ventas[ventas["_regla_ix"].eq(ix)]
        if regla["metodo"] == "directo_por_campo":
            destino = lambda d, c=regla["campo_jurisdiccion"]: d[c]
        else:
            destino = regla["jurisdiccion"]
        partes.append(_ledger_base(
            directo, tipo="Ingreso", fuente="ventas",
            id_origen=lambda d: d.factura_id, fecha=lambda d: d.fecha,
            descripcion=lambda d: d.segmento + " " + d.sku,
            monto_origen=lambda d: d.monto_total,
            regla_id=lambda d: d._regla_id, regla_desc=lambda d: d._regla_desc,
            metodo=regla["metodo"], destino=destino,
            porcentaje=100, monto_asignado=lambda d: d.monto_total,
            computable=True, _source_pos=lambda d: d._source_pos, _alloc_order=0,
        ))

    return _finalizar_ledger(partes)


# =============================================================================
# PROCESAMIENTO DE GASTOS
# =============================================================================

def procesar_gastos_cm(fuentes: dict, reglas_gastos: list[dict], drivers: dict) -> pd.DataFrame:
    gastos = asignar_primera_regla(fuentes["gastos"], reglas_gastos)
    plantas = fuentes["plantas"].set_index("planta")["jurisdiccion"]
    partes = []

    def base(df: pd.DataFrame, **extra) -> pd.DataFrame:
        cols = dict(
            tipo="Gasto", fuente="gastos",
            id_origen=lambda d: d.asiento_id, fecha=lambda d: d.fecha,
            cuenta=lambda d: d.cuenta, descripcion=lambda d: d.descripcion_cuenta,
            monto_origen=lambda d: d.monto,
            regla_id=lambda d: d._regla_id, regla_desc=lambda d: d._regla_desc,
            metodo=lambda d: d._metodo, vinculada=lambda d: d.es_a_vinculada.astype(bool),
            _source_pos=lambda d: d._source_pos, _alloc_order=0,
        )
        cols.update(extra)
        return _ledger_base(df, **cols)

    sin_regla = gastos[gastos["_regla_ix"].isna()]
    partes.append(base(sin_regla, regla_id="SIN_REGLA", regla_desc="No matcheo", computable=False))

    no_comp = gastos[gastos["_metodo"] == "no_computable"]
    partes.append(base(no_comp, computable=False))

    directo = gastos[gastos["_metodo"] == "directo_por_planta"]
    partes.append(base(
        directo, destino=lambda d: d.planta_imputacion.map(plantas),
        porcentaje=100, monto_asignado=lambda d: d.monto, computable=True,
    ))

    for ix, regla in enumerate(reglas_gastos):
        metodo = regla["metodo"]
        subset = gastos[gastos["_regla_ix"].eq(ix)]
        if subset.empty:
            continue

        if metodo == "prorrateo_por_driver":
            alloc = _driver_alloc(drivers[regla["driver"]], regla["driver"])
        elif metodo == "prorrateo_por_ingresos_nacionales":
            alloc = _driver_alloc(drivers["ingresos_por_jurisdiccion"], "ingresos_nacionales_por_jur")
        elif metodo == "prorrateo_por_ventas_export_por_planta":
            alloc = _driver_alloc(drivers["ventas_export_por_jurisdiccion"], "ventas_export_por_jur_planta")
        elif metodo == "porcentaje_manual":
            rows = []
            ingresos = drivers["ingresos_por_jurisdiccion"]
            porcentajes = regla["porcentajes_jurisdiccion"]
            jur_named = [j for j in porcentajes if j != "Otras"]
            jur_otras = [j for j in ingresos.index if j not in jur_named]
            for order, (destino, pct) in enumerate(porcentajes.items()):
                if destino == "Otras" and jur_otras:
                    subset_ing = ingresos.loc[jur_otras]
                    sub_total = subset_ing.sum()
                    if sub_total == 0:
                        continue
                    for sub_dest, sub_val in subset_ing.items():
                        rows.append({
                            "destino": sub_dest,
                            "porcentaje": round(pct * sub_val / sub_total, 3),
                            "_monto_pct": pct * sub_val / sub_total,
                            "driver_nombre": "manual+ingresos(otras)",
                            "_alloc_order": order,
                        })
                elif destino != "Otras":
                    rows.append({
                        "destino": destino, "porcentaje": pct,
                        "_monto_pct": pct,
                        "driver_nombre": "porcentaje_manual", "_alloc_order": order,
                    })
            alloc = pd.DataFrame(rows)
        else:
            continue

        expanded = subset.merge(alloc, how="cross")
        if metodo == "porcentaje_manual":
            monto = expanded.monto * expanded._monto_pct / 100
        else:
            monto = expanded.monto * expanded.driver_valor / expanded.driver_total
        partes.append(base(
            expanded, driver_nombre=lambda d: d.driver_nombre,
            driver_valor=lambda d: d.driver_valor if "driver_valor" in d else None,
            driver_total=lambda d: d.driver_total if "driver_total" in d else None,
            destino=lambda d: d.destino, porcentaje=lambda d: d.porcentaje,
            monto_asignado=monto, computable=True, _alloc_order=lambda d: d._alloc_order,
        ))

    return _finalizar_ledger(partes)


# =============================================================================
# COEFICIENTES
# =============================================================================

def calcular_coeficientes(ledger: pd.DataFrame) -> pd.DataFrame:
    comp = ledger[ledger.computable & ledger.destino.notna()].copy()
    ing = comp[comp.tipo == "Ingreso"].groupby("destino")["monto_asignado"].sum()
    gto = comp[comp.tipo == "Gasto"].groupby("destino")["monto_asignado"].sum()
    coef = pd.DataFrame({"ingresos": ing, "gastos": gto}).fillna(0)
    coef["coef_ingresos"] = coef.ingresos / coef.ingresos.sum() if coef.ingresos.sum() > 0 else 0
    coef["coef_gastos"] = coef.gastos / coef.gastos.sum() if coef.gastos.sum() > 0 else 0
    coef["coef_unificado"] = (coef.coef_ingresos + coef.coef_gastos) / 2
    return coef.sort_values("coef_unificado", ascending=False)


# =============================================================================
# RUN
# =============================================================================

def run_cm(data_dir: str | Path, reglas_path: str | Path) -> dict:
    fuentes = cargar_fuentes(data_dir)
    reglas = cargar_reglas(reglas_path)

    print(f"Procesando ingresos ({len(fuentes['ventas']):,} facturas)...")
    ledger_ing = procesar_ingresos_cm(fuentes, reglas["ingresos"])

    print("Calculando drivers...")
    drivers = calcular_drivers_cm(fuentes, ledger_ing)

    print(f"Procesando gastos ({len(fuentes['gastos']):,} asientos)...")
    ledger_gto = procesar_gastos_cm(fuentes, reglas["gastos"], drivers)

    ledger = pd.concat([ledger_ing, ledger_gto], ignore_index=True)
    coeficientes = calcular_coeficientes(ledger)

    return {
        "ledger": ledger,
        "coeficientes": coeficientes,
        "drivers": drivers,
        "fuentes": fuentes,
    }


if __name__ == "__main__":
    BASE = Path(__file__).parent.parent
    out = run_cm(BASE / "data", BASE / "reglas" / "cm_reglas_2025.yaml")
    print(f"\nLedger total: {len(out['ledger']):,} filas")
    print(f"\nTop 10 jurisdicciones por coef_unificado:")
    print(out["coeficientes"].head(10).round(4))
    print(f"\nCoeficientes suman: {out['coeficientes'].coef_unificado.sum():.4f} (debe = 1.0)")
