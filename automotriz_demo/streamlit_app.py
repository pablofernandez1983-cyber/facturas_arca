from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
import shutil

import altair as alt
import pandas as pd
import streamlit as st
import yaml

from motor import cm_motor, pt_motor


BASE      = Path(__file__).parent
DATA_DIR  = BASE / "data"
REGLAS_CM = BASE / "reglas" / "cm_reglas_2025.yaml"
REGLAS_PT = BASE / "reglas" / "pt_reglas_2025.yaml"
SALIDA    = BASE / "salida"
SALIDA.mkdir(exist_ok=True)

SYSTEM_FIELDS = {
    "id", "descripcion", "aplica_cuando", "metodo",
    "sustento_normativo", "responsable_decision",
    "notas_año",
}

# Compatibility shim: st.fragment arrived in Streamlit 1.37.
# On older installs the decorator is a no-op so the app still runs.
_fragment = getattr(st, "fragment", lambda fn: fn)


# ── helpers de reglas ──────────────────────────────────────────────────────────

def yaml_text(value) -> str:
    if value in (None, ""):
        return "{}"
    return yaml.safe_dump(
        value, allow_unicode=True, sort_keys=False, default_flow_style=False
    ).strip()


def parse_yaml_dict(text: str, field_name: str) -> dict:
    value = yaml.safe_load(text or "{}")
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} debe ser un diccionario YAML.")
    return value


def load_rules(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def rules_to_table(reglas: dict) -> pd.DataFrame:
    rows = []
    for bloque in ("ingresos", "gastos"):
        for orden, regla in enumerate(reglas.get(bloque, [])):
            extra = {k: v for k, v in regla.items() if k not in SYSTEM_FIELDS}
            rows.append({
                "bloque":               bloque,
                "orden":                orden,
                "id":                   regla.get("id", ""),
                "descripcion":          regla.get("descripcion", ""),
                "condiciones_yaml":     yaml_text(regla.get("aplica_cuando", {})),
                "metodo":               regla.get("metodo", ""),
                "parametros_yaml":      yaml_text(extra),
                "sustento_normativo":   regla.get("sustento_normativo", ""),
                "responsable_decision": regla.get("responsable_decision", ""),
                "notas_año":            regla.get("notas_año", ""),
            })
    return pd.DataFrame(rows)


def table_to_rules(df: pd.DataFrame) -> dict:
    reglas = {"ingresos": [], "gastos": []}
    required = {"bloque", "orden", "id", "descripcion", "condiciones_yaml", "metodo", "parametros_yaml"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Faltan columnas: {', '.join(sorted(missing))}")

    cleaned = df.copy()
    cleaned = cleaned[cleaned["id"].astype(str).str.strip() != ""]
    cleaned["orden"] = pd.to_numeric(cleaned["orden"], errors="coerce").fillna(999999).astype(int)
    cleaned = cleaned.sort_values(["bloque", "orden", "id"], kind="stable")

    for _, row in cleaned.iterrows():
        bloque = str(row["bloque"]).strip()
        if bloque not in reglas:
            raise ValueError(f"Bloque inválido en regla {row['id']}: {bloque}")
        condiciones = parse_yaml_dict(str(row.get("condiciones_yaml", "{}")), f"condiciones ({row['id']})")
        parametros  = parse_yaml_dict(str(row.get("parametros_yaml", "{}")),  f"parámetros ({row['id']})")
        regla: dict = {
            "id":            str(row["id"]).strip(),
            "descripcion":   str(row.get("descripcion", "")).strip(),
            "aplica_cuando": condiciones,
            "metodo":        str(row.get("metodo", "")).strip(),
        }
        regla.update(parametros)
        for col in ("sustento_normativo", "responsable_decision", "notas_año"):
            val = row.get(col, "")
            if pd.notna(val) and str(val).strip():
                regla[col] = str(val).strip()
        reglas[bloque].append(regla)
    return reglas


def save_rules_from_table(df: pd.DataFrame) -> Path:
    reglas = table_to_rules(df)
    stamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = REGLAS_CM.with_name(f"{REGLAS_CM.name}.{stamp}.bak")
    shutil.copy2(REGLAS_CM, backup)
    REGLAS_CM.write_text(
        yaml.safe_dump(reglas, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return backup


# ── motores con cache ──────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def run_motores(cm_mtime: float, pt_mtime: float, data_sig: tuple) -> dict:
    del cm_mtime, pt_mtime, data_sig
    return {
        "cm": cm_motor.run_cm(DATA_DIR, REGLAS_CM),
        "pt": pt_motor.run_pt(DATA_DIR, REGLAS_PT),
    }


def data_signature() -> tuple:
    return tuple((p.name, p.stat().st_mtime) for p in sorted(DATA_DIR.glob("*.xlsx")))


def current_outputs() -> dict:
    return run_motores(
        REGLAS_CM.stat().st_mtime,
        REGLAS_PT.stat().st_mtime,
        data_signature(),
    )


def build_excel_export(out: dict) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        out["cm"]["coeficientes"].reset_index().to_excel(w, sheet_name="CM_coeficientes", index=False)
        out["pt"]["pnl_resumen"].reset_index().to_excel(w, sheet_name="PT_PyL", index=False)
        out["pt"]["vinculadas"].to_excel(w, sheet_name="PT_vinculadas", index=False)
        out["cm"]["ledger"].head(100_000).to_excel(w, sheet_name="Ledger_CM", index=False)
        out["pt"]["ledger"].head(100_000).to_excel(w, sheet_name="Ledger_PT", index=False)
    return buf.getvalue()


def write_outputs(out: dict) -> None:
    out["cm"]["ledger"].to_csv(SALIDA / "ledger_cm.csv", index=False, encoding="utf-8-sig")
    out["pt"]["ledger"].to_csv(SALIDA / "ledger_pt.csv", index=False, encoding="utf-8-sig")
    out["cm"]["coeficientes"].to_csv(SALIDA / "coeficientes_cm.csv", encoding="utf-8-sig")
    out["pt"]["pnl_resumen"].to_csv(SALIDA / "pnl_pt.csv", encoding="utf-8-sig")
    out["pt"]["vinculadas"].to_csv(SALIDA / "operaciones_vinculadas_pt.csv", index=False, encoding="utf-8-sig")


# ── fragments (re-corren solo cuando cambia un widget interno) ─────────────────

@_fragment
def tab_convenio_multilateral(out: dict) -> None:
    st.subheader("Coeficientes por jurisdicción")

    coef  = out["cm"]["coeficientes"].reset_index().copy()
    top_n = st.slider("Jurisdicciones a mostrar", 5, len(coef), min(15, len(coef)), key="cm_topn")
    mostrar_nc = st.toggle("Mostrar gastos NO computables", value=False)

    coef_disp = pd.DataFrame({
        "Jurisdicción": coef["destino"].head(top_n),
        "Ingresos MM":  (coef["ingresos"]       / 1e6).round(1).head(top_n),
        "Gastos MM":    (coef["gastos"]          / 1e6).round(1).head(top_n),
        "Coef Ing %":   (coef["coef_ingresos"]  * 100).round(3).head(top_n),
        "Coef Gto %":   (coef["coef_gastos"]    * 100).round(3).head(top_n),
        "Coef CM %":    (coef["coef_unificado"] * 100).round(3).head(top_n),
    })

    col_tabla, col_chart = st.columns([1, 1])
    with col_tabla:
        st.dataframe(
            coef_disp,
            hide_index=True,
            use_container_width=True,
            height=420,
            column_config={
                "Jurisdicción": st.column_config.TextColumn(width="medium"),
                "Ingresos MM":  st.column_config.NumberColumn(format="$ %.1f"),
                "Gastos MM":    st.column_config.NumberColumn(format="$ %.1f"),
                "Coef Ing %":   st.column_config.NumberColumn(format="%.3f"),
                "Coef Gto %":   st.column_config.NumberColumn(format="%.3f"),
                "Coef CM %":    st.column_config.NumberColumn(format="%.3f"),
            },
        )
    with col_chart:
        st.altair_chart(
            alt.Chart(coef_disp.sort_values("Coef CM %"))
            .mark_bar(color="#2563eb")
            .encode(
                x=alt.X("Coef CM %:Q", title="Coeficiente CM (%)"),
                y=alt.Y("Jurisdicción:N", sort=None, title=None),
                tooltip=["Jurisdicción", alt.Tooltip("Coef CM %:Q", format=".3f")],
            )
            .properties(height=420),
            use_container_width=True,
        )

    if mostrar_nc:
        st.divider()
        st.subheader("Gastos NO computables")
        ledger_cm = out["cm"]["ledger"]
        no_comp   = ledger_cm[~ledger_cm.computable]
        sumario   = (
            no_comp.groupby("regla_desc")["monto_origen"]
            .agg(asientos="count", monto="sum")
            .reset_index()
        )
        sumario["Monto excluido MM"] = (sumario["monto"] / 1e6).round(1)
        st.dataframe(
            sumario[["regla_desc", "asientos", "Monto excluido MM"]].rename(
                columns={"regla_desc": "Regla", "asientos": "Asientos"}
            ),
            hide_index=True,
            use_container_width=True,
            column_config={
                "Regla":             st.column_config.TextColumn(width="large"),
                "Asientos":          st.column_config.NumberColumn(format="%d"),
                "Monto excluido MM": st.column_config.NumberColumn(format="$ %.1f"),
            },
        )


@_fragment
def tab_precios_transferencia(out: dict) -> None:
    st.subheader("P&L segmentado por línea de negocio")

    pnl      = out["pt"]["pnl_resumen"].reset_index().copy()
    pnl_disp = pd.DataFrame({
        "Segmento":     pnl["destino"],
        "Ingresos MM":  (pnl["Ingresos"]  / 1e6).round(1),
        "Gastos MM":    (pnl["Gastos"]     / 1e6).round(1),
        "Resultado MM": (pnl["Resultado"]  / 1e6).round(1),
        "Margen %":     pnl["Margen_%"].round(1),
    })

    col_pnl, col_chart_pt = st.columns([1, 1])
    with col_pnl:
        st.dataframe(
            pnl_disp,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Segmento":     st.column_config.TextColumn(width="small"),
                "Ingresos MM":  st.column_config.NumberColumn(format="$ %.1f"),
                "Gastos MM":    st.column_config.NumberColumn(format="$ %.1f"),
                "Resultado MM": st.column_config.NumberColumn(format="$ %.1f"),
                "Margen %":     st.column_config.NumberColumn(format="%.1f"),
            },
        )
    with col_chart_pt:
        _pt_melt = pnl_disp.melt(
            id_vars="Segmento",
            value_vars=["Ingresos MM", "Gastos MM"],
            var_name="Concepto",
            value_name="Monto MM",
        )
        st.altair_chart(
            alt.Chart(_pt_melt)
            .mark_bar()
            .encode(
                x=alt.X("Segmento:N", title="Segmento", sort=["AUT", "PKP", "UTI", "MOT"]),
                y=alt.Y("Monto MM:Q", title="Monto (MM ARS)"),
                color=alt.Color(
                    "Concepto:N",
                    scale=alt.Scale(
                        domain=["Ingresos MM", "Gastos MM"],
                        range=["#16a34a", "#dc2626"],
                    ),
                ),
                tooltip=["Segmento", "Concepto", alt.Tooltip("Monto MM:Q", format=",.1f")],
            )
            .properties(height=280),
            use_container_width=True,
        )

    st.divider()
    st.subheader("Operaciones con partes vinculadas")
    st.caption("Transacciones que requieren análisis de PT (Art. 15 LIG).")
    vinc = out["pt"]["vinculadas"].copy()
    if vinc.empty:
        st.info("Sin operaciones vinculadas en la corrida actual.")
    else:
        vinc["monto_MM"] = (vinc["monto_total"] / 1e6).round(1)
        st.dataframe(
            vinc[["tipo", "flag_pt", "destino", "n_operaciones", "monto_MM"]].rename(columns={
                "tipo":          "Tipo",
                "flag_pt":       "Flag PT",
                "destino":       "Segmento",
                "n_operaciones": "Operaciones",
                "monto_MM":      "Monto MM ARS",
            }),
            hide_index=True,
            use_container_width=True,
            column_config={
                "Tipo":         st.column_config.TextColumn(width="small"),
                "Flag PT":      st.column_config.TextColumn(width="medium"),
                "Segmento":     st.column_config.TextColumn(width="small"),
                "Operaciones":  st.column_config.NumberColumn(format="%d"),
                "Monto MM ARS": st.column_config.NumberColumn(format="$ %.1f"),
            },
        )


@_fragment
def tab_impacto_reglas(out: dict) -> None:
    st.subheader("Impacto de reglas CM en el ledger")
    ledger_cm  = out["cm"]["ledger"]
    regla_ids  = sorted(x for x in ledger_cm.regla_id.dropna().unique())
    default_ix = regla_ids.index("ING-001A") if "ING-001A" in regla_ids else 0
    sel_rule   = st.selectbox("Regla", regla_ids, index=default_ix)
    impacted   = ledger_cm[ledger_cm.regla_id == sel_rule].copy()

    if impacted.empty:
        st.warning("La regla seleccionada no impactó ninguna fila en la corrida actual.")
        return

    m1, m2, m3 = st.columns(3)
    m1.metric("Movimientos origen", f"{impacted.id_origen.nunique():,}")
    m2.metric("Filas ledger",        f"{len(impacted):,}")
    m3.metric("Monto asignado",      f"${impacted.monto_asignado.sum() / 1e6:,.1f} MM ARS")

    st.markdown("#### Por destino")
    by_dest = (
        impacted
        .groupby(["tipo", "destino"], dropna=False)
        .agg(movimientos=("id_origen", "nunique"), monto=("monto_asignado", "sum"))
        .reset_index()
    )
    st.dataframe(
        pd.DataFrame({
            "Tipo":         by_dest["tipo"],
            "Destino":      by_dest["destino"],
            "Movimientos":  by_dest["movimientos"],
            "Monto MM ARS": (by_dest["monto"] / 1e6).round(2),
        }),
        hide_index=True,
        use_container_width=True,
        column_config={
            "Tipo":         st.column_config.TextColumn(width="small"),
            "Destino":      st.column_config.TextColumn(width="medium"),
            "Movimientos":  st.column_config.NumberColumn(format="%d"),
            "Monto MM ARS": st.column_config.NumberColumn(format="$ %.2f"),
        },
    )

    st.markdown("#### Detalle (primeras 500 filas)")
    detail = impacted[[
        "ledger_id", "tipo", "id_origen", "fecha", "descripcion",
        "monto_origen", "destino", "porcentaje", "monto_asignado",
    ]].head(500).copy()
    st.dataframe(
        pd.DataFrame({
            "ID":          detail["ledger_id"],
            "Tipo":        detail["tipo"],
            "Origen":      detail["id_origen"],
            "Fecha":       detail["fecha"],
            "Descripción": detail["descripcion"],
            "Monto orig.": detail["monto_origen"].round(0),
            "Destino":     detail["destino"],
            "% asig.":     detail["porcentaje"].round(3),
            "Monto asig.": detail["monto_asignado"].round(0),
        }),
        hide_index=True,
        use_container_width=True,
        height=380,
        column_config={
            "ID":          st.column_config.NumberColumn(format="%d",    width="small"),
            "Tipo":        st.column_config.TextColumn(width="small"),
            "Origen":      st.column_config.TextColumn(width="small"),
            "Fecha":       st.column_config.TextColumn(width="small"),
            "Descripción": st.column_config.TextColumn(width="large"),
            "Monto orig.": st.column_config.NumberColumn(format="$ %.0f"),
            "Destino":     st.column_config.TextColumn(width="medium"),
            "% asig.":     st.column_config.NumberColumn(format="%.3f",  width="small"),
            "Monto asig.": st.column_config.NumberColumn(format="$ %.0f"),
        },
    )


@_fragment
def tab_ledger(out: dict) -> None:
    st.subheader("Ledger trazable")
    motor_sel = st.radio("Motor", ["CM", "PT"], horizontal=True)
    ledger    = out["cm"]["ledger"] if motor_sel == "CM" else out["pt"]["ledger"]

    col1, col2, col3 = st.columns(3)
    f_destino = col1.multiselect("Destino / Jurisdicción", sorted(x for x in ledger.destino.dropna().unique()))
    f_regla   = col2.multiselect("Regla",                  sorted(x for x in ledger.regla_id.dropna().unique()))
    f_tipo    = col3.multiselect("Tipo",                   sorted(x for x in ledger.tipo.dropna().unique()))

    col4, col5 = st.columns(2)
    f_vinc    = col4.toggle("Solo partes vinculadas", value=False)
    f_no_comp = col5.toggle("Incluir no computables", value=True)

    filtered = ledger.copy()
    if f_destino: filtered = filtered[filtered.destino.isin(f_destino)]
    if f_regla:   filtered = filtered[filtered.regla_id.isin(f_regla)]
    if f_tipo:    filtered = filtered[filtered.tipo.isin(f_tipo)]
    if f_vinc and "vinculada" in filtered.columns:
        filtered = filtered[filtered.vinculada == True]
    if not f_no_comp and "computable" in filtered.columns:
        filtered = filtered[filtered.computable == True]

    st.caption(f"{len(filtered):,} filas — mostrando primeras 2.000.")
    _cols = [
        "ledger_id", "tipo", "id_origen", "fecha", "cuenta", "descripcion",
        "monto_origen", "regla_id", "metodo", "driver_nombre",
        "destino", "porcentaje", "monto_asignado", "vinculada", "flag_pt",
    ]
    st.dataframe(
        filtered[[c for c in _cols if c in filtered.columns]].head(2_000),
        hide_index=True,
        use_container_width=True,
        height=540,
        column_config={
            "ledger_id":      st.column_config.NumberColumn("ID",           format="%d",    width="small"),
            "tipo":           st.column_config.TextColumn("Tipo",                           width="small"),
            "id_origen":      st.column_config.TextColumn("Origen",                         width="small"),
            "fecha":          st.column_config.TextColumn("Fecha",                          width="small"),
            "cuenta":         st.column_config.TextColumn("Cuenta",                         width="small"),
            "descripcion":    st.column_config.TextColumn("Descripción",                    width="large"),
            "monto_origen":   st.column_config.NumberColumn("Monto origen",  format="$ %.0f"),
            "regla_id":       st.column_config.TextColumn("Regla",                          width="small"),
            "metodo":         st.column_config.TextColumn("Método",                         width="medium"),
            "driver_nombre":  st.column_config.TextColumn("Driver",                         width="medium"),
            "destino":        st.column_config.TextColumn("Destino",                        width="medium"),
            "porcentaje":     st.column_config.NumberColumn("% asig.",       format="%.3f", width="small"),
            "monto_asignado": st.column_config.NumberColumn("Monto asig.",   format="$ %.0f"),
            "vinculada":      st.column_config.CheckboxColumn("Vinc.",                      width="small"),
            "flag_pt":        st.column_config.TextColumn("Flag PT",                        width="medium"),
        },
    )


@_fragment
def tab_fuentes(out: dict) -> None:
    st.subheader("Fuentes cargadas")
    fuentes = out["cm"]["fuentes"]
    st.dataframe(
        pd.DataFrame([
            {"Fuente": name, "Filas": len(df), "Columnas": len(df.columns)}
            for name, df in fuentes.items()
        ]),
        hide_index=True,
        use_container_width=True,
        column_config={
            "Fuente":   st.column_config.TextColumn(width="medium"),
            "Filas":    st.column_config.NumberColumn(format="%d"),
            "Columnas": st.column_config.NumberColumn(format="%d", width="small"),
        },
    )
    st.divider()
    sel_source = st.selectbox("Vista previa de fuente", sorted(fuentes))
    st.dataframe(fuentes[sel_source].head(200), hide_index=True, use_container_width=True)


# ── página ─────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Automotriz — Motor DDJJ",
    page_icon="🚗",
    layout="wide",
)

st.title("🚗 Automotriz Argentina S.A. — Motor de reglas para DDJJ")
st.caption("App local: editá reglas, recalculá y exportá información trazable para CM y PT.")

with st.sidebar:
    st.header("Corrida")
    if st.button("↺  Recalcular", type="primary", use_container_width=True):
        run_motores.clear()
        st.toast("Recálculo iniciado.", icon="✅")
        st.rerun()
    st.divider()
    st.caption(f"📋 Reglas: `{REGLAS_CM.name}`")
    st.caption(f"📁 Datos: `{DATA_DIR.name}/`")

with st.spinner("Calculando motores…"):
    out = current_outputs()

tabs = st.tabs([
    "📊 Resumen",
    "🗺️ Convenio Multilateral",
    "🚙 Precios de Transferencia",
    "📋 Reglas CM",
    "🔍 Impacto de reglas",
    "📂 Ledger",
    "⬇️ Exportar DDJJ",
    "🗂️ Fuentes",
])


# ── TAB 0 — RESUMEN (sin widgets → no necesita fragment) ──────────────────────

with tabs[0]:
    ventas       = out["cm"]["fuentes"]["ventas"]
    gastos       = out["cm"]["fuentes"]["gastos"]
    ledger_total = len(out["cm"]["ledger"]) + len(out["pt"]["ledger"])

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Facturas",     f"{len(ventas):,}")
    c2.metric("Asientos",     f"{len(gastos):,}")
    c3.metric("Ventas",       f"${ventas.monto_total.sum() / 1e9:,.1f} MM")
    c4.metric("Gastos",       f"${gastos.monto.sum() / 1e9:,.1f} MM")
    c5.metric("Ledger total", f"{ledger_total:,} filas")

    st.divider()
    st.subheader("Controles de integridad")
    coef_sum = out["cm"]["coeficientes"].coef_unificado.sum()
    checks = [
        ("CM ledger",     f"{len(out['cm']['ledger']):,} filas",  "✅" if len(out["cm"]["ledger"]) > 0 else "⚠️"),
        ("PT ledger",     f"{len(out['pt']['ledger']):,} filas",  "✅" if len(out["pt"]["ledger"]) > 0 else "⚠️"),
        ("Ventas origen", f"{len(ventas):,} facturas",            "✅"),
        ("Gastos origen", f"{len(gastos):,} asientos",            "✅"),
        ("Suma coef. CM", f"{coef_sum:.6f}",                      "✅" if abs(coef_sum - 1.0) < 0.0001 else "⚠️"),
    ]
    st.dataframe(
        pd.DataFrame(checks, columns=["Control", "Valor", "Estado"]),
        hide_index=True,
        use_container_width=True,
        column_config={
            "Control": st.column_config.TextColumn(width="medium"),
            "Valor":   st.column_config.TextColumn(width="medium"),
            "Estado":  st.column_config.TextColumn(width="small"),
        },
    )


# ── TABs 1-2: exploración → fragment ──────────────────────────────────────────

with tabs[1]:
    tab_convenio_multilateral(out)

with tabs[2]:
    tab_precios_transferencia(out)


# ── TAB 3 — REGLAS CM (save → full rerun, no fragment) ────────────────────────

with tabs[3]:
    st.subheader("Editor de reglas CM")
    st.info(
        "Hacé **doble clic** en cualquier celda para editar. "
        "`Condiciones YAML` y `Parámetros YAML` aceptan YAML tipo diccionario. "
        "Al guardar se crea un backup automático con timestamp."
    )
    reglas   = load_rules(REGLAS_CM)
    rules_df = rules_to_table(reglas)
    edited   = st.data_editor(
        rules_df,
        num_rows="dynamic",
        use_container_width=True,
        height=560,
        column_config={
            "bloque":               st.column_config.SelectboxColumn("Bloque",        options=["ingresos", "gastos"], required=True, width="small"),
            "orden":                st.column_config.NumberColumn("Orden",             step=1, required=True, width="small"),
            "id":                   st.column_config.TextColumn("ID",                  width="small"),
            "descripcion":          st.column_config.TextColumn("Descripción",         width="large"),
            "metodo":               st.column_config.TextColumn("Método",              width="medium"),
            "condiciones_yaml":     st.column_config.TextColumn("Condiciones YAML",    width="medium"),
            "parametros_yaml":      st.column_config.TextColumn("Parámetros YAML",     width="medium"),
            "sustento_normativo":   st.column_config.TextColumn("Sustento",            width="medium"),
            "responsable_decision": st.column_config.TextColumn("Responsable",         width="small"),
            "notas_año":            st.column_config.TextColumn("Notas del año",       width="large"),
        },
        key="rules_editor",
    )

    col_save, col_val = st.columns([1, 3])
    with col_save:
        if st.button("💾 Guardar reglas CM", type="primary", use_container_width=True):
            try:
                backup = save_rules_from_table(edited)
                run_motores.clear()
                st.success(f"Guardado. Backup: `{backup.name}`")
                st.rerun()
            except Exception as exc:
                st.error(f"No se pudo guardar: {exc}")
    with col_val:
        try:
            table_to_rules(edited)
            st.success("✅ Formato válido — podés guardar.")
        except Exception as exc:
            st.error(f"⚠️ Error de formato: {exc}")


# ── TABs 4-5: exploración → fragment ──────────────────────────────────────────

with tabs[4]:
    tab_impacto_reglas(out)

with tabs[5]:
    tab_ledger(out)


# ── TAB 6 — EXPORTAR DDJJ (download button, no fragment) ──────────────────────

with tabs[6]:
    st.subheader("Exportar información para DDJJ")
    st.info(
        "El Excel incluye 5 solapas: coeficientes CM, P&L PT, "
        "operaciones vinculadas, Ledger CM y Ledger PT (máx. 100k filas c/u)."
    )
    col_dl, col_csv = st.columns(2)
    with col_dl:
        excel_bytes = build_excel_export(out)
        st.download_button(
            "⬇️  Descargar Excel",
            data=excel_bytes,
            file_name=f"salida_ddjj_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
        )
    with col_csv:
        if st.button("💾  Guardar CSVs en salida/", use_container_width=True):
            write_outputs(out)
            st.success(f"Archivos guardados en `{SALIDA}`")


# ── TAB 7 — FUENTES → fragment ────────────────────────────────────────────────

with tabs[7]:
    tab_fuentes(out)
