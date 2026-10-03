"""
Web app para Railway.
Lee facturas de Supabase, dispara GitHub Actions.

Env vars en Railway:
    SUPABASE_URL
    SUPABASE_SERVICE_KEY   (service_role: la tabla facturas tiene RLS y no es accesible con la anon)
    GITHUB_PAT             (token con scope 'workflow')
    GITHUB_REPO            pablofernandez1983-cyber/facturas_arca
    APP_PIN                (obligatorio: todas las rutas /api/* exigen el header X-App-Pin)
"""

import os
import re
import hmac
import asyncio
import calendar
import subprocess
import tempfile
from datetime import date

NOMBRES_MES_ES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
                  "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
import httpx
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

SUPABASE_URL      = os.environ["SUPABASE_URL"]
SUPABASE_KEY      = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_ANON_KEY"]
GITHUB_PAT        = os.environ["GITHUB_PAT"]
GITHUB_REPO       = os.environ.get("GITHUB_REPO", "pablofernandez1983-cyber/facturas_arca")
APP_PIN           = os.environ.get("APP_PIN", "")
WORKFLOW_FILE     = "emitir.yml"

app = FastAPI()

STATIC = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _pin_ok(pin):
    return bool(APP_PIN) and hmac.compare_digest(str(pin or "").encode(), APP_PIN.encode())


@app.middleware("http")
async def exigir_pin(request: Request, call_next):
    """Todo /api/* exige el PIN en el header X-App-Pin (menos /api/login).
    Antes el PIN solo se pedía en la pantalla: el servidor no lo verificaba."""
    path = request.url.path
    if path.startswith("/api/") and path != "/api/login" and request.method != "OPTIONS":
        if not APP_PIN:
            return JSONResponse(status_code=503, content={"error": "APP_PIN no configurado"})
        if not _pin_ok(request.headers.get("X-App-Pin")):
            await asyncio.sleep(0.5)
            return JSONResponse(status_code=401, content={"error": "PIN incorrecto"})
    return await call_next(request)


def _sb_headers(extra=None):
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", **(extra or {})}


@app.get("/")
def root():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.post("/api/convert-docx")
async def convert_docx(file: UploadFile = File(...)):
    """Convierte un .docx a PDF usando LibreOffice headless."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Guardar el archivo recibido
        safe_name = "documento.docx"
        docx_path = os.path.join(tmpdir, safe_name)
        with open(docx_path, "wb") as f:
            f.write(await file.read())

        # Convertir con LibreOffice
        result = subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", tmpdir, docx_path],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            return JSONResponse(status_code=500, content={"error": result.stderr or "Conversión fallida"})

        pdf_path = os.path.join(tmpdir, "documento.pdf")
        if not os.path.exists(pdf_path):
            return JSONResponse(status_code=500, content={"error": "No se generó el PDF"})

        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

    return Response(content=pdf_bytes, media_type="application/pdf")


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    if _pin_ok(body.get("pin")):
        return {"ok": True}
    await asyncio.sleep(0.5)
    return JSONResponse(status_code=401, content={"error": "Contraseña incorrecta"})


_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@app.get("/api/facturas/meses")
async def facturas_meses():
    """Fechas de comprobante de todas las facturas (para armar los combos de año/mes)."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/facturas?select=fecha_cbte&order=fecha_cbte.asc",
            headers=_sb_headers(),
        )
    if not resp.is_success:
        return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
    return resp.json()


@app.get("/api/facturas")
async def facturas_del_mes(desde: str, hasta: str):
    if not (_FECHA.match(desde) and _FECHA.match(hasta)):
        return JSONResponse(status_code=400, content={"error": "fechas inválidas"})
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/facturas?select=*&fecha_cbte=gte.{desde}&fecha_cbte=lte.{hasta}"
            f"&order=tipo.asc,fecha_cbte.asc",
            headers=_sb_headers(),
        )
    if not resp.is_success:
        return JSONResponse(status_code=resp.status_code, content={"error": resp.text})
    return resp.json()


@app.post("/api/emitir")
async def emitir(request: Request):
    """
    Body: { "ids": [1, 2, 3], "tipo": "AMBOS" | "MAMA" | "PAPA" }
    Dispara el workflow de GitHub Actions.
    """
    body = await request.json()
    ids  = body.get("ids", [])
    tipo = body.get("tipo", "AMBOS")

    ids_str = ",".join(str(i) for i in ids) if ids else ""

    headers = {
        "Authorization": f"Bearer {GITHUB_PAT}",
        "Accept":        "application/vnd.github.v3+json",
    }
    payload = {
        "ref": "main",
        "inputs": {
            "tipo": tipo,
            "ids":  ids_str,
        },
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches",
            json=payload,
            headers=headers,
        )

    if resp.status_code == 204:
        return {"ok": True}
    return JSONResponse(
        status_code=resp.status_code,
        content={"error": resp.text},
    )


CAMPOS_EDITABLES = {"doc_receptor", "detalle", "precio", "fecha_cbte", "desde", "hasta", "vto_pago"}

@app.patch("/api/factura/{factura_id}")
async def update_factura(factura_id: int, request: Request):
    body = await request.json()
    update_data = {k: v for k, v in body.items() if k in CAMPOS_EDITABLES}
    if not update_data:
        return JSONResponse(status_code=400, content={"error": "Sin campos válidos"})

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.patch(
            f"{SUPABASE_URL}/rest/v1/facturas?id=eq.{factura_id}",
            json=update_data,
            headers=_sb_headers({"Content-Type": "application/json", "Prefer": "return=minimal"}),
        )
    if resp.is_success:
        return {"ok": True}
    return JSONResponse(status_code=resp.status_code, content={"error": resp.text})


@app.get("/api/workflow/progreso")
async def workflow_progreso():
    """Devuelve los mensajes de progreso del último run (tabla workflow_log en Supabase)."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/workflow_log?select=mensaje,creado_at&order=creado_at.asc&limit=100",
            headers=_sb_headers(),
        )
    if not resp.is_success:
        return []
    return resp.json()


@app.get("/api/workflow/estado")
async def workflow_estado():
    """Devuelve el último run del workflow."""
    headers = {
        "Authorization": f"Bearer {GITHUB_PAT}",
        "Accept":        "application/vnd.github.v3+json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILE}/runs?per_page=1",
            headers=headers,
        )
    if not resp.is_success:
        return JSONResponse(status_code=resp.status_code, content={"error": resp.text})

    runs = resp.json().get("workflow_runs", [])
    if not runs:
        return {"status": "none"}

    run = runs[0]
    return {
        "status":      run["status"],        # queued | in_progress | completed
        "conclusion":  run["conclusion"],    # success | failure | None
        "url":         run["html_url"],
        "started_at":  run["run_started_at"],
        "run_id":      run["id"],
    }


@app.post("/api/copiar-mes")
async def copiar_mes(request: Request):
    """
    Copia todas las facturas de mes_origen a mes_destino
    actualizando fecha_cbte, desde, hasta, vto_pago al nuevo mes.
    Body: { "mes_origen": "2026-04", "mes_destino": "2026-05" }  (ambos opcionales)
    """
    body = await request.json()

    hoy = date.today()
    mes_destino = body.get("mes_destino") or hoy.strftime("%Y-%m")
    anio_d, mes_d = int(mes_destino.split("-")[0]), int(mes_destino.split("-")[1])

    # mes_origen = mes anterior al destino
    if body.get("mes_origen"):
        mes_origen = body["mes_origen"]
    else:
        primer_dia_destino = date(anio_d, mes_d, 1)
        if mes_d == 1:
            mes_origen = f"{anio_d - 1}-12"
        else:
            mes_origen = f"{anio_d}-{mes_d - 1:02d}"

    ultimo_dia = calendar.monthrange(anio_d, mes_d)[1]
    primer_dia_str = f"{anio_d}-{mes_d:02d}-01"
    ultimo_dia_str = f"{anio_d}-{mes_d:02d}-{ultimo_dia:02d}"

    headers = _sb_headers({"Content-Type": "application/json"})

    async with httpx.AsyncClient(timeout=15) as client:
        # Borrar facturas NO emitidas del mes destino (las emitidas no se tocan)
        await client.delete(
            f"{SUPABASE_URL}/rest/v1/facturas"
            f"?fecha_cbte=gte.{primer_dia_str}&fecha_cbte=lte.{ultimo_dia_str}&emitida=eq.false",
            headers=headers,
        )

        # Traer facturas del mes origen
        anio_o, mes_o = int(mes_origen.split("-")[0]), int(mes_origen.split("-")[1])
        ultimo_o = calendar.monthrange(anio_o, mes_o)[1]
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/facturas"
            f"?fecha_cbte=gte.{anio_o}-{mes_o:02d}-01"
            f"&fecha_cbte=lte.{anio_o}-{mes_o:02d}-{ultimo_o:02d}"
            f"&select=*",
            headers=headers,
        )
        if not resp.is_success:
            return JSONResponse(status_code=resp.status_code, content={"error": resp.text})

        origen = resp.json()
        if not origen:
            return JSONResponse(status_code=404, content={
                "error": f"No hay facturas en {mes_origen} para copiar."
            })

        # Construir nuevas filas
        EXCLUIR = {"id", "emitida", "emitida_at", "idx_excel"}
        nuevas = []
        for f in origen:
            nueva = {k: v for k, v in f.items() if k not in EXCLUIR}
            nueva["fecha_cbte"] = primer_dia_str
            nueva["desde"]      = primer_dia_str
            nueva["hasta"]      = ultimo_dia_str
            nueva["vto_pago"]   = ultimo_dia_str
            nueva["emitida"]    = False
            nueva["emitida_at"] = None
            nueva["detalle"]    = f"Alquiler {NOMBRES_MES_ES[mes_d - 1]} {anio_d}"
            nuevas.append(nueva)

        ins = await client.post(
            f"{SUPABASE_URL}/rest/v1/facturas",
            json=nuevas,
            headers={**headers, "Prefer": "return=minimal"},
        )
        if not ins.is_success:
            return JSONResponse(status_code=ins.status_code, content={"error": ins.text})

    return {"ok": True, "copiadas": len(nuevas), "mes_origen": mes_origen, "mes_destino": mes_destino}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
