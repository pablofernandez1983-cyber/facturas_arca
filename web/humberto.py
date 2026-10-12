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
import io
import csv
import asyncio
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
_ipc_estado = {"intento": 0.0, "busqueda": None}
FUENTES_INDICE = ("INDEC", "datos.gob.ar")   # traen el índice exacto; las otras solo el % con 1 decimal


async def _ipc(c):
    """({'YYYYMM': índice}, {'YYYYMM': fuente}) desde la tabla `ipc_indec`: responde al instante.

    Solo se sale a buscar cuando hace falta: si el próximo ajuste necesita un IPC que ya debería
    estar publicado (el INDEC lo publica a mediados del mes siguiente; se busca desde el día 8) y
    no está, o está solo estimado. En ese caso, como mucho cada 2 h y en segundo plano (el
    11/10/2026 datos.gob.ar se colgó y la pantalla quedaba en "Cargando...")."""
    ipc, fuentes = await _ipc_leer(c)
    falta = _ipc_que_falta(ipc, fuentes)
    if falta and time.time() - _ipc_estado["intento"] > 2 * 3600:
        _ipc_estado["intento"] = time.time()
        asyncio.create_task(_ipc_refrescar(falta, ipc, fuentes))
    if not falta:
        _ipc_estado["busqueda"] = None
    return ipc, fuentes


async def _ipc_leer(c):
    r = await c.get(f"{SUPABASE_URL}/rest/v1/ipc_indec?select=periodo,indice,fuente", headers=_sb())
    r.raise_for_status()
    return ({f["periodo"]: float(f["indice"]) for f in r.json()},
            {f["periodo"]: f["fuente"] for f in r.json()})


def _ipc_que_falta(ipc: dict, fuentes: dict) -> list:
    """Meses del próximo ajuste pendiente cuyo IPC ya debería estar publicado y no está (o está
    estimado desde un %)."""
    hoy = _hoy()
    ini = CONTRATO["inicio"][:4] + CONTRATO["inicio"][5:7]
    fin = CONTRATO["fin"][:4] + CONTRATO["fin"][5:7]
    p = _mas_meses(ini, CONTRATO["cada_meses"])
    while p <= fin:
        necesarios = [_mas_meses(p, -k) for k in (4, 3, 2, 1)]
        incompletos = [m for m in necesarios if m not in ipc or fuentes.get(m) not in FUENTES_INDICE]
        if incompletos:
            # Solo importa en el mes del ajuste: el IPC del mes anterior sale a mediados (día 8 en
            # adelante). Los meses intermedios se completan en esa misma búsqueda.
            return incompletos if hoy >= date(int(p[:4]), int(p[4:]), 8) else []
        p = _mas_meses(p, CONTRATO["cada_meses"])
    return []


async def _ipc_refrescar(falta: list, ipc: dict, fuentes: dict):
    """Busca en orden INDEC (CSV oficial), datos.gob.ar, BCRA y ArgentinaDatos hasta completar lo
    que falta. Los índices exactos pisan a los estimados; un estimado nunca pisa un índice."""
    nuevos, probadas = {}, []   # {periodo: (indice, fuente)}

    def pendientes():
        return [m for m in falta if m not in nuevos]

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        # 1) INDEC: serie_ipc_divisiones.csv (Nacional, nivel general = código 0)
        try:
            probadas.append("INDEC")
            r = await c.get("https://www.indec.gob.ar/ftp/cuadros/economia/serie_ipc_divisiones.csv")
            r.raise_for_status()
            try:
                texto = r.content.decode("utf-8")
            except UnicodeDecodeError:
                texto = r.content.decode("latin-1")
            for fila in csv.DictReader(io.StringIO(texto), delimiter=";"):
                if fila.get("Codigo") == "0" and fila.get("Region") == "Nacional" and fila.get("Indice_IPC") not in (None, "", "NA"):
                    nuevos[fila["Periodo"]] = (float(fila["Indice_IPC"].replace(",", ".")), "INDEC")
        except Exception as e:
            print(f"IPC INDEC: {e}")

        # 2) datos.gob.ar (cachea por URL: varias variantes en paralelo y se unen)
        if pendientes():
            probadas.append("datos.gob.ar")
            hoy = _hoy()
            variantes = [{"start_date": "2025-10-01"}, {"start_date": f"{hoy.year - 1}-{hoy.month:02d}-01"},
                         {"start_date": "2025-01-01", "limit": 999}]

            async def una(v):
                r = await c.get("https://apis.datos.gob.ar/series/api/series/",
                                params={"ids": IPC_SERIE, "format": "json", **v}, timeout=12)
                r.raise_for_status()
                return {f[0][:4] + f[0][5:7]: float(f[1]) for f in r.json()["data"] if f[1] is not None}
            for x in await asyncio.gather(*(una(v) for v in variantes), return_exceptions=True):
                if isinstance(x, dict):
                    for p, v in x.items():
                        nuevos.setdefault(p, (v, "datos.gob.ar"))

        # 3) y 4) Solo el % mensual (1 decimal): se estima el índice encadenando desde el mes anterior
        async def por_porcentaje(nombre, url, params, extraer):
            if not pendientes():
                return
            probadas.append(nombre)
            try:
                r = await c.get(url, params=params)
                r.raise_for_status()
                pct = extraer(r.json())
            except Exception as e:
                print(f"IPC {nombre}: {e}")
                return
            for m in sorted(pendientes()):
                ant = _mas_meses(m, -1)
                base = nuevos.get(ant, (ipc.get(ant), None))[0]
                if base and m in pct:
                    nuevos[m] = (round(base * (1 + pct[m] / 100), 4), f"{nombre} (%)")

        await por_porcentaje("BCRA", "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias/27",
                             {"desde": f"{falta[0][:4]}-{falta[0][4:]}-01"},
                             lambda j: {d["fecha"][:4] + d["fecha"][5:7]: float(d["valor"])
                                        for d in j["results"][0]["detalle"]})
        await por_porcentaje("ArgentinaDatos", "https://api.argentinadatos.com/v1/finanzas/indices/inflacion", None,
                             lambda j: {d["fecha"][:4] + d["fecha"][5:7]: float(d["valor"]) for d in j[-24:]})

        # Guardar: índices exactos siempre; estimados solo donde no hay un índice exacto
        filas = [{"periodo": p, "indice": v, "fuente": f, "actualizado_at": datetime.now(timezone.utc).isoformat()}
                 for p, (v, f) in nuevos.items()
                 if f in FUENTES_INDICE and (ipc.get(p) != v or fuentes.get(p) != f)
                 or f not in FUENTES_INDICE and fuentes.get(p) not in FUENTES_INDICE]
        if filas:
            await c.post(f"{SUPABASE_URL}/rest/v1/ipc_indec?on_conflict=periodo", json=filas,
                         headers=_sb({"Content-Type": "application/json",
                                      "Prefer": "resolution=merge-duplicates,return=minimal"}))
    hora = datetime.now(AR).strftime("%d/%m %H:%M")
    no_esta = [_mes_txt(m) for m in falta if m not in nuevos]
    estimado = [f"{_mes_txt(m)} ({nuevos[m][1]})" for m in falta if m in nuevos and nuevos[m][1] not in FUENTES_INDICE]
    if no_esta:
        _ipc_estado["busqueda"] = (f"Busqué el IPC de {', '.join(no_esta)} en {', '.join(probadas)} ({hora}): "
                                   f"todavía no salió. Vuelvo a mirar en unas horas.")
    elif estimado:
        _ipc_estado["busqueda"] = (f"IPC de {', '.join(estimado)} estimado desde el % mensual (1 decimal); "
                                   f"cuando el INDEC publique el índice exacto se corrige solo.")
    else:
        _ipc_estado["busqueda"] = None


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
            ipc, fuentes = await _ipc(c)
        except Exception as e:
            ipc, fuentes, ipc_error = {}, {}, f"No pude leer el IPC guardado: {e}"
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
            "ipc_busqueda": _ipc_estado["busqueda"],
            "ipc_estimados": [f"{_mes_txt(p)} ({f})" for p, f in sorted(fuentes.items())
                              if f not in FUENTES_INDICE and p >= "202603"],
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


@router.post("/runner/humberto/ipc")
async def runner_ipc(request: Request):
    """Chequeo diario (humberto_ipc.yml, días 8-25): si falta el IPC del ajuste de este mes lo busca
    (sin esperar a que se abra la app) y, cuando el ajuste queda completo con índices exactos y
    todavía no se avisó, devuelve los datos para el mail. El aviso se marca con /ipc/avisado
    recién después de mandar el mail."""
    if not _runner_ok(request):
        return JSONResponse(status_code=401, content={"error": "token"})
    async with httpx.AsyncClient(timeout=20) as c:
        ipc, fuentes = await _ipc_leer(c)
        falta = _ipc_que_falta(ipc, fuentes)
        if falta:
            _ipc_estado["intento"] = time.time()
            await _ipc_refrescar(falta, ipc, fuentes)
            ipc, fuentes = await _ipc_leer(c)
        hoy = _hoy()
        actual = f"{hoy.year}{hoy.month:02d}"
        aj = next((a for a in _cronograma(ipc) if a["periodo"] == actual), None)
        exacto = aj and all(fuentes.get(_mas_meses(actual, -k)) in FUENTES_INDICE for k in (4, 3, 2, 1))
        if not aj or aj["estado"] != "aplicado" or not exacto:
            return {"aviso": None, "falta": [_mes_txt(m) for m in _ipc_que_falta(ipc, fuentes)],
                    "busqueda": _ipc_estado["busqueda"]}
        r = await c.get(f"{SUPABASE_URL}/rest/v1/humberto_avisos?periodo=eq.{actual}&select=periodo", headers=_sb())
        if r.json():
            return {"aviso": None, "ya_avisado": actual}
        facturas = {f["periodo"]: f for f in await _facturas(c)}
        anterior = facturas.get(_mas_meses(actual, -1))
        pagado = float(anterior["monto"]) if anterior and anterior.get("monto") else round(aj["valor_anterior"])
    return {"aviso": {"periodo": actual, "mes": aj["mes"], "meses_ipc": aj["meses_ipc"],
                      "variacion": aj["variacion"], "valor_anterior": aj["valor_anterior"], "valor": aj["valor"],
                      "pagado": pagado, "diferencia": round(aj["valor"] - pagado, 2),
                      "inquilino": CONTRATO["inquilino"], "inmueble": CONTRATO["inmueble"]}}


@router.post("/runner/humberto/ipc/avisado")
async def runner_ipc_avisado(request: Request):
    if not _runner_ok(request):
        return JSONResponse(status_code=401, content={"error": "token"})
    periodo = str((await request.json()).get("periodo", ""))
    if not re.fullmatch(r"\d{6}", periodo):
        return JSONResponse(status_code=400, content={"error": "período inválido"})
    async with httpx.AsyncClient(timeout=15) as c:
        await c.post(f"{SUPABASE_URL}/rest/v1/humberto_avisos?on_conflict=periodo", json={"periodo": periodo},
                     headers=_sb({"Content-Type": "application/json",
                                  "Prefer": "resolution=ignore-duplicates,return=minimal"}))
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
