"""
HumbertoBot: alquiler de Humberto Primo 2031 7A (Pablo, monotributo, CUIT propio).

Dos partes:
  · Contrato: ajuste trimestral por IPC (INDEC, vía la API de series de datos.gob.ar). El ajuste
    del mes M usa la variación del índice entre M-4 y M-1 (verificado con el de julio 2026:
    junio / marzo × $1.300.000 = $1.387.943; el inquilino paga $1.388.000 redondeado). La cadena
    de valores es exacta: el redondeo que pague el inquilino no cambia la base del próximo ajuste.
  · Facturas: tabla `humberto_facturas` (un mes por fila). La emisión corre en el repo privado
    RUNNER_REPO (humberto.yml + scripts/emitir_humberto.py); antes de confirmar cada factura el
    robot sube la vista previa y espera el OK de la app (`esperando` / `aprobado`).
"""

import os
import re
import hmac
import time
import calendar
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, Response

SUPABASE_URL  = os.environ["SUPABASE_URL"]
SUPABASE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_ANON_KEY"]
GITHUB_PAT    = os.environ["GITHUB_PAT"]
RUNNER_TOKEN  = os.environ.get("RUNNER_TOKEN", "")
RUNNER_REPO   = os.environ.get("RUNNER_REPO", "pablofernandez1983-cyber/arca-runner")
WORKFLOW_FILE = "humberto.yml"
BUCKET        = "humberto"
AR            = ZoneInfo("America/Argentina/Buenos_Aires")

CONTRATO = {
    "inmueble":   "Humberto Primo 2031 7A",
    "inquilino":  "CAJAL JOSE MARCELO",
    "dni":        "22966835",
    "inicio":     "2026-04-01",
    "fin":        "2028-03-31",
    "valor_inicial": 1300000,
    "cada_meses": 3,
    "indice":     "IPC (INDEC) nivel general",
}
IPC_SERIE = "148.3_INIVELNAL_DICI_M_26"
MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
         "Septiembre", "Octubre", "Noviembre", "Diciembre"]
EN_CURSO = {"en_cola", "emitiendo", "esperando_ok", "confirmando"}

router = APIRouter()


def _sb(extra=None):
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", **(extra or {})}


def _gh():
    return {"Authorization": f"Bearer {GITHUB_PAT}", "Accept": "application/vnd.github.v3+json"}


def _hoy() -> date:
    return datetime.now(AR).date()


def _mas_meses(p: str, n: int) -> str:
    """'202607' + n meses → 'YYYYMM'."""
    y, m = int(p[:4]), int(p[4:]) - 1 + n
    return f"{y + m // 12}{m % 12 + 1:02d}"


def _mes_txt(p: str) -> str:
    return f"{MESES[int(p[4:]) - 1]} {p[:4]}"


def _dt(s: str) -> datetime:
    s = re.sub(r"\.(\d+)", lambda m: "." + m.group(1)[:6].ljust(6, "0"), s.replace("Z", "+00:00"))
    return datetime.fromisoformat(s)


# ─────────────────────────────────────────────
# IPC y cronograma del contrato
# ─────────────────────────────────────────────
_ipc_cache = {"t": 0.0, "datos": {}}


async def _ipc(c) -> dict:
    """{'YYYYMM': índice}. Cacheado 3 h (el INDEC publica una vez por mes).

    La API de series cachea por URL y algunas variantes devuelven datos viejos o meses en null
    (11/10/2026: con start_date=2025-01-01&limit=1000 faltaba agosto). Se piden varias y se unen:
    los valores no se contradicen, solo cambia qué meses trae cada una."""
    if _ipc_cache["datos"] and time.time() - _ipc_cache["t"] < 3 * 3600:
        return _ipc_cache["datos"]
    hoy = _hoy()
    variantes = [{"start_date": "2025-06-01"}, {"start_date": "2025-10-01"},
                 {"start_date": f"{hoy.year - 1}-{hoy.month:02d}-01"}, {"start_date": "2025-01-01", "limit": 999}]
    datos, errores = {}, []
    for v in variantes:
        try:
            r = await c.get("https://apis.datos.gob.ar/series/api/series/",
                            params={"ids": IPC_SERIE, "format": "json", **v}, timeout=20)
            r.raise_for_status()
            datos.update({f[0][:4] + f[0][5:7]: float(f[1]) for f in r.json()["data"] if f[1] is not None})
        except Exception as e:
            errores.append(str(e))
    if not datos:
        raise RuntimeError("; ".join(errores) or "sin datos")
    _ipc_cache.update(t=time.time(), datos=datos)
    return datos


def _cronograma(ipc: dict) -> list:
    """Ajustes del contrato con su estado: aplicado (IPC completo), parcial (falta algún mes) o futuro."""
    ini = CONTRATO["inicio"][:4] + CONTRATO["inicio"][5:7]
    fin = CONTRATO["fin"][:4] + CONTRATO["fin"][5:7]
    valor, ajustes = float(CONTRATO["valor_inicial"]), []
    p = _mas_meses(ini, CONTRATO["cada_meses"])
    while p <= fin:
        base, ultimo = _mas_meses(p, -4), _mas_meses(p, -1)
        meses = [_mas_meses(p, -3), _mas_meses(p, -2), ultimo]
        hay = [m for m in meses if m in ipc]
        a = {"periodo": p, "mes": _mes_txt(p), "meses_ipc": [_mes_txt(m) for m in meses],
             "valor_anterior": round(valor, 2) if valor else None}
        if base in ipc and ultimo in ipc and valor:
            f = ipc[ultimo] / ipc[base]
            valor = valor * f
            a.update(estado="aplicado", variacion=round((f - 1) * 100, 2), valor=round(valor, 2))
        elif base in ipc and hay and valor:
            f = ipc[hay[-1]] / ipc[base]
            falta = [m for m in meses if m not in ipc]
            a.update(estado="parcial", variacion_parcial=round((f - 1) * 100, 2),
                     valor_parcial=round(valor * f, 2),
                     falta=[_mes_txt(m) for m in falta],
                     sale=f"mediados de {MESES[int(_mas_meses(falta[0], 1)[4:]) - 1].lower()}")
            valor = None   # los ajustes siguientes no se pueden calcular todavía
        else:
            a.update(estado="futuro")
            valor = None
        ajustes.append(a)
        p = _mas_meses(p, CONTRATO["cada_meses"])
    return ajustes


def _valor_de(periodo: str, ajustes: list):
    """Valor contractual de un mes (None si depende de un IPC que todavía no salió)."""
    v = float(CONTRATO["valor_inicial"])
    for a in ajustes:
        if a["periodo"] > periodo:
            break
        v = a.get("valor") if a["estado"] == "aplicado" else None
    return round(v, 2) if v else None


# ─────────────────────────────────────────────
# Supabase
# ─────────────────────────────────────────────
async def _facturas(c) -> list:
    r = await c.get(f"{SUPABASE_URL}/rest/v1/humberto_facturas?select=*&order=periodo.asc", headers=_sb())
    r.raise_for_status()
    return r.json()


async def _factura(c, fid: int):
    r = await c.get(f"{SUPABASE_URL}/rest/v1/humberto_facturas?id=eq.{int(fid)}&select=*", headers=_sb())
    r.raise_for_status()
    filas = r.json()
    return filas[0] if filas else None


async def _actualizar(c, fid: int, campos: dict):
    campos = {**campos, "actualizado_at": datetime.now(timezone.utc).isoformat()}
    r = await c.patch(f"{SUPABASE_URL}/rest/v1/humberto_facturas?id=eq.{int(fid)}", json=campos,
                      headers=_sb({"Content-Type": "application/json", "Prefer": "return=minimal"}))
    r.raise_for_status()


async def _runs(c) -> list:
    r = await c.get(f"https://api.github.com/repos/{RUNNER_REPO}/actions/workflows/{WORKFLOW_FILE}/runs?per_page=3",
                    headers=_gh())
    if r.status_code == 404:   # workflow todavía no creado
        return []
    if not r.is_success:
        raise RuntimeError(f"GitHub {r.status_code}: {r.text[:200]}")
    return r.json().get("workflow_runs", [])


async def _archivos(c, fid) -> list:
    r = await c.post(f"{SUPABASE_URL}/storage/v1/object/list/{BUCKET}",
                     json={"prefix": f"{fid}/", "limit": 20, "sortBy": {"column": "created_at", "order": "asc"}},
                     headers=_sb({"Content-Type": "application/json"}))
    if not r.is_success:
        return []
    return [f"{fid}/{o['name']}" for o in r.json() if o.get("name")]


def _detalle(periodo: str) -> str:
    return f"Alquiler H. Primo 2031 7A - {_mes_txt(periodo)}"


# ─────────────────────────────────────────────
# API para la app (PIN)
# ─────────────────────────────────────────────
@router.get("/api/humberto")
async def humberto_estado():
    async with httpx.AsyncClient(timeout=20) as c:
        ipc_error = None
        try:
            ipc = await _ipc(c)
        except Exception as e:
            ipc, ipc_error = _ipc_cache["datos"], f"No pude leer el IPC: {e}"
        ajustes = _cronograma(ipc)
        facturas = {f["periodo"]: f for f in await _facturas(c)}

        try:
            runs = await _runs(c)
            run = runs[0] if runs else None
            vivo = any(x["status"] != "completed" for x in runs)
        except Exception:
            run, vivo = None, None
        # Si el robot murió sin reportar, ninguna factura puede quedar "en curso" para siempre
        if vivo is False:
            for f in facturas.values():
                if f["estado"] in EN_CURSO and _dt(f["actualizado_at"]) < datetime.now(timezone.utc) - timedelta(minutes=3):
                    nuevo = "verificar" if f["estado"] == "confirmando" else "pendiente"
                    msg = ("El robot se cortó justo al confirmar: fijate en ARCA si la factura se emitió."
                           if nuevo == "verificar" else "El robot se cortó antes de emitir. Podés volver a intentarlo.")
                    await _actualizar(c, f["id"], {"estado": nuevo, "mensaje": msg, "esperando": False})
                    f.update(estado=nuevo, mensaje=msg, esperando=False)

        hoy = _hoy()
        actual = f"{hoy.year}{hoy.month:02d}"
        ini = CONTRATO["inicio"][:4] + CONTRATO["inicio"][5:7]
        meses, p = [], ini
        while p <= actual:
            f = facturas.get(p)
            meses.append({"periodo": p, "mes": _mes_txt(p), "valor_contrato": _valor_de(p, ajustes),
                          "factura": f, "archivos": await _archivos(c, f["id"]) if f and f["estado"] in
                          ("emitida", "esperando_ok", "verificar") else []})
            p = _mas_meses(p, 1)

    vigente = None
    for a in ajustes:
        if a["periodo"] <= actual:
            vigente = a
    return {"contrato": CONTRATO, "ajustes": ajustes, "meses": meses, "actual": actual,
            "valor_vigente": _valor_de(actual, ajustes), "ajuste_vigente": vigente,
            "ipc_ultimo": _mes_txt(max(ipc)) if ipc else None, "ipc_error": ipc_error,
            "robot_ocupado": vivo,
            "run": run and {"status": run["status"], "conclusion": run["conclusion"], "url": run["html_url"]}}


@router.post("/api/humberto/monto")
async def humberto_monto(request: Request):
    """Fija el monto a facturar de un mes (lo que paga el inquilino; pesos enteros)."""
    body = await request.json()
    periodo = str(body.get("periodo", ""))
    if not re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", periodo):
        return JSONResponse(status_code=400, content={"error": "período inválido"})
    try:
        monto = int(round(float(body.get("monto"))))
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"error": "monto inválido"})
    if monto <= 0:
        return JSONResponse(status_code=400, content={"error": "monto inválido"})
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/humberto_facturas?periodo=eq.{periodo}&select=*", headers=_sb())
        r.raise_for_status()
        fila = r.json()[0] if r.json() else None
        if fila and fila["estado"] not in ("pendiente", "error"):
            return JSONResponse(status_code=409, content={"error": "Esa factura ya está emitida o en curso"})
        if fila:
            await _actualizar(c, fila["id"], {"monto": monto, "nota": body.get("nota")})
        else:
            r = await c.post(f"{SUPABASE_URL}/rest/v1/humberto_facturas",
                             json={"periodo": periodo, "monto": monto, "nota": body.get("nota")},
                             headers=_sb({"Content-Type": "application/json", "Prefer": "return=minimal"}))
            r.raise_for_status()
    return {"ok": True}


@router.post("/api/humberto/emitir")
async def humberto_emitir(request: Request):
    """Body: {periodos: ['202608', ...]}. Cada una se emite recién con el OK de la app."""
    body = await request.json()
    periodos = sorted({str(p) for p in body.get("periodos") or []})
    if not periodos:
        return JSONResponse(status_code=400, content={"error": "No elegiste ninguna factura"})
    actual = f"{_hoy().year}{_hoy().month:02d}"
    async with httpx.AsyncClient(timeout=20) as c:
        try:
            if any(x["status"] != "completed" for x in await _runs(c)):
                return JSONResponse(status_code=409, content={"error": "El robot ya está trabajando. Esperá a que termine."})
        except Exception as e:
            return JSONResponse(status_code=502, content={"error": str(e)})
        facturas = {f["periodo"]: f for f in await _facturas(c)}
        ids = []
        for p in periodos:
            f = facturas.get(p)
            if p > actual:
                return JSONResponse(status_code=409, content={"error": f"{_mes_txt(p)} todavía no empezó"})
            if not f or not f.get("monto"):
                return JSONResponse(status_code=409, content={"error": f"{_mes_txt(p)} no tiene monto"})
            if f["estado"] not in ("pendiente", "error"):
                return JSONResponse(status_code=409, content={"error": f"{_mes_txt(p)} está {f['estado']}"})
            ids.append(f["id"])
        await c.delete(f"{SUPABASE_URL}/rest/v1/humberto_log?id=gt.0", headers=_sb())
        for p in periodos:
            await _actualizar(c, facturas[p]["id"], {"estado": "en_cola", "detalle": _detalle(p), "mensaje": None,
                                                     "esperando": False, "aprobado": None})
        r = await c.post(f"https://api.github.com/repos/{RUNNER_REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches",
                         headers=_gh(), json={"ref": "main", "inputs": {"ids": ",".join(map(str, ids))}})
        if r.status_code != 204:
            for p in periodos:
                await _actualizar(c, facturas[p]["id"], {"estado": "pendiente",
                                                         "mensaje": f"No se pudo arrancar el robot: GitHub {r.status_code}"})
            return JSONResponse(status_code=502, content={"error": f"GitHub {r.status_code}: {r.text[:200]}"})
    return {"ok": True}


@router.post("/api/humberto/ok")
async def humberto_ok(request: Request):
    """Body: {id, emitir: true|false}. Respuesta a la vista previa que mostró el robot."""
    body = await request.json()
    async with httpx.AsyncClient(timeout=20) as c:
        f = await _factura(c, int(body.get("id") or 0))
        if not f or f["estado"] != "esperando_ok" or not f["esperando"]:
            return JSONResponse(status_code=409, content={"error": "El robot no está esperando esa factura"})
        await _actualizar(c, f["id"], {"aprobado": "emitir" if body.get("emitir") else "saltear", "esperando": False})
    return {"ok": True}


@router.post("/api/humberto/verificado")
async def humberto_verificado(request: Request):
    """Body: {id, emitida: bool}. Pablo chequeó en ARCA una factura en 'verificar'."""
    body = await request.json()
    async with httpx.AsyncClient(timeout=20) as c:
        f = await _factura(c, int(body.get("id") or 0))
        if not f or f["estado"] != "verificar":
            return JSONResponse(status_code=409, content={"error": "No hay nada para verificar"})
        emitida = bool(body.get("emitida"))
        await _actualizar(c, f["id"], {"estado": "emitida" if emitida else "pendiente",
                                       "mensaje": "Marcada como emitida a mano (verificada en ARCA)." if emitida else None,
                                       "fecha_emision": _hoy().isoformat() if emitida else None})
    return {"ok": True}


@router.get("/api/humberto/progreso")
async def humberto_progreso():
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/humberto_log?select=mensaje,creado_at&order=id.asc&limit=300",
                        headers=_sb())
    return r.json() if r.is_success else []


@router.get("/api/humberto/archivo")
async def humberto_archivo(path: str):
    if not re.fullmatch(r"\d+/[\w.\-]+\.(png|pdf)", path):
        return JSONResponse(status_code=400, content={"error": "ruta inválida"})
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{path}", headers=_sb())
    if not r.is_success:
        return JSONResponse(status_code=404, content={"error": "no encontrado"})
    tipo = "application/pdf" if path.endswith(".pdf") else "image/png"
    return Response(content=r.content, media_type=tipo, headers={"Cache-Control": "private, max-age=600"})


# ─────────────────────────────────────────────
# Runner (token propio)
# ─────────────────────────────────────────────
def _runner_ok(request: Request) -> bool:
    tok = request.headers.get("X-Runner-Token", "")
    return bool(RUNNER_TOKEN) and hmac.compare_digest(tok.encode(), RUNNER_TOKEN.encode())


CAMPOS_RUNNER = {"estado", "nro", "cae", "fecha_emision", "esperando", "aprobado", "mensaje"}


@router.post("/runner/humberto/facturas")
async def runner_facturas(request: Request):
    """Las facturas a emitir (solo las que la app dejó en cola), con los datos del receptor."""
    if not _runner_ok(request):
        return JSONResponse(status_code=401, content={"error": "token"})
    ids = [int(x) for x in (await request.json()).get("ids", [])]
    async with httpx.AsyncClient(timeout=20) as c:
        filas = [await _factura(c, i) for i in ids]
    salida = []
    for f in filas:
        if not f or f["estado"] != "en_cola":
            continue
        y, m = int(f["periodo"][:4]), int(f["periodo"][4:])
        salida.append({"id": f["id"], "periodo": f["periodo"], "monto": int(round(float(f["monto"]))),
                       "detalle": f["detalle"] or _detalle(f["periodo"]),
                       "desde": f"01/{m:02d}/{y}", "hasta": f"{calendar.monthrange(y, m)[1]:02d}/{m:02d}/{y}",
                       "dni": CONTRATO["dni"], "inquilino": CONTRATO["inquilino"]})
    return {"facturas": salida}


@router.post("/runner/humberto/actualizar")
async def runner_actualizar(request: Request):
    if not _runner_ok(request):
        return JSONResponse(status_code=401, content={"error": "token"})
    body = await request.json()
    campos = {k: v for k, v in body.items() if k in CAMPOS_RUNNER}
    async with httpx.AsyncClient(timeout=15) as c:
        await _actualizar(c, int(body["id"]), campos)
    return {"ok": True}


@router.post("/runner/humberto/aprobacion")
async def runner_aprobacion(request: Request):
    if not _runner_ok(request):
        return JSONResponse(status_code=401, content={"error": "token"})
    async with httpx.AsyncClient(timeout=15) as c:
        f = await _factura(c, int((await request.json())["id"]))
    return {"aprobado": f and f.get("aprobado")}


@router.post("/runner/humberto/log")
async def runner_log(request: Request):
    if not _runner_ok(request):
        return JSONResponse(status_code=401, content={"error": "token"})
    body = await request.json()
    async with httpx.AsyncClient(timeout=15) as c:
        await c.post(f"{SUPABASE_URL}/rest/v1/humberto_log", json={"mensaje": str(body.get("mensaje", ""))[:500]},
                     headers=_sb({"Content-Type": "application/json", "Prefer": "return=minimal"}))
    return {"ok": True}


@router.post("/runner/humberto/archivo")
async def runner_archivo(request: Request, id: int = Form(...), nombre: str = Form(...),
                         archivo: UploadFile = File(...)):
    if not _runner_ok(request):
        return JSONResponse(status_code=401, content={"error": "token"})
    if not re.fullmatch(r"[\w.\-]+\.(png|pdf)", nombre):
        return JSONResponse(status_code=400, content={"error": "nombre inválido"})
    tipo = "application/pdf" if nombre.endswith(".pdf") else "image/png"
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{int(id)}/{nombre}",
                         content=await archivo.read(), headers=_sb({"Content-Type": tipo, "x-upsert": "true"}))
    if not r.is_success:
        return JSONResponse(status_code=502, content={"error": r.text[:200]})
    return {"ok": True}
