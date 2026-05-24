from __future__ import annotations

from datetime import datetime
from pathlib import Path
import html

from motor import cm_motor, pt_motor


BASE = Path(__file__).parent
OUT = BASE / "salida"
OUT.mkdir(exist_ok=True)


def table(df, index=False):
    return df.to_html(index=index, classes="data", border=0, escape=True)


def main() -> None:
    out_cm = cm_motor.run_cm(BASE / "data", BASE / "reglas" / "cm_reglas_2025.yaml")
    out_pt = pt_motor.run_pt(BASE / "data", BASE / "reglas" / "pt_reglas_2025.yaml")

    cm_ledger = out_cm["ledger"]
    pt_ledger = out_pt["ledger"]
    cm_ledger.to_csv(OUT / "ledger_cm.csv", index=False, encoding="utf-8-sig")
    pt_ledger.to_csv(OUT / "ledger_pt.csv", index=False, encoding="utf-8-sig")
    out_cm["coeficientes"].to_csv(OUT / "coeficientes_cm.csv", encoding="utf-8-sig")
    out_pt["pnl_resumen"].to_csv(OUT / "pnl_pt.csv", encoding="utf-8-sig")
    out_pt["vinculadas"].to_csv(OUT / "operaciones_vinculadas_pt.csv", index=False, encoding="utf-8-sig")

    ventas = out_pt["fuentes"]["ventas"]
    gastos = out_pt["fuentes"]["gastos"]
    clientes = out_pt["fuentes"]["clientes"]
    proveedores = out_pt["fuentes"]["proveedores"]
    empleados = out_pt["fuentes"]["empleados"]

    coef_show = out_cm["coeficientes"].copy().head(15).assign(
        ingresos=lambda d: (d.ingresos / 1e6).round(0),
        gastos=lambda d: (d.gastos / 1e6).round(0),
        coef_ingresos=lambda d: (d.coef_ingresos * 100).round(3),
        coef_gastos=lambda d: (d.coef_gastos * 100).round(3),
        coef_unificado=lambda d: (d.coef_unificado * 100).round(3),
    ).rename(columns={
        "ingresos": "Ingresos MM",
        "gastos": "Gastos MM",
        "coef_ingresos": "% Ing",
        "coef_gastos": "% Gtos",
        "coef_unificado": "Coef CM %",
    })

    pnl = out_pt["pnl_resumen"].copy().assign(
        Ingresos=lambda d: (d.Ingresos / 1e6).round(0),
        Gastos=lambda d: (d.Gastos / 1e6).round(0),
        Resultado=lambda d: (d.Resultado / 1e6).round(0),
    )

    vinc = out_pt["vinculadas"].copy()
    if not vinc.empty:
        vinc["Monto MM"] = (vinc["monto_total"] / 1e6).round(1)
        vinc = vinc[["tipo", "flag_pt", "destino", "n_operaciones", "Monto MM"]]

    no_comp = cm_ledger[~cm_ledger.computable].groupby("regla_desc")["monto_origen"].agg(["count", "sum"]).reset_index()
    no_comp["Monto MM"] = (no_comp["sum"] / 1e6).round(0)
    no_comp = no_comp.rename(columns={"regla_desc": "Regla", "count": "Filas"})[["Regla", "Filas", "Monto MM"]]

    cm_sample_cols = [
        "ledger_id", "tipo", "id_origen", "cuenta", "descripcion", "monto_origen",
        "regla_id", "metodo", "driver_nombre", "destino", "porcentaje",
        "monto_asignado", "vinculada",
    ]
    pt_sample_cols = cm_sample_cols + ["flag_pt"]
    cm_sample = cm_ledger[[c for c in cm_sample_cols if c in cm_ledger.columns]].head(300)
    pt_sample = pt_ledger[[c for c in pt_sample_cols if c in pt_ledger.columns]].head(300)

    cards = [
        ("Facturas", f"{len(ventas):,}"),
        ("Asientos", f"{len(gastos):,}"),
        ("Empleados", f"{len(empleados):,}"),
        ("Clientes", f"{len(clientes):,}"),
        ("Proveedores", f"{len(proveedores):,}"),
        ("Ventas ARS", f"${ventas.monto_total.sum()/1e9:,.1f} MM"),
        ("Gastos ARS", f"${gastos.monto.sum()/1e9:,.1f} MM"),
        ("Ledger total", f"{len(cm_ledger)+len(pt_ledger):,}"),
    ]
    card_html = "".join(
        f'<div class="card"><span>{html.escape(k)}</span><strong>{html.escape(v)}</strong></div>'
        for k, v in cards
    )

    html_doc = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Automotriz Argentina - Datos calculados</title>
<style>
:root {{ --bg:#f6f7f9; --ink:#20242a; --muted:#667085; --line:#d8dde5; --blue:#1f5fbf; --panel:#ffffff; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:Segoe UI, Arial, sans-serif; background:var(--bg); color:var(--ink); }}
header {{ background:#12372a; color:white; padding:28px 32px; }}
h1 {{ margin:0 0 8px; font-size:28px; letter-spacing:0; }}
header p {{ margin:0; color:#d7eadf; }}
main {{ padding:24px 32px 48px; max-width:1400px; margin:auto; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:0 0 22px; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px 16px; }}
.card span {{ display:block; color:var(--muted); font-size:13px; }}
.card strong {{ display:block; margin-top:6px; font-size:22px; }}
section {{ margin-top:24px; }}
h2 {{ font-size:20px; margin:0 0 12px; }}
.panel {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; overflow:auto; }}
.grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
.data {{ border-collapse:collapse; width:100%; font-size:13px; }}
.data th {{ background:#eef3f0; color:#17231d; text-align:left; position:sticky; top:0; }}
.data th, .data td {{ border-bottom:1px solid #e6e9ee; padding:8px 10px; vertical-align:top; white-space:nowrap; }}
.data td:nth-child(5) {{ white-space:normal; min-width:220px; }}
a {{ color:var(--blue); font-weight:600; }}
.links {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:10px; }}
.links a {{ background:white; border:1px solid var(--line); border-radius:6px; padding:8px 10px; text-decoration:none; }}
.note {{ color:var(--muted); margin:6px 0 0; font-size:13px; }}
@media (max-width: 900px) {{ main {{ padding:18px; }} .grid {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<header>
  <h1>Automotriz Argentina S.A. - Datos calculados</h1>
  <p>Reporte generado {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} sin servidor ni procesos de fondo.</p>
</header>
<main>
  <div class="cards">{card_html}</div>
  <section>
    <h2>Archivos completos</h2>
    <div class="links">
      <a href="salida/ledger_cm.csv">Ledger CM completo</a>
      <a href="salida/ledger_pt.csv">Ledger PT completo</a>
      <a href="salida/coeficientes_cm.csv">Coeficientes CM</a>
      <a href="salida/pnl_pt.csv">P&amp;L PT</a>
      <a href="salida/operaciones_vinculadas_pt.csv">Vinculadas PT</a>
    </div>
    <p class="note">Los CSV se abren en Excel. La vista de abajo muestra resumen y primeras 300 filas de cada ledger.</p>
  </section>
  <section class="grid">
    <div><h2>Convenio Multilateral - Top jurisdicciones</h2><div class="panel">{table(coef_show, index=True)}</div></div>
    <div><h2>Precios de Transferencia - P&amp;L por segmento</h2><div class="panel">{table(pnl, index=True)}</div></div>
  </section>
  <section class="grid">
    <div><h2>CM - Gastos no computables</h2><div class="panel">{table(no_comp)}</div></div>
    <div><h2>PT - Operaciones vinculadas</h2><div class="panel">{table(vinc)}</div></div>
  </section>
  <section><h2>Ledger CM - primeras 300 filas</h2><div class="panel">{table(cm_sample)}</div></section>
  <section><h2>Ledger PT - primeras 300 filas</h2><div class="panel">{table(pt_sample)}</div></section>
</main>
</body>
</html>"""

    report = BASE / "reporte_datos.html"
    report.write_text(html_doc, encoding="utf-8")
    print(report.resolve())


if __name__ == "__main__":
    main()
