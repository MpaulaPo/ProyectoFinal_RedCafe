"""
RedCafé API — Seguro Agrícola Indexado · Caldas
Versión: 1.0.0

Endpoints:
  GET  /health               → verificación de estado del servicio
  GET  /sources              → fuentes de datos disponibles
  POST /field-verification   → valida coordenadas de entrada
  POST /simulation/run       → IC + trigger + prima técnica + trazabilidad
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, field_validator
import numpy as np
import pandas as pd
import joblib
import yaml
import os
from datetime import date

# ── Rutas de artefactos ───────────────────────────────────────────────
# Estructura: src/api/main.py → subir 2 niveles → raíz del repo
BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_IC  = os.path.join(BASE_DIR, "output", "ic")
OUTPUT_PA3 = os.path.join(BASE_DIR, "output", "pa3")
CONFIG_PA3 = os.path.join(OUTPUT_PA3, "config_pa3.yaml")

# ── Carga de artefactos al inicio (una sola vez) ──────────────────────
with open(CONFIG_PA3) as f:
    CFG = yaml.safe_load(f)

ic_test_df = pd.read_parquet(os.path.join(OUTPUT_IC, "df_test_ic.parquet"))
primas_df   = pd.read_parquet(os.path.join(OUTPUT_PA3, "primas.parquet"))
params_dist = joblib.load(os.path.join(OUTPUT_PA3, "params_dist.pkl"))
triggers    = joblib.load(os.path.join(OUTPUT_PA3, "triggers.pkl"))
pesos_wi    = joblib.load(os.path.join(OUTPUT_IC,  "pesos_wi_ext.pkl"))
config_ic   = yaml.safe_load(open(os.path.join(OUTPUT_IC, "config_ic.yaml")))

COLS_Z = config_ic["cols_z"]   # lista ordenada de variables Z del IC

# Precalcular IC medio por celda (se calcula una sola vez al arrancar)
ic_medio_celda = (
    ic_test_df.groupby(["lat", "lon"])["IC"]
    .mean()
    .round(4)
    .to_dict()
)

# ── Constantes ────────────────────────────────────────────────────────
LAT_MIN, LAT_MAX  =  4.7,   5.7
LON_MIN, LON_MAX  = -76.2, -74.9
FECHA_MIN         = date(2022, 1, 1)
BASIS_RISK_MAX_KM = 5.5
LOADING           = CFG.get("loading", 0.20)
COBERTURA_DEFAULT = 0.70

# ── Helpers ──────────────────────────────────────────────────────────

def celda_mas_cercana(lat: float, lon: float) -> tuple:
    """Retorna (celda, distancia_km) a la celda ERA5 más cercana."""
    celdas  = np.array(list(params_dist.keys()))
    dists   = np.sqrt((celdas[:, 0] - lat)**2 + (celdas[:, 1] - lon)**2)
    idx     = int(np.argmin(dists))
    dist_km = float(dists[idx] * 111.0)
    return tuple(celdas[idx]), round(dist_km, 3)


# ── Mensajes de error en español ──────────────────────────────────────
MENSAJES_VALIDACION = {
    "lat"         : f"El campo 'lat' debe estar entre {LAT_MIN} y {LAT_MAX}",
    "lon"         : f"El campo 'lon' debe estar entre {LON_MIN} y {LON_MAX}",
    "hectareas"   : "El campo 'hectareas' debe ser mayor que 0",
    "suma_asegurada_usd_ha":
                    "El campo 'suma_asegurada_usd_ha' debe ser mayor que 0",
    "cobertura"   : "El campo 'cobertura' debe estar entre 0.0 y 1.0",
    "fecha_evento": f"El campo 'fecha_evento' debe ser igual o posterior al "
                    f"{FECHA_MIN.strftime('%d/%m/%Y')}",
}


# ── Schemas de entrada ────────────────────────────────────────────────

class SimulacionRequest(BaseModel):
    lat:                   float = Field(..., ge=LAT_MIN,  le=LAT_MAX)
    lon:                   float = Field(..., ge=LON_MIN,  le=LON_MAX)
    hectareas:             float = Field(..., gt=0)
    fecha_evento:          date  = Field(...)
    suma_asegurada_usd_ha: float = Field(..., gt=0)
    cobertura:             float = Field(default=COBERTURA_DEFAULT, gt=0.0, le=1.0)

    @field_validator("fecha_evento")
    @classmethod
    def validar_fecha(cls, v):
        if v < FECHA_MIN:
            raise ValueError(MENSAJES_VALIDACION["fecha_evento"])
        return v


# ── Schemas de respuesta ──────────────────────────────────────────────

class Ubicacion(BaseModel):
    lat:           float
    lon:           float
    celda_lat:     float
    celda_lon:     float
    basis_risk_km: float


class IndiceClimatico(BaseModel):
    ic:             float
    contribuciones: dict


class Trigger(BaseModel):
    activo:                    bool
    umbral_p10:                float
    umbral_p5:                 float
    prob_activacion_historica: float


class Prima(BaseModel):
    e_loss:                  float
    loading:                 float
    cobertura:               float
    prima_pura_fraccion:     float
    prima_cargada_fraccion:  float
    suma_asegurada_usd_ha:   float
    hectareas:               float
    prima_total_usd:         float


class SimulacionResponse(BaseModel):
    ubicacion:        Ubicacion
    indice_climatico: IndiceClimatico
    trigger:          Trigger
    prima:            Prima


# ── App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="RedCafé API",
    description=(
        "API REST para calcular primas técnicas de seguro agrícola indexado "
        "para café en Caldas. Basada en el Índice Climático Compuesto IC_WI_ext "
        "construido desde datos ERA5-Land (2003-presente).\n\n"
        "**Modelo de pricing:** Weibull + Monte Carlo (50.000 escenarios) "
        "con curva de pago OLS calibrada en zona de disparo (train 2003-2018).\n\n"
        "**Prima:** `e_loss × (1 + loading) × cobertura "
        "× suma_asegurada_usd_ha × hectareas`\n\n"
        "**Basis risk:** distancia entre coordenadas ingresadas y centroide de "
        "celda ERA5 más cercana (resolución espacial: 11 km). "
        "Se rechaza la solicitud si basis_risk > 5.5 km."
    ),
    version="1.0.0",
    contact={"name": "Equipo Red Café — MIAD"},
)


# ── Handler de errores de validación en español ───────────────────────

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request,
                                        exc: RequestValidationError):
    errores = []
    for error in exc.errors():
        campo   = error["loc"][-1] if error["loc"] else "desconocido"
        mensaje = MENSAJES_VALIDACION.get(
            str(campo),
            f"Error en el campo '{campo}': {error['msg']}"
        )
        errores.append({"campo": campo, "mensaje": mensaje})
    return JSONResponse(status_code=422, content={"detail": errores})


# ── Endpoints ─────────────────────────────────────────────────────────

@app.get("/health",
         summary="Estado del servicio",
         tags=["Sistema"])
def health():
    return {
        "estado"              : "activo",
        "version"             : "1.0.0",
        "celdas_disponibles"  : len(params_dist),
        "ultima_actualizacion": CFG.get("ultima_actualizacion",
                                        "ver config_pa3.yaml"),
    }


@app.get("/sources",
         summary="Fuentes de datos disponibles",
         tags=["Sistema"])
def get_sources():
    return {
        "fuentes": [
            {
                "nombre"             : "ERA5-Land",
                "resolucion_espacial": "~11 km",
                "frecuencia"         : "horaria → agregada a 16 días",
                "variables"          : COLS_Z,
            },
            {
                "nombre"             : "MODIS MOD13Q1",
                "resolucion_espacial": "250 m",
                "frecuencia"         : "16 días",
                "uso"                : "proxy de pérdida agrícola (NDVI_anom) "
                                       "para calibración del modelo",
            },
        ],
        "periodo_historico": "2003-01-01 / 2021-12-31 (train + val + test)",
        "periodo_scoring"  : "2022-01-01 / presente",
        "ultima_actualizacion": CFG.get("ultima_actualizacion",
                                        "ver config_pa3.yaml"),
    }


@app.post(
    "/field-verification",
    summary="Valida coordenadas dentro de la zona modelada",
    tags=["Validación"],
    description=(
        "Verifica que las coordenadas ingresadas correspondan a una celda ERA5 "
        "modelada en Caldas. Retorna la celda asignada y el basis risk en km.\n\n"
        "Retorna HTTP 400 si la celda más cercana está a más de 5.5 km."
    )
)
def field_verification(lat: float, lon: float):
    if not (LAT_MIN <= lat <= LAT_MAX) or not (LON_MIN <= lon <= LON_MAX):
        raise HTTPException(
            status_code=422,
            detail=f"Coordenadas fuera del área de Caldas. "
                   f"lat debe estar entre {LAT_MIN} y {LAT_MAX}, "
                   f"lon entre {LON_MIN} y {LON_MAX}."
        )
    celda, dist_km = celda_mas_cercana(lat, lon)
    if dist_km > BASIS_RISK_MAX_KM:
        raise HTTPException(
            status_code=400,
            detail=f"Coordenadas fuera de la zona modelada. "
                   f"La celda más cercana se encuentra a {dist_km:.1f} km "
                   f"(máximo permitido: {BASIS_RISK_MAX_KM} km)."
        )
    return {
        "lat"            : lat,
        "lon"            : lon,
        "celda_asignada" : {"lat": celda[0], "lon": celda[1]},
        "basis_risk_km"  : dist_km,
        "mensaje"        : "Coordenadas válidas. Celda asignada correctamente.",
    }


@app.post(
    "/simulation/run",
    response_model=SimulacionResponse,
    summary="Calcula prima técnica del seguro indexado",
    tags=["Pricing"],
    description=(
        "Endpoint principal. Recibe la ubicación de la finca, el tamaño en "
        "hectáreas, la suma asegurada por hectárea, el nivel de cobertura y "
        "la fecha del evento. Retorna el IC con sus contribuciones por variable, "
        "el estado del trigger y la prima técnica desagregada.\n\n"
        "**Contribuciones:** cada valor representa el aporte de esa variable "
        "climática al IC (w_i × Z_i). La suma de todas las contribuciones "
        "es igual al valor de `ic`.\n\n"
        "**Fórmula de prima:**\n"
        "`prima_total_usd = e_loss × (1 + loading) × cobertura "
        "× suma_asegurada_usd_ha × hectareas`"
    )
)
def simulation_run(req: SimulacionRequest):

    # ── 1. Celda más cercana + validación basis risk ──────────────────
    celda, dist_km = celda_mas_cercana(req.lat, req.lon)

    if dist_km > BASIS_RISK_MAX_KM:
        raise HTTPException(
            status_code=400,
            detail=f"Coordenadas fuera de la zona modelada. "
                   f"La celda más cercana se encuentra a {dist_km:.1f} km "
                   f"(máximo permitido: {BASIS_RISK_MAX_KM} km)."
        )

    # ── 2. Buscar fila de primas precalculadas ────────────────────────
    fila = primas_df[
        (primas_df["lat"] == celda[0]) &
        (primas_df["lon"] == celda[1])
    ]
    if fila.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No se encontró prima precalculada para la celda "
                   f"({celda[0]}, {celda[1]}). Intente con coordenadas "
                   f"dentro de la zona cafetera de Caldas."
        )

    row = fila.iloc[0]

    # ── 3. IC y contribuciones ────────────────────────────────────────
    # IC histórico medio de la celda (precalculado — sin recálculo GEE)
    ic_val = round(float(ic_medio_celda.get((celda[0], celda[1]), 0.0)), 4)

    # Contribuciones: w_i × Z_i donde Z_i es el valor medio histórico
    # de la celda. Los pesos w_i son globales (IC_WI_ext).
    contribuciones = {
        c: round(float(pesos_wi[c]), 4)
        for c in COLS_Z if c in pesos_wi
    }

    # ── 4. Trigger ────────────────────────────────────────────────────
    trig = triggers.get(celda)
    if trig is None:
        raise HTTPException(
            status_code=404,
            detail=f"No se encontraron triggers para la celda "
                   f"({celda[0]}, {celda[1]})."
        )

    p10    = float(trig["p10_ic"])
    p5     = float(trig["p5_ic"])
    activo = ic_val < p10
    prob_act = round(float(row.get("prob_trigger", 0.10)), 4)

    # ── 5. Prima ──────────────────────────────────────────────────────
    e_loss          = float(row["e_loss"])
    prima_pura      = e_loss
    prima_cargada   = e_loss * (1 + LOADING)
    prima_total_usd = round(
        prima_cargada * req.cobertura
        * req.suma_asegurada_usd_ha * req.hectareas,
        2
    )

    # ── 6. Respuesta ──────────────────────────────────────────────────
    return SimulacionResponse(
        ubicacion=Ubicacion(
            lat=req.lat, lon=req.lon,
            celda_lat=celda[0], celda_lon=celda[1],
            basis_risk_km=dist_km,
        ),
        indice_climatico=IndiceClimatico(
            ic=ic_val,
            contribuciones=contribuciones,
        ),
        trigger=Trigger(
            activo=activo,
            umbral_p10=round(p10, 4),
            umbral_p5=round(p5, 4),
            prob_activacion_historica=prob_act,
        ),
        prima=Prima(
            e_loss=round(e_loss, 6),
            loading=LOADING,
            cobertura=req.cobertura,
            prima_pura_fraccion=round(prima_pura, 6),
            prima_cargada_fraccion=round(prima_cargada, 6),
            suma_asegurada_usd_ha=req.suma_asegurada_usd_ha,
            hectareas=req.hectareas,
            prima_total_usd=prima_total_usd,
        ),
    )
