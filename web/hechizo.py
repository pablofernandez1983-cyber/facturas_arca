"""
Facturación de Hechizo (Lorena Digiorno, monotributo Cat. B) desde la app.

Port de contador/tiendanube_a_arca.py: mismas reglas para elegir qué órdenes se facturan
(MercadoPago + pago recibido + enviada/entregada/lista para retirar) y mismo tope mensual
leído de la página de categorías de ARCA. La emisión real corre en GitHub Actions, en el
repo privado RUNNER_REPO (scripts/emitir_hechizo.py), que reporta por /runner/hechizo/*.

Env vars:
    TIENDANUBE_STORE_ID, TIENDANUBE_ACCESS_TOKEN
    RUNNER_TOKEN   (secreto compartido con el workflow del runner)
    RUNNER_REPO    (default pablofernandez1983-cyber/arca-runner)
"""

import os
import re
import hmac
import time
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

SUPABASE_URL  = os.environ["SUPABASE_URL"]
SUPABASE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_ANON_KEY"]
GITHUB_PAT    = os.environ["GITHUB_PAT"]
TN_STORE_ID   = os.environ.get("TIENDANUBE_STORE_ID", "")
TN_TOKEN      = os.environ.get("TIENDANUBE_ACCESS_TOKEN", "")
RUNNER_TOKEN  = os.environ.get("RUNNER_TOKEN", "")
RUNNER_REPO   = os.environ.get("RUNNER_REPO", "pablofernandez1983-cyber/arca-runner")
WORKFLOW_FILE = "hechizo.yml"

AR = timezone(timedelta(hours=-3))

router = APIRouter()


def _sb_headers(extra=None):
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", **(extra or {})}


def _gh_headers():
    return {"Authorization": f"Bearer {GITHUB_PAT}", "Accept": "application/vnd.github.v3+json"}


def _hoy_ar() -> datetime:
    return datetime.now(AR).replace(tzinfo=None)


# ─────────────────────────────────────────────
# Tope mensual Monotributo Cat. B (igual que actualizar_limite_desde_arca del script de la PC)
# ─────────────────────────────────────────────
ARCA_CATEGORIAS_URL = "https://www.afip.gob.ar/monotributo/categorias.asp"
_limite = {"anual": 17_595_182.74, "origen": "valor por defecto", "ts": 0.0, "verificado": False}


async def _limite_cat_b() -> dict:
    if _limite["verificado"] and time.time() - _limite["ts"] < 6 * 3600:
        return _limite
    try:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "Mozilla/5.0"}) as c:
            r = await c.get(ARCA_CATEGORIAS_URL)
        r.raise_for_status()
        m = re.search(r'>\s*B\s*</th>\s*<td[^>]*Ingresos brutos[^>]*>\s*\$\s*([\d.,]+)', r.text)
        if not m:
            raise ValueError("no encontré la fila de Categoría B")
        anual = float(m.group(1).replace(".", "").replace(",", "."))
        if not 1_000_000 < anual < 10_000_000_000:
            raise ValueError(f"valor raro: {anual}")
        _limite.update(anual=anual, origen=f"ARCA {_hoy_ar():%d/%m/%Y}", ts=time.time(), verificado=True)
    except Exception as e:
        print(f"[hechizo] no pude leer el tope de ARCA: {e}")
        if not _limite["verificado"]:
            _limite["origen"] = "sin verificar, valor por defecto"
        else:
            _limite["origen"] = _limite["origen"].replace("ARCA", "sin verificar, leído")
    return _limite


# ─────────────────────────────────────────────
# Tienda Nube
# ─────────────────────────────────────────────
TRADUCCION_ESTADOS = {
    'paid': 'Recibido', 'delivered': 'Entregado', 'shipped': 'Enviado',
    'ready_for_pickup': 'Listo para retirar',
}
ESTADOS_PAGO_OK  = {'recibido', 'paid'}
ESTADOS_ENVIO_OK = {'entregado', 'delivered', 'shipped', 'enviado',
                    'ready_for_pickup', 'listo para retirar'}
MP_KEYWORDS = ["mercadopago", "mercado pago", "mp", "mercado_pago"]


def _traducir(estado) -> str:
    return TRADUCCION_ESTADOS.get(str(estado or "").lower(), str(estado or ""))


def _es_mp(medio: str) -> bool:
    mp = str(medio).lower()
    return any(k in mp for k in MP_KEYWORDS)


def normalize_amount(raw) -> str:
    """Monto en el formato que acepta ARCA (sin miles, punto decimal). Igual que en la PC."""
    s = str(raw).strip().replace("$", "").replace(" ", "")
    if not s:
        return ""
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return str(raw)
    return str(int(d)) if d == d.to_integral_value() else str(d.quantize(Decimal("0.01")))


def _dt_ar(fecha_iso: str) -> datetime:
    dt = datetime.fromisoformat(fecha_iso.replace('Z', '+00:00').replace('+0000', '+00:00'))
    return dt.astimezone(AR).replace(tzinfo=None) if dt.tzinfo else dt


def _rango_periodo(periodo: str) -> tuple[datetime, datetime]:
    hoy = _hoy_ar()
    if periodo == "anterior":
        ultimo = hoy.replace(day=1) - timedelta(days=1)
        return ultimo.replace(day=1), ultimo
    return hoy.replace(day=1), hoy


async def _ventas_tn(desde: datetime, hasta: datetime) -> list:
    url = f"https://api.tiendanube.com/v1/{TN_STORE_ID}/orders"
    headers = {"Authentication": f"bearer {TN_TOKEN}",
               "User-Agent": "FacturadorIntegrado (pablofernandez1983@gmail.com)"}
    params = {"per_page": 200,
              "created_at_min": desde.strftime("%Y-%m-%dT00:00:00-03:00"),
              "created_at_max": hasta.strftime("%Y-%m-%dT23:59:59-03:00")}
    todas, page = [], 1
    async with httpx.AsyncClient(timeout=30) as c:
        while True:
            for intento in range(3):
                try:
                    r = await c.get(url, headers=headers, params={**params, "page": page})
                    if r.status_code == 404 and page > 1:   # TN devuelve 404 al pasarse de la última página
                        return todas
                    r.raise_for_status()
                    break
                except httpx.HTTPError:
                    if intento == 2:
                        raise
                    await asyncio.sleep(2 * (intento + 1))
            lote = r.json()
            todas.extend(lote)
            if len(lote) < 200:
                return todas
            page += 1
            await asyncio.sleep(1)


def _facturables(ventas: list, desde: datetime, hasta: datetime) -> list[dict]:
    items = []
    for v in ventas:
        try:
            fecha = _dt_ar(v.get("created_at", ""))
        except Exception:
            continue
        if not desde.date() <= fecha.date() <= hasta.date():
            continue
        pago_ok  = _traducir(v.get("payment_status")).lower() in ESTADOS_PAGO_OK
        envio_ok = _traducir(v.get("shipping_status")).lower() in ESTADOS_ENVIO_OK
        medio    = v.get("gateway_name", v.get("gateway", "")) or ""
        if not (_es_mp(medio) and pago_ok and envio_ok):
            continue
        customer = v.get("customer") or {}
        items.append({
            "numero_orden": str(v.get("number", "")),
            "fecha":        fecha.strftime("%d/%m %H:%M"),
            "comprador":    customer.get("name", "") or "",
            "monto":        normalize_amount(v.get("total", 0)),
        })
    items.sort(key=lambda it: int(it["numero_orden"]) if it["numero_orden"].isdigit() else 0)
    return items


async def _historial(numeros: list[str]) -> set:
    if not numeros:
        return set()
    lista = ",".join(n for n in numeros if n.isdigit())
    if not lista:
        return set()
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/hechizo_facturas?select=numero_orden&numero_orden=in.({lista})",
                        headers=_sb_headers())
    r.raise_for_status()
    return {row["numero_orden"] for row in r.json()}


async def _facturado_mes() -> float:
    """El tope de ARCA cuenta por fecha de emisión: lo facturado en el mes calendario actual."""
    hoy = _hoy_ar()
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/hechizo_facturas?select=monto"
                        f"&fecha=gte.{hoy:%Y-%m}-01&fecha=lte.{hoy:%Y-%m-%d}", headers=_sb_headers())
    r.raise_for_status()
    return round(sum(float(row["monto"]) for row in r.json()), 2)


async def _ordenes_periodo(periodo: str):
    desde, hasta = _rango_periodo(periodo)
    ventas = await _ventas_tn(desde, hasta)
    items = _facturables(ventas, desde, hasta)
    ya = await _historial([it["numero_orden"] for it in items])
    return desde, items, ya


# ─────────────────────────────────────────────
# API para la app (protegida por el PIN en el middleware de main.py)
# ─────────────────────────────────────────────
@router.get("/api/hechizo/ordenes")
async def hechizo_ordenes(periodo: str = "actual"):
    if not TN_STORE_ID or not TN_TOKEN:
        return JSONResponse(status_code=503, content={"error": "Faltan las credenciales de Tienda Nube en el servidor"})
    try:
        desde, items, ya = await _ordenes_periodo(periodo)
        limite, facturado = await asyncio.gather(_limite_cat_b(), _facturado_mes())
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": f"No pude traer los datos: {e}"})
    pendientes = [it for it in items if it["numero_orden"] not in ya]
    emitidas   = [it for it in items if it["numero_orden"] in ya]
    return {
        "periodo":        desde.strftime("%Y-%m"),
        "limite_mensual": round(limite["anual"] / 12, 2),
        "limite_origen":  limite["origen"],
        "facturado_mes":  facturado,
        "mes_actual":     _hoy_ar().strftime("%Y-%m"),
        "ya_emitidas":    len(emitidas),
        "ya_emitidas_total": round(sum(float(it["monto"]) for it in emitidas), 2),
        "pendientes":     pendientes,
    }


@router.post("/api/hechizo/emitir")
async def hechizo_emitir(request: Request):
    """Body: {"periodo": "actual"|"anterior", "ordenes": ["11462", ...]}.
    Los montos no se toman del celu: se vuelven a leer de Tienda Nube y se descartan las ya facturadas."""
    import json
    body = await request.json()
    pedidas = {str(n) for n in body.get("ordenes", [])}
    if not pedidas:
        return JSONResponse(status_code=400, content={"error": "No elegiste ninguna orden"})
    try:
        _, items, ya = await _ordenes_periodo(body.get("periodo", "actual"))
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": f"No pude traer los datos: {e}"})
    elegidas = [{"numero_orden": it["numero_orden"], "monto": it["monto"], "comprador": it["comprador"][:60]}
                for it in items if it["numero_orden"] in pedidas and it["numero_orden"] not in ya]
    if not elegidas:
        return JSONResponse(status_code=409, content={"error": "Esas órdenes ya están facturadas o ya no califican"})

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"https://api.github.com/repos/{RUNNER_REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches",
                         headers=_gh_headers(),
                         json={"ref": "main", "inputs": {"ordenes": json.dumps(elegidas, ensure_ascii=False)}})
    if r.status_code == 204:
        return {"ok": True, "cantidad": len(elegidas),
                "total": round(sum(float(e["monto"]) for e in elegidas), 2)}
    return JSONResponse(status_code=502, content={"error": f"GitHub {r.status_code}: {r.text[:200]}"})


@router.get("/api/hechizo/estado")
async def hechizo_estado():
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"https://api.github.com/repos/{RUNNER_REPO}/actions/workflows/{WORKFLOW_FILE}/runs?per_page=1",
                        headers=_gh_headers())
    if not r.is_success:   # nunca devolver el 401/404 de GitHub tal cual: la app lo tomaría como PIN incorrecto
        return JSONResponse(status_code=502, content={"error": f"GitHub {r.status_code}: {r.text[:200]}"})
    runs = r.json().get("workflow_runs", [])
    if not runs:
        return {"status": "none"}
    run = runs[0]
    return {"status": run["status"], "conclusion": run["conclusion"],
            "url": run["html_url"], "started_at": run["run_started_at"], "run_id": run["id"]}


@router.get("/api/hechizo/progreso")
async def hechizo_progreso():
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/hechizo_log?select=mensaje,creado_at&order=id.asc&limit=300",
                        headers=_sb_headers())
    return r.json() if r.is_success else []


# Para el script de la PC (tiendanube_a_arca.py), que comparte el mismo historial.
@router.post("/api/hechizo/historial/consultar")
async def hechizo_historial_consultar(request: Request):
    body = await request.json()
    ya = await _historial([str(n) for n in body.get("ordenes", [])])
    return {"emitidas": sorted(ya), "facturado_mes": await _facturado_mes()}


@router.post("/api/hechizo/historial/registrar")
async def hechizo_historial_registrar(request: Request):
    body = await request.json()
    return await _registrar(body, origen="pc")


# ─────────────────────────────────────────────
# Endpoints del runner (GitHub Actions) — token propio, no el PIN
# ─────────────────────────────────────────────
def _runner_ok(request: Request) -> bool:
    tok = request.headers.get("X-Runner-Token", "")
    return bool(RUNNER_TOKEN) and hmac.compare_digest(tok.encode(), RUNNER_TOKEN.encode())


async def _registrar(body: dict, origen: str):
    fila = {
        "numero_orden": str(body["numero_orden"]),
        "monto":        round(float(body["monto"]), 2),
        "fecha":        _hoy_ar().strftime("%Y-%m-%d"),
        "comprador":    (body.get("comprador") or "")[:120],
        "origen":       origen,
    }
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{SUPABASE_URL}/rest/v1/hechizo_facturas?on_conflict=numero_orden",
                         json=fila,
                         headers=_sb_headers({"Content-Type": "application/json",
                                              "Prefer": "resolution=ignore-duplicates,return=minimal"}))
    if not r.is_success:
        return JSONResponse(status_code=502, content={"error": r.text})
    return {"ok": True}


@router.post("/runner/hechizo/inicio")
async def runner_inicio(request: Request):
    if not _runner_ok(request):
        return JSONResponse(status_code=401, content={"error": "token"})
    async with httpx.AsyncClient(timeout=15) as c:
        await c.delete(f"{SUPABASE_URL}/rest/v1/hechizo_log?id=gte.0", headers=_sb_headers())
        await c.post(f"{SUPABASE_URL}/rest/v1/hechizo_log",
                     json={"mensaje": "🚀 Preparando el robot (instalando, ~1-2 min)..."},
                     headers=_sb_headers({"Content-Type": "application/json", "Prefer": "return=minimal"}))
    return {"ok": True}


@router.post("/runner/hechizo/log")
async def runner_log(request: Request):
    if not _runner_ok(request):
        return JSONResponse(status_code=401, content={"error": "token"})
    body = await request.json()
    async with httpx.AsyncClient(timeout=15) as c:
        await c.post(f"{SUPABASE_URL}/rest/v1/hechizo_log", json={"mensaje": str(body.get("mensaje", ""))[:500]},
                     headers=_sb_headers({"Content-Type": "application/json", "Prefer": "return=minimal"}))
    return {"ok": True}


@router.post("/runner/hechizo/emitidas")
async def runner_emitidas(request: Request):
    if not _runner_ok(request):
        return JSONResponse(status_code=401, content={"error": "token"})
    body = await request.json()
    return {"emitidas": sorted(await _historial([str(n) for n in body.get("ordenes", [])]))}


@router.post("/runner/hechizo/registrar")
async def runner_registrar(request: Request):
    if not _runner_ok(request):
        return JSONResponse(status_code=401, content={"error": "token"})
    return await _registrar(await request.json(), origen="celu")
