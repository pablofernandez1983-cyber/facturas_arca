"""
Emite facturas MAMA desde Supabase via Playwright headless.
Corre en GitHub Actions.

Env vars requeridas:
    ARCA_CUIT_MAMA, ARCA_CLAVE_MAMA
    SUPABASE_URL, SUPABASE_SERVICE_KEY

Args opcionales:
    --ids 1,2,3     IDs de Supabase a emitir (si no se pasa: todos los pendientes del mes actual)
"""

import os
import re
import random
import argparse
from datetime import datetime, timezone
import pandas as pd
from supabase import create_client
from playwright.sync_api import Playwright, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

# ========= Config =========
TIPO         = "MAMA"
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
FAST_HUMAN   = True
PDF_DIR      = "/tmp/pdfs"

# ========= Args =========
parser = argparse.ArgumentParser()
parser.add_argument("--ids", type=str, default="",
                    help="IDs de Supabase separados por coma. Vacío = pendientes del mes.")
args = parser.parse_args()
IDS_FORZADOS = [int(x) for x in args.ids.split(",") if x.strip()] if args.ids.strip() else []

# ========= Helpers =========
def human_pause(min_s=0.03, max_s=0.12):
    if not FAST_HUMAN: min_s, max_s = 0.4, 1.2
    return int(random.uniform(min_s, max_s) * 1000)

def safe_wait(page, ms_min=20, ms_max=80):
    if not FAST_HUMAN: ms_min, ms_max = 500, 1200
    page.wait_for_timeout(int(random.uniform(ms_min, ms_max)))

def safe_click(locator, page=None, timeout=60000):
    locator.wait_for(state="visible", timeout=timeout)
    if page: page.wait_for_timeout(human_pause())
    locator.click()
    if page: page.wait_for_timeout(human_pause())

def fast_fill(locator, value: str, page=None, timeout=60000):
    locator.wait_for(state="visible", timeout=timeout)
    if page: page.wait_for_timeout(human_pause())
    locator.fill(value)
    if page: page.wait_for_timeout(human_pause())

def fmt_fecha(date_str: str) -> str:
    """YYYY-MM-DD → DD/MM/YYYY"""
    if not date_str: return ""
    try: return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d/%m/%Y")
    except: return date_str

def obtener_facturas(sb) -> list[dict]:
    if IDS_FORZADOS:
        resp = sb.table("facturas").select("*").in_("id", IDS_FORZADOS).eq("tipo", TIPO).execute()
    else:
        mes = datetime.now().strftime("%Y-%m")
        # Filtrar por mes actual y no emitidas
        resp = (sb.table("facturas").select("*")
                  .eq("tipo", TIPO)
                  .eq("emitida", False)
                  .gte("fecha_cbte", f"{mes}-01")
                  .lte("fecha_cbte", f"{mes}-31")
                  .execute())
    return resp.data or []

def marcar_emitida(sb, factura_id: int):
    sb.table("facturas").update({
        "emitida":    True,
        "emitida_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", factura_id).execute()
    print(f"   📝 ID {factura_id} marcada como emitida en Supabase.")

def wait_comprobante_generado(page, timeout=180000):
    try:
        page.get_by_text(re.compile(r"Comprobante\s+Generado", re.I)).wait_for(
            state="visible", timeout=timeout)
        return
    except Exception: pass
    page.wait_for_function(
        "() => { const el = document.querySelector('#botones_comprobante'); "
        "return el && getComputedStyle(el).display !== 'none'; }",
        timeout=timeout)

def click_imprimir_y_guardar(page2, context, out_path: str) -> bool:
    btn = page2.locator("input[type='button'][value*='Imprimir']").first
    if btn.count() == 0:
        print("⚠️ No encontré el botón Imprimir...")
        return False
    try:
        with page2.expect_download(timeout=8000) as d:
            btn.scroll_into_view_if_needed()
            btn.click()
        d.value.save_as(out_path)
        print(f"📄 PDF guardado (download): {out_path}")
        return True
    except Exception:
        pass
    try:
        with page2.expect_popup(timeout=8000) as p:
            btn.scroll_into_view_if_needed()
            btn.click()
        pdf_page = p.value
        pdf_page.wait_for_load_state("domcontentloaded")
        url = pdf_page.url
        for _ in range(20):
            if url and url != "about:blank":
                break
            pdf_page.wait_for_timeout(250)
            url = pdf_page.url
        if not url or url == "about:blank":
            print("⚠️ Popup abierto pero sin URL de PDF.")
            try: pdf_page.close()
            except: pass
            return False
        resp = context.request.get(url)
        if not resp.ok:
            print(f"⚠️ No pude bajar el PDF. HTTP {resp.status}")
            try: pdf_page.close()
            except: pass
            return False
        with open(out_path, "wb") as f:
            f.write(resp.body())
        print(f"📄 PDF guardado (popup): {out_path}")
        try: pdf_page.close()
        except: pass
        return True
    except PlaywrightTimeoutError:
        print("⚠️ Imprimir no generó download ni popup.")
        return False
    except Exception as e:
        print(f"⚠️ Error al manejar Imprimir: {e}")
        return False

def confirmar_y_emitir(page):
    """
    1. Click en #btngenerar  ("Confirmar Datos...")
    2. Click en botón "Confirmar" del diálogo jQuery UI
    """
    btn1 = page.locator("#btngenerar")
    btn1.wait_for(state="visible", timeout=30000)
    safe_click(btn1, page=page)
    print("   ✔ Confirmar Datos...")
    safe_wait(page, 300, 600)
    btn2 = page.get_by_role("button", name="Confirmar", exact=True)
    btn2.wait_for(state="visible", timeout=30000)
    safe_click(btn2, page=page)
    print("   ✔ Confirmar (diálogo)")

def abrir_comprobantes_en_linea(page1, contribuyente_btn_text: str):
    try:
        buscador = page1.get_by_role("combobox", name="Buscador")
        buscador.wait_for(state="visible", timeout=60000)
    except Exception:
        buscador = page1.get_by_role("combobox").first
        buscador.wait_for(state="visible", timeout=60000)
    safe_click(buscador, page=page1)
    page1.wait_for_timeout(human_pause(0.05, 0.12))
    buscador.fill("comprobantes")
    page1.wait_for_timeout(human_pause(0.05, 0.12))
    link = page1.get_by_role("link", name=re.compile(r"Comprobantes en línea", re.I))
    link.wait_for(state="visible", timeout=60000)
    with page1.expect_popup() as pinfo:
        safe_click(link, page=page1)
    page2 = pinfo.value
    page2.wait_for_load_state("domcontentloaded")
    page2.on("dialog", lambda d: d.accept())
    safe_wait(page2)
    btn = page2.get_by_role("button", name=re.compile(re.escape(contribuyente_btn_text), re.I))
    safe_click(btn, page=page2)
    page2.wait_for_load_state("domcontentloaded")
    safe_wait(page2)
    return page2

def run(playwright: Playwright) -> None:
    CUIT  = os.environ["ARCA_CUIT_MAMA"]
    CLAVE = os.environ["ARCA_CLAVE_MAMA"]
    sb    = create_client(SUPABASE_URL, SUPABASE_KEY)

    facturas = obtener_facturas(sb)
    if not facturas:
        print(f"✅ No hay facturas {TIPO} pendientes para emitir.")
        return
    print(f"📋 {len(facturas)} facturas {TIPO} a emitir.")

    os.makedirs(PDF_DIR, exist_ok=True)

    browser = playwright.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 720},
        accept_downloads=True,
    )
    page = context.new_page()

    # Login
    page.goto("https://www.arca.gob.ar/landing/default.asp", wait_until="domcontentloaded")
    safe_wait(page)
    with page.expect_popup() as page1_info:
        safe_click(page.get_by_role("link", name="Iniciar sesión"), page=page)
    page1 = page1_info.value
    page1.wait_for_load_state("domcontentloaded")
    safe_wait(page1)

    cuit_input = page1.get_by_role("spinbutton")
    cuit_input.wait_for(state="visible", timeout=60000)
    cuit_input.click()
    page1.wait_for_timeout(human_pause(0.06, 0.18))
    cuit_input.type(CUIT, delay=random.randint(25, 60))
    page1.wait_for_timeout(human_pause(0.06, 0.16))
    safe_click(page1.get_by_role("button", name="Siguiente"), page=page1)

    clave_input = page1.get_by_role("textbox", name="TU CLAVE")
    clave_input.wait_for(state="visible", timeout=60000)
    clave_input.click()
    page1.wait_for_timeout(human_pause(0.06, 0.18))
    clave_input.type(CLAVE, delay=random.randint(30, 70))
    safe_click(page1.get_by_role("button", name=re.compile(r"Ingresar", re.I)), page=page1)

    try:
        page1.get_by_role("combobox", name="Buscador").wait_for(state="visible", timeout=180000)
    except Exception:
        page1.get_by_role("combobox").first.wait_for(state="visible", timeout=180000)
    print("✅ Login exitoso")

    page2 = None
    current_contribuyente = None

    for f in facturas:
        fid               = f["id"]
        contribuyente_btn = (f.get("contribuyente_btn") or "").strip()
        doc_receptor      = (f.get("doc_receptor") or "").strip()
        detalle           = (f.get("detalle") or "").strip()
        precio            = str(f.get("precio") or "").strip()

        if not contribuyente_btn or not doc_receptor or not detalle or not precio:
            print(f"⚠️  ID {fid}: faltan datos, se saltea.")
            continue

        pto_vta      = str(f.get("pto_vta")      or "1")
        universo     = str(f.get("universo")     or "2")
        concepto     = str(f.get("concepto")     or "2")
        iva_receptor = str(f.get("iva_receptor") or "1")
        otra         = bool(f.get("otra", False))
        fecha_cbte   = fmt_fecha(f.get("fecha_cbte") or "")
        desde        = fmt_fecha(f.get("desde")      or "")
        hasta        = fmt_fecha(f.get("hasta")      or "")
        vto_pago     = fmt_fecha(f.get("vto_pago")   or "")

        print(f"\n➡️  ID {fid} | {contribuyente_btn} | {doc_receptor} | ${precio} | {fecha_cbte}")

        if contribuyente_btn != current_contribuyente:
            if page2:
                try: page2.close()
                except: pass
            page2 = abrir_comprobantes_en_linea(page1, contribuyente_btn)
            current_contribuyente = contribuyente_btn

        safe_click(page2.get_by_role("button", name="Generar Comprobantes"), page=page2)

        page2.locator("#puntodeventa").wait_for(state="visible", timeout=60000)
        page2.locator("#puntodeventa").select_option(pto_vta)
        safe_wait(page2)
        page2.locator("#universocomprobante").wait_for(state="visible", timeout=60000)
        page2.locator("#universocomprobante").select_option(universo)
        safe_wait(page2)
        safe_click(page2.get_by_role("button", name="Continuar >"), page=page2)

        fast_fill(page2.get_by_role("textbox", name="Fecha del Comprobante"), fecha_cbte, page=page2)
        page2.locator("#idconcepto").select_option(concepto)
        safe_wait(page2)
        fast_fill(page2.get_by_role("textbox", name="Desde"),           desde,    page=page2)
        fast_fill(page2.get_by_role("textbox", name="Hasta"),           hasta,    page=page2)
        fast_fill(page2.get_by_role("textbox", name="Vto. para el Pago"), vto_pago, page=page2)
        safe_click(page2.get_by_role("button", name="Continuar >"), page=page2)

        page2.locator("#idivareceptor").select_option(iva_receptor)
        safe_wait(page2)
        nro = page2.locator("#nrodocreceptor")
        nro.wait_for(state="visible", timeout=60000)
        nro.click(force=True)
        page2.wait_for_timeout(human_pause(0.05, 0.15))
        nro.fill("")
        page2.wait_for_timeout(human_pause(0.05, 0.12))
        nro.type(doc_receptor, delay=random.randint(15, 35))
        page2.wait_for_timeout(human_pause(0.20, 0.45))
        nro.press("Tab")
        page2.wait_for_timeout(int(random.uniform(900, 1600)))
        chk = page2.get_by_role("checkbox", name="Otra")
        chk.wait_for(state="visible", timeout=60000)
        chk.scroll_into_view_if_needed()
        chk.set_checked(otra, force=True)
        page2.wait_for_timeout(int(random.uniform(120, 260)))
        safe_click(page2.get_by_role("button", name="Continuar >"), page=page2)

        fast_fill(page2.locator("#detalle_descripcion1"), detalle, page=page2)
        fast_fill(page2.locator("#detalle_precio1"),      precio,  page=page2)
        safe_click(page2.get_by_role("button", name="Continuar >"), page=page2)

        # Confirmación manual si corre localmente, automática en GitHub Actions
        import sys
        if sys.stdin.isatty():
            print(f"\n{'='*55}")
            print(f"  Revisá el browser — factura lista para emitir:")
            print(f"  Receptor: {doc_receptor} | ${precio} | {fecha_cbte}")
            print(f"{'='*55}")
            respuesta = input("  ¿Emitir esta factura? [s = SÍ / cualquier otra = saltear]: ").strip().lower()
            if respuesta != "s":
                print("⏭️  Factura salteada.")
                try: page2.close()
                except: pass
                page2 = abrir_comprobantes_en_linea(page1, current_contribuyente)
                continue

        print("   🖱️  Confirmando...")
        confirmar_y_emitir(page2)
        wait_comprobante_generado(page2, timeout=180000)
        print(f"✅ ID {fid}: comprobante generado")

        marcar_emitida(sb, fid)

        # Descargar PDF
        raw_fecha   = f.get("fecha_cbte") or ""
        periodo     = datetime.strptime(raw_fecha, "%Y-%m-%d").strftime("%m-%Y") if raw_fecha else "00-0000"
        cuit_emisor = re.sub(r"[^0-9]", "", CUIT)
        cuit_recep  = re.sub(r"[^0-9]", "", doc_receptor)
        pdf_path    = os.path.join(PDF_DIR, f"Factura-{cuit_emisor}-{cuit_recep}-{periodo}.pdf")
        click_imprimir_y_guardar(page2, context, pdf_path)

        try: page2.close()
        except: pass
        page2 = abrir_comprobantes_en_linea(page1, current_contribuyente)

    print(f"\n🎉 Proceso terminado ({TIPO})")

with sync_playwright() as playwright:
    run(playwright)
