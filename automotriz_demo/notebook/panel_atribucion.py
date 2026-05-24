import marimo

__generated_with = "0.23.8"
app = marimo.App(width="full")


@app.cell
def _():
    import marimo as mo
    import pandas as pd
    import altair as alt
    import yaml
    import sys
    from pathlib import Path

    BASE = Path(__file__).parent.parent
    sys.path.insert(0, str(BASE))
    from motor import cm_motor, pt_motor

    return BASE, alt, cm_motor, mo, pd, pt_motor, yaml


@app.cell
def _(mo):
    mo.md(
        """
        # Automotriz Argentina S.A. - Panel de Atribución 2025

        Demo de motor de reglas para:
        - **Convenio Multilateral**: coeficientes por jurisdicción.
        - **Precios de Transferencia / Segmentación**: P&L por línea de negocio.

        Mismo dataset crudo, dos lógicas distintas, trazabilidad total vía ledger.
        """
    )
    return


@app.cell
def _(mo):
    seccion = mo.ui.tabs(
        {
            "Dashboard general": mo.md(""),
            "Convenio Multilateral": mo.md(""),
            "Precios de Transferencia": mo.md(""),
            "Explorador de ledger": mo.md(""),
            "Editor de reglas": mo.md(""),
            "Reglas activas": mo.md(""),
        },
        value="Dashboard general",
    )
    seccion
    return (seccion,)


@app.cell
def _(mo):
    recalcular = mo.ui.run_button(label="Recalcular datos", kind="success")
    recalcular
    return (recalcular,)


@app.cell
def _(BASE, cm_motor, pt_motor, recalcular):
    _ = recalcular.value
    out_cm = cm_motor.run_cm(BASE / "data", BASE / "reglas" / "cm_reglas_2025.yaml")
    out_pt = pt_motor.run_pt(BASE / "data", BASE / "reglas" / "pt_reglas_2025.yaml")
    return out_cm, out_pt


@app.cell
def _(mo, out_cm, out_pt, seccion):
    if seccion.value == "Dashboard general":
        ventas = out_pt["fuentes"]["ventas"]
        gastos = out_pt["fuentes"]["gastos"]
        empleados = out_pt["fuentes"]["empleados"]
        clientes = out_pt["fuentes"]["clientes"]
        proveedores = out_pt["fuentes"]["proveedores"]
        ledger_total = len(out_cm["ledger"]) + len(out_pt["ledger"])

        kpis = mo.hstack(
            [
                mo.stat(f"{len(ventas):,}", label="Facturas", caption="Ingresos 2025"),
                mo.stat(f"{len(gastos):,}", label="Asientos", caption="Gastos 2025"),
                mo.stat(f"{len(empleados):,}", label="Empleados"),
                mo.stat(
                    f"{len(clientes):,}",
                    label="Clientes",
                    caption=f"{(clientes.tipo == 'exportacion').sum()} exportación",
                ),
                mo.stat(
                    f"{len(proveedores):,}",
                    label="Proveedores",
                    caption=f"{proveedores.es_parte_relacionada.sum()} vinculados",
                ),
                mo.stat(f"${ventas.monto_total.sum() / 1e9:.1f}MM", label="Ventas", caption="ARS"),
                mo.stat(f"${gastos.monto.sum() / 1e9:.1f}MM", label="Gastos", caption="ARS"),
                mo.stat(f"{ledger_total:,}", label="Ledger total", caption="filas"),
            ]
        )

        texto = mo.md(
            f"""
            ## Visión general

            Las {len(ventas):,} ventas y {len(gastos):,} gastos generaron un
            **ledger total de {ledger_total:,} filas** entre ambos motores.

            Navegá las pestañas para revisar coeficientes CM, P&L de PT,
            operaciones vinculadas, reglas activas y el ledger granular filtrable.
            """
        )
        bloque_general = mo.vstack([kpis, texto])
    else:
        bloque_general = mo.md("")
    bloque_general
    return


@app.cell
def _(mo, seccion):
    if seccion.value == "Convenio Multilateral":
        top_n = mo.ui.slider(5, 24, value=10, label="Cantidad de jurisdicciones")
        mostrar_no_comp = mo.ui.switch(value=False, label="Mostrar gastos no computables")
        controles_cm = mo.hstack([top_n, mostrar_no_comp])
    else:
        top_n, mostrar_no_comp, controles_cm = None, None, mo.md("")
    controles_cm
    return mostrar_no_comp, top_n


@app.cell
def _(alt, mo, mostrar_no_comp, out_cm, seccion, top_n):
    if seccion.value == "Convenio Multilateral":
        coef = out_cm["coeficientes"].copy()
        coef_show = coef.head(top_n.value)
        tabla_coef = mo.ui.table(
            coef_show.assign(
                coef_ingresos=lambda d: (d.coef_ingresos * 100).round(3),
                coef_gastos=lambda d: (d.coef_gastos * 100).round(3),
                coef_unificado=lambda d: (d.coef_unificado * 100).round(3),
                ingresos=lambda d: (d.ingresos / 1e6).round(0),
                gastos=lambda d: (d.gastos / 1e6).round(0),
            ).rename(
                columns={
                    "ingresos": "Ingresos (MM)",
                    "gastos": "Gastos (MM)",
                    "coef_ingresos": "% Ing",
                    "coef_gastos": "% Gtos",
                    "coef_unificado": "Coef CM %",
                }
            ),
            label=f"Coeficientes CM - Top {top_n.value}",
            page_size=25,
        )

        coef_chart = coef_show.reset_index().assign(coef_pct=lambda d: d.coef_unificado * 100)
        chart = (
            alt.Chart(coef_chart)
            .mark_bar()
            .encode(
                x=alt.X("coef_pct:Q", title="Coeficiente CM (%)"),
                y=alt.Y("destino:N", sort="-x", title=None),
                tooltip=["destino", alt.Tooltip("coef_pct:Q", format=".3f")],
                color=alt.value("#2563eb"),
            )
            .properties(height=400)
        )

        no_comp = out_cm["ledger"][~out_cm["ledger"].computable]
        sumario_nc = no_comp.groupby("regla_desc")["monto_origen"].agg(["count", "sum"]).reset_index()
        sumario_nc.columns = ["Regla", "Asientos", "Monto excluido"]
        sumario_nc["Monto excluido (MM)"] = (sumario_nc["Monto excluido"] / 1e6).round(0)

        bloque_cm = mo.vstack(
            [
                mo.md("### Coeficientes por jurisdicción"),
                mo.hstack([tabla_coef, mo.ui.altair_chart(chart)]),
                mo.md("### Gastos no computables") if mostrar_no_comp.value else mo.md(""),
                mo.ui.table(sumario_nc[["Regla", "Asientos", "Monto excluido (MM)"]])
                if mostrar_no_comp.value
                else mo.md(""),
            ]
        )
    else:
        bloque_cm = mo.md("")
    bloque_cm
    return


@app.cell
def _(alt, mo, out_pt, seccion):
    if seccion.value == "Precios de Transferencia":
        _resumen_pt = out_pt["pnl_resumen"].copy()
        resumen_mm = _resumen_pt.assign(
            Ingresos=lambda d: (d.Ingresos / 1e6).round(0),
            Gastos=lambda d: (d.Gastos / 1e6).round(0),
            Resultado=lambda d: (d.Resultado / 1e6).round(0),
        )

        chart_df = _resumen_pt.reset_index().melt(
            id_vars="destino",
            value_vars=["Ingresos", "Gastos"],
            var_name="Concepto",
            value_name="Monto",
        )
        chart_df["Monto_MM"] = chart_df.Monto / 1e6
        pnl_chart = (
            alt.Chart(chart_df)
            .mark_bar()
            .encode(
                x=alt.X("destino:N", title="Segmento", sort=["AUT", "PKP", "UTI", "MOT"]),
                y=alt.Y("Monto_MM:Q", title="Monto (MM ARS)"),
                color=alt.Color("Concepto:N", scale=alt.Scale(range=["#16a34a", "#dc2626"])),
                tooltip=["destino", "Concepto", alt.Tooltip("Monto_MM:Q", format=",.0f")],
            )
            .properties(height=350, width=500)
        )

        vinc = out_pt["vinculadas"].copy()
        if not vinc.empty:
            vinc["Monto (MM)"] = (vinc.monto_total / 1e6).round(1)
            vinc_table = mo.ui.table(
                vinc[["tipo", "flag_pt", "destino", "n_operaciones", "Monto (MM)"]],
                label="Operaciones con partes vinculadas",
                page_size=20,
            )
        else:
            vinc_table = mo.md("Sin operaciones vinculadas.")

        bloque_pt = mo.vstack(
            [
                mo.md("### P&L resumido por segmento (MM ARS)"),
                mo.hstack([mo.ui.table(resumen_mm), mo.ui.altair_chart(pnl_chart)]),
                mo.md("### Operaciones con partes vinculadas"),
                vinc_table,
            ]
        )
    else:
        bloque_pt = mo.md("")
    bloque_pt
    return


@app.cell
def _(mo, seccion):
    if seccion.value == "Explorador de ledger":
        motor_sel = mo.ui.dropdown(
            options={"Convenio Multilateral": "cm", "Precios de Transferencia": "pt"},
            value="Convenio Multilateral",
            label="Qué ledger explorar",
        )
        controles_l = motor_sel
    else:
        motor_sel, controles_l = None, mo.md("")
    controles_l
    return (motor_sel,)


@app.cell
def _(mo, motor_sel, out_cm, out_pt, seccion):
    if seccion.value == "Explorador de ledger" and motor_sel is not None:
        _ledger_filtros = out_cm["ledger"] if motor_sel.value == "cm" else out_pt["ledger"]
        destinos = sorted([d for d in _ledger_filtros.destino.dropna().unique()])
        reglas = sorted(_ledger_filtros.regla_id.dropna().unique())
        tipos = sorted(_ledger_filtros.tipo.dropna().unique())

        f_destino = mo.ui.multiselect(destinos, value=[], label="Destino")
        f_regla = mo.ui.multiselect(reglas, value=[], label="Regla")
        f_tipo = mo.ui.multiselect(tipos, value=[], label="Tipo")
        f_vinculadas = mo.ui.switch(value=False, label="Solo partes vinculadas")
        f_no_comp = mo.ui.switch(value=False, label="Incluir no computables")

        filtros = mo.vstack(
            [
                mo.md("**Filtros del ledger**"),
                mo.hstack([f_destino, f_regla, f_tipo]),
                mo.hstack([f_vinculadas, f_no_comp]),
            ]
        )
    else:
        f_destino, f_regla, f_tipo, f_vinculadas, f_no_comp = (None,) * 5
        filtros = mo.md("")
    filtros
    return f_destino, f_no_comp, f_regla, f_tipo, f_vinculadas


@app.cell
def _(f_destino, f_no_comp, f_regla, f_tipo, f_vinculadas, mo, motor_sel, out_cm, out_pt, seccion):
    if seccion.value == "Explorador de ledger" and motor_sel is not None:
        _ledger = out_cm["ledger"] if motor_sel.value == "cm" else out_pt["ledger"]
        df = _ledger.copy()
        if not f_no_comp.value:
            df = df[df.computable]
        if f_destino.value:
            df = df[df.destino.isin(f_destino.value)]
        if f_regla.value:
            df = df[df.regla_id.isin(f_regla.value)]
        if f_tipo.value:
            df = df[df.tipo.isin(f_tipo.value)]
        if f_vinculadas.value:
            df = df[df.vinculada]

        cols = [
            "ledger_id",
            "tipo",
            "id_origen",
            "cuenta",
            "descripcion",
            "monto_origen",
            "regla_id",
            "metodo",
            "driver_nombre",
            "destino",
            "porcentaje",
            "monto_asignado",
            "vinculada",
            "flag_pt",
        ]
        cols = [c for c in cols if c in df.columns]
        _resumen_ledger = mo.md(
            f"""
            **Resultados:** {len(df):,} filas, mostrando primeras 500.
            Suma monto asignado: **${df.monto_asignado.sum() / 1e6:,.0f} MM ARS**.
            """
        )
        bloque_l = mo.vstack([_resumen_ledger, mo.ui.table(df[cols].head(500), page_size=25)])
    else:
        bloque_l = mo.md("")
    bloque_l
    return


@app.cell
def _(BASE, mo, pd, seccion, yaml):
    if seccion.value == "Editor de reglas":
        _reglas_path = BASE / "reglas" / "cm_reglas_2025.yaml"
        _reglas_cm = yaml.safe_load(_reglas_path.read_text(encoding="utf-8"))
        _rows_editor = []
        for _bloque in ("ingresos", "gastos"):
            for _orden, _regla in enumerate(_reglas_cm[_bloque]):
                _extra = {
                    _k: _v
                    for _k, _v in _regla.items()
                    if _k
                    not in {
                        "id",
                        "descripcion",
                        "aplica_cuando",
                        "metodo",
                        "sustento_normativo",
                        "responsable_decision",
                        "notas_año",
                        "notas_aÃ±o",
                    }
                }
                _rows_editor.append(
                    {
                        "bloque": _bloque,
                        "orden": _orden,
                        "id": _regla.get("id"),
                        "descripcion": _regla.get("descripcion", ""),
                        "metodo": _regla.get("metodo", ""),
                        "condiciones": yaml.safe_dump(
                            _regla.get("aplica_cuando", {}),
                            allow_unicode=True,
                            sort_keys=False,
                            default_flow_style=False,
                        ).strip(),
                        "parametros": yaml.safe_dump(
                            _extra,
                            allow_unicode=True,
                            sort_keys=False,
                            default_flow_style=False,
                        ).strip(),
                        "responsable": _regla.get("responsable_decision", ""),
                        "notas": _regla.get("notas_año", _regla.get("notas_aÃ±o", "")),
                    }
                )
        reglas_df = pd.DataFrame(_rows_editor)
        tabla_reglas = mo.ui.table(
            reglas_df,
            selection="single",
            initial_selection=[1] if len(reglas_df) > 1 else [0],
            page_size=8,
            label="Reglas CM",
            wrapped_columns=["descripcion", "condiciones", "parametros", "notas"],
            max_height=360,
        )
        bloque_editor_lista = mo.vstack(
            [
                mo.md("### Editor visual de reglas CM"),
                mo.md(
                    "Seleccioná una regla para ver impacto, editar campos y guardar cambios en el YAML."
                ),
                tabla_reglas,
            ]
        )
    else:
        reglas_df = pd.DataFrame()
        tabla_reglas = None
        bloque_editor_lista = mo.md("")
    bloque_editor_lista
    return reglas_df, tabla_reglas


@app.cell
def _(mo, out_cm, reglas_df, seccion, tabla_reglas, yaml):
    if seccion.value == "Editor de reglas" and tabla_reglas is not None and not reglas_df.empty:
        _seleccion = tabla_reglas.value
        if hasattr(_seleccion, "empty"):
            _regla_sel = (
                _seleccion.iloc[0].to_dict()
                if not _seleccion.empty
                else reglas_df.iloc[0].to_dict()
            )
        elif _seleccion:
            _regla_sel = _seleccion[0]
        else:
            _regla_sel = reglas_df.iloc[0].to_dict()

        regla_id_editor = _regla_sel["id"]
        bloque_editor = _regla_sel["bloque"]
        orden_editor = int(_regla_sel["orden"])
        desc_editor = mo.ui.text_area(
            value=_regla_sel["descripcion"],
            label="Descripción",
            rows=3,
            full_width=True,
        )
        metodo_editor = mo.ui.text(
            value=_regla_sel["metodo"],
            label="Método",
            full_width=True,
        )
        condiciones_editor = mo.ui.text_area(
            value=_regla_sel["condiciones"],
            label="Condiciones YAML",
            rows=5,
            full_width=True,
        )
        parametros_editor = mo.ui.text_area(
            value=_regla_sel["parametros"],
            label="Parámetros YAML",
            rows=5,
            full_width=True,
        )
        responsable_editor = mo.ui.text(
            value=_regla_sel["responsable"],
            label="Responsable",
            full_width=True,
        )
        notas_editor = mo.ui.text_area(
            value=_regla_sel["notas"],
            label="Notas del año",
            rows=4,
            full_width=True,
        )
        guardar_regla = mo.ui.run_button(label="Guardar regla en YAML", kind="warn")

        _ledger = out_cm["ledger"]
        _impacto = _ledger[_ledger.regla_id == regla_id_editor]
        if _impacto.empty:
            _impacto_md = mo.md("**Impacto actual:** sin filas en el ledger actual.")
        else:
            _por_destino = (
                _impacto.groupby(["tipo", "destino"], dropna=False)
                .agg(filas=("id_origen", "nunique"), monto=("monto_asignado", "sum"))
                .reset_index()
            )
            _por_destino["monto_MM"] = (_por_destino["monto"] / 1e6).round(1)
            _impacto_md = mo.vstack(
                [
                    mo.md(
                        f"**Impacto actual:** {_impacto.id_origen.nunique():,} movimientos, "
                        f"${_impacto.monto_asignado.sum() / 1e6:,.1f} MM ARS asignados."
                    ),
                    mo.ui.table(
                        _por_destino[["tipo", "destino", "filas", "monto_MM"]],
                        page_size=10,
                        label="Impacto por destino",
                    ),
                ]
            )

        bloque_editor_detalle = mo.vstack(
            [
                mo.md(f"### Regla seleccionada: `{regla_id_editor}`"),
                _impacto_md,
                mo.hstack([desc_editor, metodo_editor]),
                mo.hstack([condiciones_editor, parametros_editor]),
                mo.hstack([responsable_editor, notas_editor]),
                guardar_regla,
            ]
        )
    else:
        regla_id_editor = None
        bloque_editor = None
        orden_editor = None
        desc_editor = None
        metodo_editor = None
        condiciones_editor = None
        parametros_editor = None
        responsable_editor = None
        notas_editor = None
        guardar_regla = None
        bloque_editor_detalle = mo.md("")
    bloque_editor_detalle
    return (
        bloque_editor,
        condiciones_editor,
        desc_editor,
        guardar_regla,
        metodo_editor,
        notas_editor,
        orden_editor,
        parametros_editor,
        regla_id_editor,
        responsable_editor,
    )


@app.cell
def _(
    BASE,
    bloque_editor,
    condiciones_editor,
    desc_editor,
    guardar_regla,
    metodo_editor,
    mo,
    notas_editor,
    orden_editor,
    parametros_editor,
    regla_id_editor,
    responsable_editor,
    seccion,
    yaml,
):
    if (
        seccion.value == "Editor de reglas"
        and guardar_regla is not None
        and guardar_regla.value
    ):
        _mensaje_guardado = None
        try:
            _condiciones = yaml.safe_load(condiciones_editor.value) or {}
            _parametros = yaml.safe_load(parametros_editor.value) or {}
            if not isinstance(_condiciones, dict):
                raise ValueError("Condiciones YAML debe ser un diccionario.")
            if not isinstance(_parametros, dict):
                raise ValueError("Parámetros YAML debe ser un diccionario.")

            _reglas_path = BASE / "reglas" / "cm_reglas_2025.yaml"
            _backup_path = BASE / "reglas" / "cm_reglas_2025.yaml.bak"
            _backup_path.write_text(_reglas_path.read_text(encoding="utf-8"), encoding="utf-8")
            _reglas = yaml.safe_load(_reglas_path.read_text(encoding="utf-8"))
            _regla = _reglas[bloque_editor][orden_editor]
            if _regla.get("id") != regla_id_editor:
                raise ValueError("La regla seleccionada cambió de posición; recargá la página.")

            _nueva = {
                "id": regla_id_editor,
                "descripcion": desc_editor.value,
                "aplica_cuando": _condiciones,
                "metodo": metodo_editor.value,
            }
            _nueva.update(_parametros)
            if responsable_editor.value:
                _nueva["responsable_decision"] = responsable_editor.value
            if notas_editor.value:
                _nueva["notas_año"] = notas_editor.value

            _reglas[bloque_editor][orden_editor] = _nueva
            _reglas_path.write_text(
                yaml.safe_dump(_reglas, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            _mensaje_guardado = mo.md(
                f"**Guardado:** `{regla_id_editor}` fue actualizada. "
                "Usá **Recalcular datos** para ver el impacto."
            )
        except Exception as _exc:
            _mensaje_guardado = mo.md(f"**No se pudo guardar:** `{_exc}`")
    else:
        _mensaje_guardado = mo.md("")
    _mensaje_guardado
    return


@app.cell
def _(BASE, mo, seccion):
    if seccion.value == "Reglas activas":
        cm_yaml = (BASE / "reglas" / "cm_reglas_2025.yaml").read_text(encoding="utf-8")
        pt_yaml = (BASE / "reglas" / "pt_reglas_2025.yaml").read_text(encoding="utf-8")
        bloque_r = mo.vstack(
            [
                mo.md("### Reglas del Convenio Multilateral"),
                mo.md(f"```yaml\n{cm_yaml}\n```"),
                mo.md("---"),
                mo.md("### Reglas de Precios de Transferencia"),
                mo.md(f"```yaml\n{pt_yaml}\n```"),
            ]
        )
    else:
        bloque_r = mo.md("")
    bloque_r
    return


if __name__ == "__main__":
    app.run()
