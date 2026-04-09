"""
Envía por email los PDFs generados en /tmp/pdfs/.
Se ejecuta al final del workflow de GitHub Actions.

Env vars requeridas:
    GMAIL_USER, GMAIL_APP_PASSWORD
    EMAIL_DEST  (opcional, default: pablofernandez1983@gmail.com)
"""

import os
import ssl
import smtplib
import glob
from datetime import datetime
from email.message import EmailMessage

PDF_DIR    = "/tmp/pdfs"
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_PASS = os.environ.get("GMAIL_APP_PASSWORD", "")
EMAIL_DEST = os.environ.get("EMAIL_DEST", "pablofernandez1983@gmail.com")

if not GMAIL_USER or not GMAIL_PASS:
    print("⚠️  Sin credenciales Gmail — no se envía email.")
    raise SystemExit(0)

pdfs = sorted(glob.glob(os.path.join(PDF_DIR, "*.pdf")))
if not pdfs:
    print("ℹ️  No hay PDFs en /tmp/pdfs — no se envía email.")
    raise SystemExit(0)

fecha = datetime.now().strftime("%d/%m/%Y")
msg = EmailMessage()
msg["Subject"] = f"Facturas emitidas {fecha} ({len(pdfs)} comprobante{'s' if len(pdfs) != 1 else ''})"
msg["From"]    = GMAIL_USER
msg["To"]      = EMAIL_DEST
msg.set_content(
    f"Se adjuntan {len(pdfs)} comprobante(s) emitido(s) el {fecha} desde ARCA.\n\n"
    + "\n".join(f"  • {os.path.basename(p)}" for p in pdfs)
)

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

print(f"✅ Email enviado a {EMAIL_DEST} con {len(pdfs)} PDF(s).")
