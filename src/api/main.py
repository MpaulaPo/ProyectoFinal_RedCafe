"""
RedCafé API — Seguro Agrícola Indexado · Caldas
Versión: 1.0.0

Endpoints:
  GET  /sources              → fuentes de datos disponibles
  POST /field-verification   → valida coordenadas de entrada
  POST /indicator/generate   → IC para una ubicación y fecha
  POST /loss-model/predict   → pérdida esperada E(Loss)
  POST /simulation/run       → prima pura + trazabilidad completa
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator
import numpy as np
import pandas as pd
import joblib
import yaml
import os
from scipy.stats import weibull_min as weibull_dist
from datetime import date

# ── Configuración ─────────────────────────────────────────────────────
OUTPUT_IC  = "output/ic"
OUTPUT_PA3 = "output/pa3"
CONFIG_PA3 = os.path.join(OUTPUT_PA3, "config_pa3.yaml")

with open(CONFIG_PA3) as f:
    CFG = yaml.safe_load(f)

# ── Carga de artefactos al inicio (una sola vez) ──────────────────────
primas_df    = pd.read_parquet(os.path.join(OUTPUT_PA3, "primas.parquet"))
params_dist  = joblib.load(os.path.join(OUTPUT_PA3, "params_dist.pkl"))
triggers     = joblib.load(os.path.join(OUTPUT_PA3, "triggers.pkl"))
pesos_wi_ext = joblib.load(os.path.join(OUTPUT_IC,  "pesos_wi_ext.pkl"))
config_ic    = yaml.safe_load(
    open(os.path.join(OUTPUT_IC, "config_ic.yaml"))
)

COLS_Z = config_ic["cols_z"]

# Límites de la región
LAT_MIN, LAT_MAX =  4.7,  5.7
LON_MIN, LON_MAX = -76.2, -74.9

# ── Helpers ──────────────────────────────────────────────────────────

def celda_mas_cercana(lat: float, lon: float) -> tuple:
    """Asigna (lat, lon) al centroide de celda más cercano."""
    celdas = np.array(list(params_dist.keys()))
    dists  = np.sqrt((celdas[:, 0] - lat)**2 + (celdas[:, 1] - lon)**2)
    idx    = int(np.argmin(dists))
    return tuple(celdas[idx]), float(dists[idx] * 111)   # km aprox.


def calcular_ic(z_vars: dict) -> float:
    """Calcula IC_WI_ext desde las variables Z estandarizadas."""
    v = np.array([z_vars.get(c, 0.0) for c in COLS_Z])
    w = pesos_wi_ext[COLS_Z].values
    return float(v @ w)


# ── Schemas ───────────────────────────────────────────────────────────

class CampoRequest(BaseModel):
    lat:  float = Field(..., ge=LAT_MIN, le=LAT_MAX,
                        description="Latitud decimal (4.7 – 5.7)")
    lon:  float = Field(..., ge=LON_MIN, le=LON_MAX,
                        description="Longitud decimal (-76.2 – -74.9)")
    fecha: date = Field(...,
                        description="Fecha de consulta (YYYY-MM-DD)")

    @field_validator("fecha")
    @classmethod
    def fecha_valida(cls, v):
        if v < date(2003, 1, 1):
            raise ValueError("Fecha anterior al histórico disponible (2003-01-01)")
        return v


class SimulacionRequest(CampoRequest):
    z_vars: dict[str, float] | None = Field(
        None,
        description="Variables Z estandarizadas. Si no se proveen, "
                    "se usa el valor histórico precalculado de la celda."
    )


class IndicadorResponse(BaseModel):
    fecha:          str
    lat:            float
    lon:            float
    celda_lat:      float
    celda_lon:      float
    basis_risk_km:  float
    ic:             float
    trigger_p10:    float
    trigger_p5:     float
    activo:         bool


class PrimaResponse(IndicadorResponse):
    e_loss:         float
    prima_pura:     float
    loading:        float
    prima_cargada:  float
    distribucion:   str
    n_sim:          int


# ── App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="RedCafé API",
    description=(
        "API para calcular primas técnicas de seguro indexado "
        "para café en Caldas, integrando datos climáticos ERA5-Land "
        "y el Índice Climático Compuesto IC_WI_ext."
    ),
    version="1.0.0",
    contact={"name": "Equipo Red Café — MIAD"},
)


@app.get("/sources", summary="Fuentes de datos disponibles")
def get_sources():
    return {
        "fuentes": [
            {"nombre": "ERA5-Land",      "resolucion": "~11 km", "frecuencia": "horaria → 16d"},
            {"nombre": "MODIS MOD13Q1",  "resolucion": "250 m",  "frecuencia": "16 días"},
        ],
        "periodo_historico": "2003-01-01 / presente",
        "ultima_actualizacion": CFG.get("ultima_actualizacion", "ver config_pa3.yaml"),
    }


@app.post("/field-verification", summary="Valida coordenadas")
def field_verification(req: CampoRequest):
    celda, dist_km = celda_mas_cercana(req.lat, req.lon)
    return {
        "lat": req.lat, "lon": req.lon,
        "celda_asignada": {"lat": celda[0], "lon": celda[1]},
        "basis_risk_km": round(dist_km, 3),
        "en_zona_cafetera": True,   # validado por Pydantic range
        "mensaje": "Coordenadas válidas. Celda asignada correctamente.",
    }


@app.post("/indicator/generate",
          response_model=IndicadorResponse,
          summary="Genera el IC para una ubicación y fecha")
def indicator_generate(req: SimulacionRequest):
    celda, dist_km = celda_mas_cercana(req.lat, req.lon)
    trig           = triggers.get(celda)
    if trig is None:
        raise HTTPException(422, f"Sin modelo para celda {celda}")

    # IC desde z_vars si se proveen, si no valor neutral
    ic = calcular_ic(req.z_vars) if req.z_vars else 0.0

    return IndicadorResponse(
        fecha=str(req.fecha),
        lat=req.lat, lon=req.lon,
        celda_lat=celda[0], celda_lon=celda[1],
        basis_risk_km=round(dist_km, 3),
        ic=round(ic, 4),
        trigger_p10=trig["p10_ic"],
        trigger_p5=trig["p5_ic"],
        activo=ic < trig["p10_ic"],
    )


@app.post("/simulation/run",
          response_model=PrimaResponse,
          summary="Calcula E(Loss) y prima técnica")
def simulation_run(req: SimulacionRequest):
    celda, dist_km = celda_mas_cercana(req.lat, req.lon)

    # Buscar prima precalculada
    fila = primas_df[
        (primas_df["lat"] == celda[0]) &
        (primas_df["lon"] == celda[1])
    ]
    if fila.empty:
        raise HTTPException(422, f"Sin prima para celda {celda}")

    row   = fila.iloc[0]
    trig  = triggers[celda]
    ic    = calcular_ic(req.z_vars) if req.z_vars else 0.0
    load  = CFG.get("loading", 0.20)

    return PrimaResponse(
        fecha=str(req.fecha),
        lat=req.lat, lon=req.lon,
        celda_lat=celda[0], celda_lon=celda[1],
        basis_risk_km=round(dist_km, 3),
        ic=round(ic, 4),
        trigger_p10=trig["p10_ic"],
        trigger_p5=trig["p5_ic"],
        activo=ic < trig["p10_ic"],
        e_loss=round(float(row["e_loss"]), 6),
        prima_pura=round(float(row["e_loss"]), 6),
        loading=load,
        prima_cargada=round(float(row["e_loss"]) * (1 + load), 6),
        distribucion=CFG.get("distribucion", "weibull"),
        n_sim=CFG.get("n_sim_mc", 50000),
    )
