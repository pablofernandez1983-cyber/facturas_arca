"""
Envía por email los PDFs generados en /tmp/pdfs/, con un detalle formal
de las facturas emitidas (razón social, CUIT/documento, importe y fecha).
Se ejecuta al final del workflow de GitHub Actions.

Env vars requeridas:
    GMAIL_USER, GMAIL_APP_PASSWORD
    EMAIL_DEST  (opcional, default: pablofernandez1983@gmail.com)
"""

import os
import ssl
import json
import glob
import smtplib
from datetime import datetime
from email.message import EmailMessage

PDF_DIR       = "/tmp/pdfs"
EMITIDAS_JSON = "/tmp/facturas_emitidas.json"
GMAIL_USER    = os.environ.get("GMAIL_USER", "")
GMAIL_PASS    = os.environ.get("GMAIL_APP_PASSWORD", "")
EMAIL_DEST    = os.environ.get("EMAIL_DEST", "pablofernandez1983@gmail.com")

if not GMAIL_USER or not GMAIL_PASS:
    print("⚠️  Sin credenciales Gmail — no se envía email.")
    raise SystemExit(0)

pdfs = sorted(glob.glob(os.path.join(PDF_DIR, "*.pdf")))
if not pdfs:
    print("ℹ️  No hay PDFs en /tmp/pdfs — no se envía email.")
    raise SystemExit(0)

facturas = []
if os.path.exists(EMITIDAS_JSON):
    try:
        with open(EMITIDAS_JSON, "r", encoding="utf-8") as f:
            facturas = json.load(f)
    except Exception:
        facturas = []


def fmt_moneda(valor) -> str:
    try:
        return f"${float(str(valor).replace(',', '.')):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(valor)


def fmt_fecha(valor: str) -> str:
    if not valor:
        return ""
    try:
        return datetime.strptime(valor, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return valor


fecha_hoy = datetime.now().strftime("%d/%m/%Y")
total = sum(float(str(x.get("precio") or 0).replace(",", ".")) for x in facturas) if facturas else 0.0

subject = f"Facturas emitidas {fecha_hoy} ({len(pdfs)} comprobante{'s' if len(pdfs) != 1 else ''})"

# ── Texto plano ──────────────────────────────────────────────────────────
if facturas:
    lineas_txt = [
        f"  • {f['razon_social']}  |  CUIT/Doc {f['doc_receptor']}  |  "
        f"{fmt_moneda(f['precio'])}  |  {fmt_fecha(f['fecha_cbte'])}"
        for f in facturas
    ]
    cuerpo_txt = (
        f"Estimado/a,\n\n"
        f"Se informa que con fecha {fecha_hoy} se emitieron a través de ARCA "
        f"los siguientes comprobantes:\n\n"
        + "\n".join(lineas_txt)
        + f"\n\nTotal: {fmt_moneda(total)}\n\n"
        f"Se adjuntan los comprobantes en PDF correspondientes a cada factura detallada.\n\n"
        f"Saludos cordiales."
    )
else:
    cuerpo_txt = (
        f"Se adjuntan {len(pdfs)} comprobante(s) emitido(s) el {fecha_hoy} desde ARCA.\n\n"
        + "\n".join(f"  • {os.path.basename(p)}" for p in pdfs)
    )

# ── HTML ─────────────────────────────────────────────────────────────────
if facturas:
    filas_html = "".join(
        f"<tr>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb'>{f['razon_social']}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb'>{f['doc_receptor']}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:right'>{fmt_moneda(f['precio'])}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb'>{fmt_fecha(f['fecha_cbte'])}</td>"
        f"</tr>"
        for f in facturas
    )
    cuerpo_html = f"""
    <div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#111827;font-size:14px">
      <p>Estimado/a,</p>
      <p>Se informa que con fecha <strong>{fecha_hoy}</strong> se emitieron a través de ARCA
      los siguientes comprobantes:</p>
      <table style="border-collapse:collapse;width:100%;margin:14px 0">
        <thead>
          <tr style="background:#f0f2f5">
            <th style="padding:8px 10px;text-align:left">Razón Social</th>
            <th style="padding:8px 10px;text-align:left">CUIT / Documento</th>
            <th style="padding:8px 10px;text-align:right">Importe</th>
            <th style="padding:8px 10px;text-align:left">Fecha</th>
          </tr>
        </thead>
        <tbody>{filas_html}</tbody>
        <tfoot>
          <tr>
            <td colspan="2" style="padding:8px 10px;font-weight:700">Total</td>
            <td style="padding:8px 10px;font-weight:700;text-align:right">{fmt_moneda(total)}</td>
            <td></td>
          </tr>
        </tfoot>
      </table>
      <p>Se adjuntan los comprobantes en PDF correspondientes a cada factura detallada.</p>
      <p>Saludos cordiales.</p>
    </div>
    """
else:
    lista_html = "".join(f"<li>{os.path.basename(p)}</li>" for p in pdfs)
    cuerpo_html = f"""
    <div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#111827;font-size:14px">
      <p>Se adjuntan {len(pdfs)} comprobante(s) emitido(s) el {fecha_hoy} desde ARCA.</p>
      <ul>{lista_html}</ul>
    </div>
    """

msg = EmailMessage()
msg["Subject"] = subject
msg["From"]    = GMAIL_USER
msg["To"]      = EMAIL_DEST
msg.set_content(cuerpo_txt)
msg.add_alternative(cuerpo_html, subtype="html")

for pdf_path in pdfs:
    with open(pdf_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="pdf",
            filename=os.path.basename(pdf_path),
        )

ctx = ssl.create_default_context()
with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as smtp:
    smtp.login(GMAIL_USER, GMAIL_PASS)
    smtp.send_message(msg)

print(f"✅ Email enviado a {EMAIL_DEST} con {len(pdfs)} PDF(s) y detalle de {len(facturas)} factura(s).")
