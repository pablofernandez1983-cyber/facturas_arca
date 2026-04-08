"""
Web app para Railway.
Lee facturas de Supabase, dispara GitHub Actions.

Env vars en Railway:
    SUPABASE_URL
    SUPABASE_ANON_KEY      (la publishable que ya tenés)
    GITHUB_PAT             (token con scope 'workflow')
    GITHUB_REPO            pablofernandez1983-cyber/facturas_arca
"""

import os
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

SUPABASE_URL      = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
GITHUB_PAT        = os.environ["GITHUB_PAT"]
GITHUB_REPO       = os.environ.get("GITHUB_REPO", "pablofernandez1983-cyber/facturas_arca")
APP_PIN           = os.environ.get("APP_PIN", "4850")
WORKFLOW_FILE     = "emitir.yml"

app = FastAPI()

STATIC = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def root():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    if body.get("pin") == APP_PIN:
        return {"ok": True}
    return JSONResponse(status_code=401, content={"error": "Contraseña incorrecta"})


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
            headers={
                "apikey":          SUPABASE_ANON_KEY,
                "Authorization":   f"Bearer {SUPABASE_ANON_KEY}",
                "Content-Type":    "application/json",
                "Prefer":          "return=minimal",
            },
        )
    if resp.is_success:
        return {"ok": True}
    return JSONResponse(status_code=resp.status_code, content={"error": resp.text})


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
