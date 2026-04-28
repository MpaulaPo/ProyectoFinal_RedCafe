"""
01_descarga_gee.py — Descarga ERA5-Land + MODIS desde Google Earth Engine

Este script es llamado por DVC en la etapa 'descarga' del pipeline.
Genera:
  data/raw/ERA5_Caldas/   → archivos .tif por fecha y variable
  data/raw/MODIS_Caldas/  → archivos .tif NDVI por período de 16 días

La versión completa de este script se añadirá cuando el equipo
entregue el notebook de descarga actual (04_Descarga_GEE.ipynb).
"""

import ee
import yaml
import os
from datetime import datetime

# ── Cargar parámetros ──────────────────────────────────────────────────
with open("config/params.yaml") as f:
    PARAMS = yaml.safe_load(f)

FECHA_INICIO = PARAMS["fecha_inicio"]
FECHA_FIN    = PARAMS["fecha_fin"]
REGION = ee.Geometry.Rectangle([
    PARAMS["lon_min"], PARAMS["lat_min"],
    PARAMS["lon_max"], PARAMS["lat_max"]
])

os.makedirs("data/raw/ERA5_Caldas",  exist_ok=True)
os.makedirs("data/raw/MODIS_Caldas", exist_ok=True)

# ── Inicializar GEE ────────────────────────────────────────────────────
try:
    ee.Initialize()
except Exception:
    ee.Authenticate()
    ee.Initialize()

print(f"[GEE] Período: {FECHA_INICIO} → {FECHA_FIN}")
print("[GEE] Pendiente: insertar lógica de descarga del notebook original")

# TODO: mover aquí el código del notebook 04_Descarga_GEE.ipynb
# cuando esté disponible.
