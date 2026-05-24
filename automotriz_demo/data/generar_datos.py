"""
Generador de datasets sintéticos para Automotriz Argentina S.A.

Empresa ficticia: fabricante de autos, pickups, utilitarios y motos.
- 3 plantas: Córdoba (autos), Buenos Aires/Pacheco (pickups+utilitarios), Mendoza (motos)
- Importa CKDs desde casa matriz (Brasil) y de Asia
- Exporta a Brasil, Uruguay, Paraguay, Chile, Bolivia
- Vende en todo el país via concesionarios
- ~2000 empleados distribuidos
- Tiene servicios prestados por casa matriz (royalties, management fees, IT)
"""
import pandas as pd
import numpy as np
from datetime import date, timedelta
import random

random.seed(42)
np.random.seed(42)

# ============================================================
# CATÁLOGOS MAESTROS
# ============================================================

JURISDICCIONES = [
    "CABA", "Buenos Aires", "Catamarca", "Chaco", "Chubut", "Córdoba",
    "Corrientes", "Entre Ríos", "Formosa", "Jujuy", "La Pampa", "La Rioja",
    "Mendoza", "Misiones", "Neuquén", "Río Negro", "Salta", "San Juan",
    "San Luis", "Santa Cruz", "Santa Fe", "Santiago del Estero",
    "Tierra del Fuego", "Tucumán"
]

# Pesos demográficos aproximados (para distribuir clientes)
PESO_JUR = {
    "Buenos Aires": 38, "CABA": 7, "Córdoba": 8, "Santa Fe": 8,
    "Mendoza": 4, "Tucumán": 3, "Entre Ríos": 3, "Salta": 3, "Misiones": 3,
    "Chaco": 2.5, "Corrientes": 2.5, "Santiago del Estero": 2, "San Juan": 2,
    "Jujuy": 1.7, "Río Negro": 1.7, "Neuquén": 1.5, "Formosa": 1.3,
    "Chubut": 1.3, "San Luis": 1.2, "Catamarca": 1, "La Rioja": 0.8,
    "La Pampa": 0.8, "Santa Cruz": 0.7, "Tierra del Fuego": 0.3,
}

PLANTAS = {
    "PL-COR": {"nombre": "Planta Córdoba",       "jurisdiccion": "Córdoba",       "produce": ["AUT"]},
    "PL-PAC": {"nombre": "Planta Pacheco",       "jurisdiccion": "Buenos Aires",  "produce": ["PKP", "UTI"]},
    "PL-MZA": {"nombre": "Planta Mendoza",       "jurisdiccion": "Mendoza",       "produce": ["MOT"]},
    "PL-CDA": {"nombre": "Centro Distribución",  "jurisdiccion": "Buenos Aires",  "produce": []},
    "SC-CAB": {"nombre": "Sede Central",         "jurisdiccion": "CABA",          "produce": []},
}

SEGMENTOS = {
    "AUT": {"nombre": "Autos",       "precio_promedio": 25_000_000, "costo_unitario": 18_000_000},
    "PKP": {"nombre": "Pickups",     "precio_promedio": 45_000_000, "costo_unitario": 32_000_000},
    "UTI": {"nombre": "Utilitarios", "precio_promedio": 35_000_000, "costo_unitario": 26_000_000},
    "MOT": {"nombre": "Motos",       "precio_promedio":  4_500_000, "costo_unitario":  2_800_000},
}

# Productos específicos
PRODUCTOS = []
for seg, info in SEGMENTOS.items():
    if seg == "AUT":
        modelos = ["A150 Compacto", "A250 Sedán", "A350 SUV", "A150 GT", "A250 Hybrid"]
    elif seg == "PKP":
        modelos = ["P500 4x2", "P500 4x4", "P700 Heavy", "P500 GLS"]
    elif seg == "UTI":
        modelos = ["U200 Furgón", "U300 Combi", "U500 Carga"]
    else:  # MOT
        modelos = ["M125 Urban", "M250 Sport", "M450 Adventure", "M125 Trabajo", "M650 Scooter"]
    for i, m in enumerate(modelos, 1):
        PRODUCTOS.append({
            "sku": f"{seg}-{i:03d}",
            "segmento": seg,
            "modelo": m,
            "precio_lista": info["precio_promedio"] * np.random.uniform(0.85, 1.20),
            "costo_unitario_estandar": info["costo_unitario"] * np.random.uniform(0.92, 1.08),
        })

# Casa matriz y partes relacionadas (relevantes para PT)
PARTES_RELACIONADAS = {
    "CM-BRA-001": {"nombre": "AutoCorp Brasil S.A.",       "pais": "Brasil",    "vinculo": "Casa Matriz"},
    "VIN-MEX-001": {"nombre": "AutoCorp México",           "pais": "México",    "vinculo": "Vinculada"},
    "VIN-ESP-001": {"nombre": "AutoCorp Europa SL",        "pais": "España",    "vinculo": "Vinculada"},
    "VIN-CHN-001": {"nombre": "AutoCorp Parts Asia Ltd",   "pais": "China",     "vinculo": "Vinculada"},
    "VIN-USA-001": {"nombre": "AutoCorp Tech LLC",         "pais": "Estados Unidos", "vinculo": "Vinculada"},
}


# ============================================================
# GENERAR CLIENTES (concesionarios + flotas + exportación)
# ============================================================

def generar_clientes(n_nacionales=180, n_exportacion=15):
    clientes = []

    # Concesionarios y clientes nacionales
    jur_list = list(PESO_JUR.keys())
    pesos = list(PESO_JUR.values())
    for i in range(n_nacionales):
        jur = random.choices(jur_list, weights=pesos)[0]
        tipo = random.choices(
            ["concesionario", "flota_corporativa", "organismo_publico"],
            weights=[80, 15, 5]
        )[0]
        clientes.append({
            "cliente_id": f"CLI-N-{i+1:04d}",
            "razon_social": f"{'Concesionario' if tipo=='concesionario' else 'Flota' if tipo=='flota_corporativa' else 'Gobierno'} {jur} {i+1:03d}",
            "tipo": tipo,
            "pais": "Argentina",
            "jurisdiccion_entrega": jur,
            "cuit": f"30-{random.randint(10_000_000, 99_999_999)}-{random.randint(0,9)}",
            "es_parte_relacionada": False,
            "vinculo": None,
        })

    # Clientes de exportación
    paises_export = [("Brasil", 8), ("Uruguay", 3), ("Paraguay", 2), ("Chile", 4), ("Bolivia", 2)]
    for i in range(n_exportacion):
        pais = random.choices([p for p, _ in paises_export], weights=[w for _, w in paises_export])[0]
        # Algunos export son a vinculadas (relevante para PT)
        es_vinc = random.random() < 0.4 and pais == "Brasil"
        clientes.append({
            "cliente_id": f"CLI-X-{i+1:04d}",
            "razon_social": f"AutoCorp {pais}" if es_vinc else f"Distribuidor {pais} {i+1:02d}",
            "tipo": "exportacion",
            "pais": pais,
            "jurisdiccion_entrega": None,
            "cuit": None,
            "es_parte_relacionada": es_vinc,
            "vinculo": "Casa Matriz" if (es_vinc and pais == "Brasil") else ("Vinculada" if es_vinc else None),
        })

    return pd.DataFrame(clientes)


# ============================================================
# GENERAR PROVEEDORES
# ============================================================

def generar_proveedores(n_nacionales=80, n_importacion=25):
    proveedores = []
    jur_list = list(PESO_JUR.keys())
    pesos = list(PESO_JUR.values())

    tipos_nac = ["autopartes", "servicios", "logistica", "energia", "alquileres", "publicidad", "consultoria"]
    for i in range(n_nacionales):
        tipo = random.choice(tipos_nac)
        jur = random.choices(jur_list, weights=pesos)[0]
        proveedores.append({
            "proveedor_id": f"PROV-N-{i+1:04d}",
            "razon_social": f"{tipo.title()} {jur} {i+1:03d} SA",
            "tipo": tipo,
            "pais": "Argentina",
            "jurisdiccion_domicilio": jur,
            "es_parte_relacionada": False,
            "vinculo": None,
        })

    # Proveedores del exterior (incluye casa matriz)
    for i in range(n_importacion):
        if i < 8:  # Primeros 8 son partes relacionadas
            pr_key = random.choice(list(PARTES_RELACIONADAS.keys()))
            pr_info = PARTES_RELACIONADAS[pr_key]
            proveedores.append({
                "proveedor_id": pr_key if i == 0 else f"{pr_key}-{i}",
                "razon_social": pr_info["nombre"],
                "tipo": random.choice(["importacion_partes", "royalties", "management_fee", "servicios_IT"]),
                "pais": pr_info["pais"],
                "jurisdiccion_domicilio": None,
                "es_parte_relacionada": True,
                "vinculo": pr_info["vinculo"],
            })
        else:
            pais = random.choice(["China", "India", "Tailandia", "Alemania", "Italia"])
            proveedores.append({
                "proveedor_id": f"PROV-X-{i+1:04d}",
                "razon_social": f"Supplier {pais} {i+1:03d}",
                "tipo": "importacion_partes",
                "pais": pais,
                "jurisdiccion_domicilio": None,
                "es_parte_relacionada": False,
                "vinculo": None,
            })

    return pd.DataFrame(proveedores)


# ============================================================
# GENERAR EMPLEADOS
# ============================================================

def generar_empleados(n=2000):
    empleados = []
    distribucion = {
        "PL-COR": 600, "PL-PAC": 800, "PL-MZA": 250, "PL-CDA": 200, "SC-CAB": 150,
    }
    areas = ["produccion", "administracion", "ventas", "ingenieria", "logistica", "RRHH", "IT"]
    pesos_area = {
        "PL-COR": [0.75, 0.05, 0.02, 0.10, 0.05, 0.02, 0.01],
        "PL-PAC": [0.78, 0.04, 0.02, 0.10, 0.04, 0.01, 0.01],
        "PL-MZA": [0.80, 0.05, 0.03, 0.08, 0.02, 0.01, 0.01],
        "PL-CDA": [0.10, 0.10, 0.05, 0.05, 0.65, 0.03, 0.02],
        "SC-CAB": [0.00, 0.40, 0.15, 0.10, 0.05, 0.15, 0.15],
    }
    eid = 1
    for planta, cantidad in distribucion.items():
        for _ in range(cantidad):
            area = random.choices(areas, weights=pesos_area[planta])[0]
            sueldo_base = {
                "produccion": 800_000, "administracion": 1_200_000, "ventas": 1_500_000,
                "ingenieria": 1_800_000, "logistica": 900_000, "RRHH": 1_300_000, "IT": 1_700_000,
            }[area]
            empleados.append({
                "empleado_id": f"E-{eid:05d}",
                "planta": planta,
                "jurisdiccion_trabajo": PLANTAS[planta]["jurisdiccion"],
                "area": area,
                "sueldo_anual_bruto": int(sueldo_base * 13 * np.random.uniform(0.85, 1.6)),
            })
            eid += 1
    return pd.DataFrame(empleados)


# ============================================================
# GENERAR VENTAS (libro IVA ventas, ~12.000 facturas/año)
# ============================================================

def generar_ventas(clientes_df, n=12_000):
    ventas = []
    fecha_inicio = date(2025, 1, 1)
    productos_lista = PRODUCTOS

    clientes_nac = clientes_df[clientes_df.tipo != "exportacion"]
    clientes_exp = clientes_df[clientes_df.tipo == "exportacion"]

    for i in range(n):
        # 90% nacionales, 10% exportación
        if random.random() < 0.10:
            cli = clientes_exp.sample(1).iloc[0]
        else:
            cli = clientes_nac.sample(1).iloc[0]

        # Producto random ponderado por segmento (autos+motos los más vendidos)
        prod = random.choices(productos_lista, weights=[
            3 if p["segmento"] == "AUT" else
            1 if p["segmento"] == "PKP" else
            1.5 if p["segmento"] == "UTI" else
            4 for p in productos_lista
        ])[0]

        cantidad = random.choices([1, 2, 3, 5, 10, 20], weights=[60, 15, 10, 8, 5, 2])[0]
        # Precios de export a vinculadas tienden a ser más bajos (caso PT clásico!)
        if cli.es_parte_relacionada:
            precio_unit = prod["precio_lista"] * np.random.uniform(0.78, 0.90)
        elif cli.tipo == "exportacion":
            precio_unit = prod["precio_lista"] * np.random.uniform(0.88, 0.98)
        else:
            precio_unit = prod["precio_lista"] * np.random.uniform(0.95, 1.05)

        # Planta de origen según producto
        planta_origen = next(p for p, info in PLANTAS.items() if prod["segmento"] in info["produce"])

        ventas.append({
            "factura_id": f"FV-{i+1:06d}",
            "fecha": fecha_inicio + timedelta(days=random.randint(0, 364)),
            "cliente_id": cli.cliente_id,
            "sku": prod["sku"],
            "segmento": prod["segmento"],
            "planta_origen": planta_origen,
            "cantidad": cantidad,
            "precio_unitario": round(precio_unit, 2),
            "monto_total": round(precio_unit * cantidad, 2),
            "es_exportacion": cli.tipo == "exportacion",
            "es_a_vinculada": cli.es_parte_relacionada,
            "jurisdiccion_entrega": cli.jurisdiccion_entrega,
            "pais_destino": cli.pais if cli.tipo == "exportacion" else "Argentina",
        })

    return pd.DataFrame(ventas)


# ============================================================
# GENERAR COMPRAS / GASTOS (~8000 asientos/año)
# ============================================================

CUENTAS_GASTO = {
    # cuenta : (descripcion, tipo, computable_CM, asignable_a_segmento, prorrateo_default)
    51001: ("Materia prima - aceros y chapa",           "MP_directa",    True,  True,  "directa"),
    51002: ("Materia prima - motores y transmisión",    "MP_directa",    True,  True,  "directa"),
    51003: ("Materia prima - electrónica",              "MP_directa",    True,  True,  "directa"),
    51100: ("Importación CKDs - casa matriz",           "MP_importada",  True,  True,  "directa"),
    51200: ("Autopartes nacionales",                    "MP_directa",    True,  True,  "directa"),
    61500: ("Energía eléctrica plantas",                "energia",       True,  True,  "kg_consumo"),
    61510: ("Gas natural plantas",                      "energia",       True,  True,  "kg_consumo"),
    61600: ("Mantenimiento equipos productivos",        "mantenimiento", True,  True,  "horas_maquina"),
    63100: ("Fletes nacionales",                        "logistica",     True,  False, "destino"),
    63200: ("Fletes exportación",                       "logistica",     True,  False, "origen"),
    63300: ("Almacenaje y logística inversa",           "logistica",     True,  False, "directa"),
    64100: ("Marketing y publicidad nacional",          "comercial",     True,  False, "ingresos"),
    64200: ("Comisiones a concesionarios",              "comercial",     True,  False, "ingresos_nacionales"),
    64300: ("Marketing institucional",                  "comercial",     True,  False, "decision_manual"),
    71100: ("Sueldos producción",                       "personal",      True,  True,  "empleados"),
    71200: ("Sueldos administración",                   "personal",      True,  False, "empleados"),
    71300: ("Sueldos comerciales",                      "personal",      True,  False, "empleados"),
    71400: ("Cargas sociales",                          "personal",      True,  False, "empleados"),
    72100: ("Alquileres oficinas",                      "estructura",    True,  False, "sucursal"),
    72200: ("Servicios públicos oficinas",              "estructura",    True,  False, "sucursal"),
    73100: ("Honorarios profesionales locales",         "servicios",     True,  False, "directa"),
    73200: ("Royalties casa matriz",                    "servicios_ext", False, True,  "ingresos"),
    73300: ("Management fees casa matriz",              "servicios_ext", False, False, "ingresos"),
    73400: ("Servicios IT casa matriz",                 "servicios_ext", False, False, "empleados"),
    74100: ("Intereses préstamos bancarios",            "financiero",    False, False, "no_computable"),
    74200: ("Diferencias de cambio",                    "financiero",    False, False, "no_computable"),
    75100: ("Amortizaciones plantas",                   "amortizaciones", True, True,  "horas_maquina"),
    75200: ("Amortizaciones edificios admin",           "amortizaciones", True, False, "sucursal"),
    79100: ("Otros gastos operativos",                  "otros",         True,  False, "ingresos"),
}

def generar_gastos(proveedores_df, n=8000):
    gastos = []
    fecha_inicio = date(2025, 1, 1)
    cuentas_lista = list(CUENTAS_GASTO.keys())
    # Distribución no uniforme: MP_directa y personal son los más frecuentes
    pesos_cuenta = []
    for c in cuentas_lista:
        tipo = CUENTAS_GASTO[c][1]
        if tipo == "MP_directa":         pesos_cuenta.append(8)
        elif tipo == "MP_importada":     pesos_cuenta.append(4)
        elif tipo == "personal":         pesos_cuenta.append(5)
        elif tipo == "logistica":        pesos_cuenta.append(3)
        elif tipo == "comercial":        pesos_cuenta.append(3)
        elif tipo == "energia":          pesos_cuenta.append(2)
        elif tipo == "servicios_ext":    pesos_cuenta.append(2)
        elif tipo == "amortizaciones":   pesos_cuenta.append(2)
        else:                            pesos_cuenta.append(1)

    for i in range(n):
        cuenta = random.choices(cuentas_lista, weights=pesos_cuenta)[0]
        desc, tipo, _, _, _ = CUENTAS_GASTO[cuenta]

        # Elegir proveedor consistente con el tipo de gasto
        if tipo in ("MP_importada", "servicios_ext"):
            prov_pool = proveedores_df[proveedores_df.es_parte_relacionada]
        elif tipo == "MP_directa":
            prov_pool = proveedores_df[
                (proveedores_df.tipo == "autopartes") |
                ((proveedores_df.pais != "Argentina") & ~proveedores_df.es_parte_relacionada)
            ]
        elif tipo == "personal":
            prov_pool = None
        elif tipo == "logistica":
            prov_pool = proveedores_df[proveedores_df.tipo == "logistica"]
        elif tipo == "energia":
            prov_pool = proveedores_df[proveedores_df.tipo == "energia"]
        elif tipo == "estructura":
            prov_pool = proveedores_df[proveedores_df.tipo.isin(["alquileres", "servicios"])]
        else:
            prov_pool = proveedores_df[proveedores_df.tipo.isin(["consultoria", "servicios", "publicidad"])]

        if prov_pool is None or len(prov_pool) == 0:
            prov = proveedores_df.sample(1).iloc[0]
        else:
            prov = prov_pool.sample(1).iloc[0]

        # Monto según tipo
        if tipo == "MP_directa":          monto = np.random.uniform(500_000, 50_000_000)
        elif tipo == "MP_importada":      monto = np.random.uniform(10_000_000, 200_000_000)
        elif tipo == "personal":          monto = np.random.uniform(2_000_000, 30_000_000)
        elif tipo == "energia":           monto = np.random.uniform(3_000_000, 25_000_000)
        elif tipo == "logistica":         monto = np.random.uniform(500_000, 8_000_000)
        elif tipo == "comercial":         monto = np.random.uniform(1_000_000, 20_000_000)
        elif tipo == "servicios_ext":     monto = np.random.uniform(5_000_000, 100_000_000)
        elif tipo == "amortizaciones":    monto = np.random.uniform(2_000_000, 15_000_000)
        elif tipo == "financiero":        monto = np.random.uniform(1_000_000, 50_000_000)
        else:                             monto = np.random.uniform(200_000, 5_000_000)

        # Planta/sucursal asociada (si aplica)
        if tipo in ("MP_directa", "MP_importada", "energia", "mantenimiento", "personal") and "produccion" in desc.lower() or tipo in ("MP_directa", "MP_importada", "energia"):
            planta = random.choices(
                ["PL-COR", "PL-PAC", "PL-MZA"],
                weights=[35, 50, 15]
            )[0]
        elif tipo == "personal":
            if "producción" in desc.lower() or "produccion" in desc.lower():
                planta = random.choices(["PL-COR", "PL-PAC", "PL-MZA"], weights=[35, 50, 15])[0]
            else:
                planta = random.choices(["SC-CAB", "PL-CDA"], weights=[70, 30])[0]
        elif tipo == "estructura":
            planta = "SC-CAB"
        else:
            planta = None

        gastos.append({
            "asiento_id": f"AS-{i+1:06d}",
            "fecha": fecha_inicio + timedelta(days=random.randint(0, 364)),
            "cuenta": cuenta,
            "descripcion_cuenta": desc,
            "tipo_gasto": tipo,
            "proveedor_id": prov.proveedor_id,
            "proveedor_nombre": prov.razon_social,
            "proveedor_pais": prov.pais,
            "es_a_vinculada": bool(prov.es_parte_relacionada),
            "planta_imputacion": planta,
            "monto": round(monto, 2),
        })

    return pd.DataFrame(gastos)


# ============================================================
# DATOS AUXILIARES
# ============================================================

def generar_produccion_anual():
    """Volúmenes producidos por planta y segmento."""
    return pd.DataFrame([
        {"planta": "PL-COR", "segmento": "AUT", "unidades_producidas": 18_500, "kg_consumo_energia": 2_400_000, "horas_maquina": 380_000},
        {"planta": "PL-PAC", "segmento": "PKP", "unidades_producidas": 12_200, "kg_consumo_energia": 2_800_000, "horas_maquina": 410_000},
        {"planta": "PL-PAC", "segmento": "UTI", "unidades_producidas":  8_500, "kg_consumo_energia": 1_400_000, "horas_maquina": 220_000},
        {"planta": "PL-MZA", "segmento": "MOT", "unidades_producidas": 35_000, "kg_consumo_energia":   650_000, "horas_maquina": 140_000},
    ])


def generar_estructura_sucursales():
    return pd.DataFrame([
        {"planta": "PL-COR", "jurisdiccion": "Córdoba",       "metros2": 85_000, "tipo": "produccion"},
        {"planta": "PL-PAC", "jurisdiccion": "Buenos Aires",  "metros2": 120_000, "tipo": "produccion"},
        {"planta": "PL-MZA", "jurisdiccion": "Mendoza",       "metros2": 30_000, "tipo": "produccion"},
        {"planta": "PL-CDA", "jurisdiccion": "Buenos Aires",  "metros2": 15_000, "tipo": "distribucion"},
        {"planta": "SC-CAB", "jurisdiccion": "CABA",          "metros2":  8_000, "tipo": "administracion"},
    ])


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    import os
    OUT = "/home/claude/automotriz_demo/data"
    os.makedirs(OUT, exist_ok=True)

    print("Generando catálogos...")
    pd.DataFrame([{**v, "planta": k} for k, v in PLANTAS.items()]).to_excel(f"{OUT}/plantas.xlsx", index=False)
    pd.DataFrame(PRODUCTOS).to_excel(f"{OUT}/productos.xlsx", index=False)
    pd.DataFrame([{**v, "id": k} for k, v in PARTES_RELACIONADAS.items()]).to_excel(f"{OUT}/partes_relacionadas.xlsx", index=False)

    print("Generando clientes...")
    clientes = generar_clientes()
    clientes.to_excel(f"{OUT}/clientes.xlsx", index=False)

    print("Generando proveedores...")
    proveedores = generar_proveedores()
    proveedores.to_excel(f"{OUT}/proveedores.xlsx", index=False)

    print("Generando empleados...")
    empleados = generar_empleados()
    empleados.to_excel(f"{OUT}/empleados.xlsx", index=False)

    print("Generando ventas (12.000 facturas)...")
    ventas = generar_ventas(clientes)
    ventas.to_excel(f"{OUT}/ventas.xlsx", index=False)

    print("Generando gastos (8.000 asientos)...")
    gastos = generar_gastos(proveedores)
    gastos.to_excel(f"{OUT}/gastos.xlsx", index=False)

    print("Generando auxiliares...")
    generar_produccion_anual().to_excel(f"{OUT}/produccion_anual.xlsx", index=False)
    generar_estructura_sucursales().to_excel(f"{OUT}/sucursales.xlsx", index=False)

    print(f"\n✓ Datasets generados en {OUT}")
    print(f"  Ventas: {len(ventas):,} filas, total ${ventas.monto_total.sum()/1e9:.2f}B")
    print(f"  Gastos: {len(gastos):,} filas, total ${gastos.monto.sum()/1e9:.2f}B")
    print(f"  Empleados: {len(empleados):,}")
    print(f"  Clientes: {len(clientes):,} ({(clientes.tipo=='exportacion').sum()} export)")
    print(f"  Proveedores: {len(proveedores):,} ({proveedores.es_parte_relacionada.sum()} vinculados)")
