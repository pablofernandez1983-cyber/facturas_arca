"""
DDJJ de IVA (Ana Wainstein / Sucesión Fernández) desde la app — pestaña IVA de AlquiBot.

El trámite es una fila de `ddjj_iva` por contribuyente+período que avanza por estados:

    nuevo/error ─preparar→ preparando → revisar ─finalizar→ finalizando → listo_presentar
        ─presentar→ presentando → presentada            (verificar: dudoso tras apretar Presentar)

Cada acción dispara el workflow ddjj.yml del repo privado RUNNER_REPO
(scripts/emitir_hechizo.py tiene el mismo esquema). El robot reporta por /runner/ddjj/*.
Las acciones irreversibles solo se permiten desde el estado anterior y le pasan al robot los
montos que Pablo vio en el celu: si ARCA muestra otra cosa, el robot frena sin tocar nada.
"""

import os
import re
import hmac
import json
import calendar
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, Response

SUPABASE_URL  = os.environ["SUPABASE_URL"]
SUPABASE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_ANON_KEY"]
GITHUB_PAT    = os.environ["GITHUB_PAT"]
RUNNER_TOKEN  = os.environ.get("RUNNER_TOKEN", "")
RUNNER_REPO   = os.environ.get("RUNNER_REPO", "pablofernandez1983-cyber/arca-runner")
WORKFLOW_FILE = "ddjj.yml"
BUCKET        = "ddjj-iva"

TIPO_ALQUIBOT = {"ANA": "MAMA", "SUC": "PAPA"}   # tipo en la tabla facturas
EN_CURSO      = {"preparando", "finalizando", "presentando"}

router = APIRouter()


def _sb(extra=None):
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", **(extra or {})}


def _gh():
    return {"Authorization": f"Bearer {GITHUB_PAT}", "Accept": "application/vnd.github.v3+json"}


def _dt(s: str) -> datetime:
    """Timestamp de Postgres → datetime (tolera microsegundos con cualquier cantidad de dígitos)."""
    s = re.sub(r"\.(\d+)", lambda m: "." + m.group(1)[:6].ljust(6, "0"), s.replace("Z", "+00:00"))
    return datetime.fromisoformat(s)


def _validos(contrib, periodo) -> bool:
    return contrib in TIPO_ALQUIBOT and bool(re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", str(periodo or "")))


async def _job(c, contrib, periodo):
    r = await c.get(f"{SUPABASE_URL}/rest/v1/ddjj_iva?contrib=eq.{contrib}&periodo=eq.{periodo}&select=*",
                    headers=_sb())
    r.raise_for_status()
    filas = r.json()
    return filas[0] if filas else None


async def _job_por_id(c, job_id: int):
    r = await c.get(f"{SUPABASE_URL}/rest/v1/ddjj_iva?id=eq.{int(job_id)}&select=*", headers=_sb())
    r.raise_for_status()
    filas = r.json()
    return filas[0] if filas else None


async def _actualizar(c, job_id: int, campos: dict):
    campos = {**campos, "actualizado_at": datetime.now(timezone.utc).isoformat()}
    r = await c.patch(f"{SUPABASE_URL}/rest/v1/ddjj_iva?id=eq.{int(job_id)}", json=campos,
                      headers=_sb({"Content-Type": "application/json", "Prefer": "return=minimal"}))
    r.raise_for_status()


async def _runs(c) -> list:
    r = await c.get(f"https://api.github.com/repos/{RUNNER_REPO}/actions/workflows/{WORKFLOW_FILE}/runs?per_page=5",
                    headers=_gh())
    if not r.is_success:
        raise RuntimeError(f"GitHub {r.status_code}: {r.text[:200]}")
    return r.json().get("workflow_runs", [])


async def _alquibot(c, contrib, periodo) -> dict:
    """Neto facturado por AlquiBot ese mes: tendría que coincidir con el neto gravado del libro."""
    y, m = int(periodo[:4]), int(periodo[4:])
    ultimo = calendar.monthrange(y, m)[1]
    r = await c.get(f"{SUPABASE_URL}/rest/v1/facturas?select=precio,emitida&tipo=eq.{TIPO_ALQUIBOT[contrib]}"
                    f"&fecha_cbte=gte.{y}-{m:02d}-01&fecha_cbte=lte.{y}-{m:02d}-{ultimo:02d}", headers=_sb())
    if not r.is_success:
        return {}
    filas = r.json()
    emitidas = [f for f in filas if f.get("emitida")]
    return {"neto_emitido": round(sum(float(f["precio"] or 0) for f in emitidas), 2),
            "emitidas": len(emitidas), "pendientes": len(filas) - len(emitidas)}


async def _archivos(c, job_id) -> list:
    r = await c.post(f"{SUPABASE_URL}/storage/v1/object/list/{BUCKET}",
                     json={"prefix": f"{job_id}/", "limit": 50, "sortBy": {"column": "created_at", "order": "asc"}},
                     headers=_sb({"Content-Type": "application/json"}))
    if not r.is_success:
        return []
    return [f"{job_id}/{o['name']}" for o in r.json() if o.get("name")]


# ─────────────────────────────────────────────
# API para la app (PIN)
# ─────────────────────────────────────────────
@router.get("/api/ddjj")
async def ddjj_estado(contrib: str, periodo: str):
    if not _validos(contrib, periodo):
        return JSONResponse(status_code=400, content={"error": "contribuyente o período inválido"})
    async with httpx.AsyncClient(timeout=20) as c:
        job = await _job(c, contrib, periodo)
        run = None
        try:
            runs = await _runs(c)
            run = runs[0] if runs else None
            ocupado = any(x["status"] != "completed" for x in runs)
        except Exception:
            ocupado = None
        # Si el robot murió sin reportar, el trámite no puede quedar "en curso" para siempre
        quieto = job and _dt(job["actualizado_at"]) < datetime.now(timezone.utc) - timedelta(minutes=3)
        if job and job["estado"] in EN_CURSO and ocupado is False and quieto:
            nuevo = "verificar" if job["estado"] == "presentando" else "error"
            msg = ("El robot se cortó mientras presentaba: verificá en ARCA si la DJ quedó presentada."
                   if nuevo == "verificar" else "El robot se cortó sin terminar. Podés volver a intentarlo.")
            await _actualizar(c, job["id"], {"estado": nuevo, "mensaje": msg, "fase_en_curso": None})
            job = await _job_por_id(c, job["id"])
        return {
            "job": job,
            "archivos": await _archivos(c, job["id"]) if job else [],
            "alquibot": await _alquibot(c, contrib, periodo),
            "robot_ocupado": ocupado,
            "run": run and {"status": run["status"], "conclusion": run["conclusion"], "url": run["html_url"]},
        }


@router.post("/api/ddjj/accion")
async def ddjj_accion(request: Request):
    """Body: {contrib, periodo, accion: preparar|finalizar|presentar|verificado, ...}"""
    body = await request.json()
    contrib, periodo, accion = body.get("contrib"), str(body.get("periodo", "")), body.get("accion")
    if not _validos(contrib, periodo):
        return JSONResponse(status_code=400, content={"error": "contribuyente o período inválido"})

    async with httpx.AsyncClient(timeout=20) as c:
        job = await _job(c, contrib, periodo)
        estado = job["estado"] if job else "nuevo"

        if accion == "verificado":   # Pablo chequeó en ARCA qué pasó tras un "verificar"
            if estado != "verificar":
                return JSONResponse(status_code=409, content={"error": "No hay nada para verificar"})
            presentada = bool(body.get("presentada"))
            await _actualizar(c, job["id"], {
                "estado": "presentada" if presentada else "error",
                "mensaje": None if presentada else "Verificado: no se presentó. Podés volver a intentarlo."})
            return {"ok": True}

        try:
            runs = await _runs(c)
        except Exception as e:
            return JSONResponse(status_code=502, content={"error": str(e)})
        if any(x["status"] != "completed" for x in runs):
            return JSONResponse(status_code=409, content={"error": "El robot de IVA ya está trabajando. Esperá a que termine."})

        if accion == "preparar":
            if estado not in {"nuevo", "error", "revisar"}:
                return JSONResponse(status_code=409, content={"error": f"No se puede preparar en estado '{estado}'"})
            esperado, nuevo = {}, "preparando"
        elif accion == "finalizar":
            if estado != "revisar" or not job.get("monto_gravado"):
                return JSONResponse(status_code=409, content={"error": "Primero hay que revisar el libro"})
            esperado, nuevo = {"gravado": job["monto_gravado"], "nc": job["monto_nc"]}, "finalizando"
        elif accion == "presentar":
            if estado != "listo_presentar" or not job.get("dj_debito") or not job.get("dj_saldo"):
                return JSONResponse(status_code=409, content={"error": "La DJ todavía no está lista para presentar"})
            if not job.get("coincide") and not body.get("confirmo_diferencia"):
                return JSONResponse(status_code=409, content={"error": "La DJ no coincide con el libro: confirmá la diferencia"})
            esperado, nuevo = {"debito": job["dj_debito"], "saldo": job["dj_saldo"]}, "presentando"
        else:
            return JSONResponse(status_code=400, content={"error": "acción desconocida"})

        if not job:
            r = await c.post(f"{SUPABASE_URL}/rest/v1/ddjj_iva", json={"contrib": contrib, "periodo": periodo},
                             headers=_sb({"Content-Type": "application/json", "Prefer": "return=representation"}))
            r.raise_for_status()
            job = r.json()[0]
        await c.delete(f"{SUPABASE_URL}/rest/v1/ddjj_log?job_id=eq.{job['id']}", headers=_sb())
        await _actualizar(c, job["id"], {"estado": nuevo, "fase_en_curso": accion, "mensaje": None})

        r = await c.post(f"https://api.github.com/repos/{RUNNER_REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches",
                         headers=_gh(),
                         json={"ref": "main", "inputs": {"fase": accion, "contrib": contrib, "periodo": periodo,
                                                         "job_id": str(job["id"]), "esperado": json.dumps(esperado)}})
        if r.status_code != 204:
            await _actualizar(c, job["id"], {"estado": estado, "fase_en_curso": None,
                                             "mensaje": f"No se pudo arrancar el robot: GitHub {r.status_code}"})
            return JSONResponse(status_code=502, content={"error": f"GitHub {r.status_code}: {r.text[:200]}"})
    return {"ok": True, "job_id": job["id"]}


@router.get("/api/ddjj/progreso")
async def ddjj_progreso(job_id: int):
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/ddjj_log?job_id=eq.{int(job_id)}&select=mensaje,creado_at&order=id.asc&limit=300",
                        headers=_sb())
    return r.json() if r.is_success else []


@router.get("/api/ddjj/archivo")
async def ddjj_archivo(path: str):
    if not re.fullmatch(r"\d+/[\w.\-]+\.(png|pdf)", path):
        return JSONResponse(status_code=400, content={"error": "ruta inválida"})
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{path}", headers=_sb())
    if not r.is_success:
        return JSONResponse(status_code=404, content={"error": "no encontrado"})
    tipo = "application/pdf" if path.endswith(".pdf") else "image/png"
    return Response(content=r.content, media_type=tipo, headers={"Cache-Control": "private, max-age=3600"})


# ─────────────────────────────────────────────
# Runner (token propio)
# ─────────────────────────────────────────────
def _runner_ok(request: Request) -> bool:
    tok = request.headers.get("X-Runner-Token", "")
    return bool(RUNNER_TOKEN) and hmac.compare_digest(tok.encode(), RUNNER_TOKEN.encode())


CAMPOS_RUNNER = {"estado", "monto_gravado", "monto_nc", "libro_debito", "libro_credito", "dj_debito",
                 "dj_credito", "dj_saldo", "comparacion", "coincide", "vep", "mensaje"}


@router.post("/runner/ddjj/log")
async def runner_ddjj_log(request: Request):
    if not _runner_ok(request):
        return JSONResponse(status_code=401, content={"error": "token"})
    body = await request.json()
    async with httpx.AsyncClient(timeout=15) as c:
        await c.post(f"{SUPABASE_URL}/rest/v1/ddjj_log",
                     json={"job_id": int(body["job_id"]), "mensaje": str(body.get("mensaje", ""))[:500]},
                     headers=_sb({"Content-Type": "application/json", "Prefer": "return=minimal"}))
    return {"ok": True}


@router.post("/runner/ddjj/actualizar")
async def runner_ddjj_actualizar(request: Request):
    if not _runner_ok(request):
        return JSONResponse(status_code=401, content={"error": "token"})
    body = await request.json()
    campos = {k: v for k, v in body.items() if k in CAMPOS_RUNNER}
    if "estado" in campos and campos["estado"] not in EN_CURSO:
        campos["fase_en_curso"] = None
    async with httpx.AsyncClient(timeout=15) as c:
        await _actualizar(c, int(body["job_id"]), campos)
    return {"ok": True}


@router.post("/runner/ddjj/archivo")
async def runner_ddjj_archivo(request: Request, job_id: int = Form(...), nombre: str = Form(...),
                              archivo: UploadFile = File(...)):
    if not _runner_ok(request):
        return JSONResponse(status_code=401, content={"error": "token"})
    if not re.fullmatch(r"[\w.\-]+\.(png|pdf)", nombre):
        return JSONResponse(status_code=400, content={"error": "nombre inválido"})
    tipo = "application/pdf" if nombre.endswith(".pdf") else "image/png"
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{int(job_id)}/{nombre}",
                         content=await archivo.read(),
                         headers=_sb({"Content-Type": tipo, "x-upsert": "true"}))
    if not r.is_success:
        return JSONResponse(status_code=502, content={"error": r.text[:200]})
    return {"ok": True}
