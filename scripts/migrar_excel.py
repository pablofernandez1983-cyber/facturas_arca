"""
Migración única: Excel local → Supabase

Correr UNA SOLA VEZ desde tu PC:
    pip install supabase pandas openpyxl
    python scripts/migrar_excel.py

Necesita la variable de entorno SUPABASE_SERVICE_KEY (la service_role, no la anon).
Podés setearla así antes de correr:
    set SUPABASE_SERVICE_KEY=eyJ...
"""

import os
import sys
import pandas as pd
from datetime import datetime, date
from supabase import create_client

SUPABASE_URL = "https://zqwkznlgbkofsrygqezk.supabase.co"
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]   # la service_role

EXCEL_PATH = r"C:\Users\loren\OneDrive\Escritorio\Hechizo\Python\data\facturas.xlsx"

SHEETS = {
    "MAMA": "Facturas",
    "PAPA": "Facturas_PAPA",
}

MES_ACTUAL = datetime.now().strftime("%Y-%m")   # ej: "2026-04"


def parse_date(val) -> str | None:
    if not val or str(val).strip().lower() in ("", "nan", "none"):
        return None
    try:
        return pd.to_datetime(val, dayfirst=True, errors="raise").strftime("%Y-%m-%d")
    except Exception:
        return None


def parse_precio(val) -> float | None:
    s = str(val).strip().replace(",", ".").replace("$", "").replace(" ", "")
    try:
        return float(s)
    except Exception:
        return None


def es_historica(fecha_str: str | None) -> bool:
    """True si la factura es de un mes anterior al actual → ya está emitida."""
    if not fecha_str:
        return False
    mes = fecha_str[:7]   # "YYYY-MM"
    return mes < MES_ACTUAL


def migrar():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    total = 0
    for tipo, sheet in SHEETS.items():
        print(f"\n📋 Procesando hoja '{sheet}' ({tipo})...")
        df = pd.read_excel(EXCEL_PATH, sheet_name=sheet).fillna("")
        df.columns = [c.strip().lower() for c in df.columns]

        filas = []
        for idx, row in df.iterrows():
            fecha = parse_date(row.get("fecha_cbte", ""))
            historica = es_historica(fecha)

            fila = {
                "tipo":             tipo,
                "idx_excel":        int(idx),
                "doc_receptor":     str(row.get("doc_receptor", "")).strip(),
                "detalle":          str(row.get("detalle", "")).strip(),
                "precio":           parse_precio(row.get("precio", "")),
                "fecha_cbte":       fecha,
                "contribuyente_btn": str(row.get("contribuyente_btn", "")).strip(),
                "pto_vta":          str(row.get("pto_vta", "") or "1").strip(),
                "universo":         str(row.get("universo", "") or "2").strip(),
                "concepto":         str(row.get("concepto", "") or "2").strip(),
                "desde":            parse_date(row.get("desde", "")),
                "hasta":            parse_date(row.get("hasta", "")),
                "vto_pago":         parse_date(row.get("vto_pago", "")),
                "iva_receptor":     str(row.get("iva_receptor", "") or "1").strip(),
                "otra":             str(row.get("otra", "")).strip().lower() in ("1", "true", "si", "sí", "x", "yes"),
                "emitida":          historica,
                "emitida_at":       datetime.now().isoformat() if historica else None,
            }

            # Saltar filas vacías
            if not fila["doc_receptor"] and not fila["detalle"]:
                continue

            filas.append(fila)
            marca = "✓ histórica" if historica else "◌ pendiente"
            print(f"  Fila {idx}: {fila['doc_receptor']} | {fila['fecha_cbte']} | {marca}")

        if filas:
            resp = sb.table("facturas").insert(filas).execute()
            print(f"  → {len(filas)} filas insertadas en Supabase")
            total += len(filas)

    print(f"\n🎉 Migración completa: {total} facturas cargadas.")
    print("   Las de meses anteriores quedaron marcadas como emitidas automáticamente.")


if __name__ == "__main__":
    migrar()
