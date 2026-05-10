"""
RedCafé API — Seguro Agrícola Indexado · Caldas
Versión: 1.0.0

Endpoints:
  GET  /health               → verificación de estado del servicio
  GET  /sources              → fuentes de datos disponibles
  POST /field-verification   → valida coordenadas de entrada
  POST /policy/quote         → cotización de la póliza (prima en USD)
  POST /event/check          → verificación de pago por evento climático
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
# main.py está en src/api/ → subir 3 niveles → raíz del repo
BASE_DIR   = os.path.dirname(os.path.dirname(os.path.dirname(
               os.path.abspath(__file__))))
OUTPUT_IC  = os.path.join(BASE_DIR, "output", "ic")
OUTPUT_PA3 = os.path.join(BASE_DIR, "output", "pa3")
CONFIG_PA3 = os.path.join(OUTPUT_PA3, "config_pa3.yaml")

# ── Carga de artefactos al inicio (una sola vez) ──────────────────────
with open(CONFIG_PA3) as f:
    CFG = yaml.safe_load(f)

ic_test_df  = pd.read_parquet(os.path.join(OUTPUT_IC,  "df_test_ic.parquet"))
primas_df   = pd.read_parquet(os.path.join(OUTPUT_PA3, "primas.parquet"))
params_dist = joblib.load(os.path.join(OUTPUT_PA3, "params_dist.pkl"))
triggers    = joblib.load(os.path.join(OUTPUT_PA3, "triggers.pkl"))
pesos_wi    = joblib.load(os.path.join(OUTPUT_IC,  "pesos_wi_ext.pkl"))
curva_pago  = joblib.load(os.path.join(OUTPUT_PA3, "curva_pago.pkl"))
config_ic   = yaml.safe_load(open(os.path.join(OUTPUT_IC, "config_ic.yaml")))

COLS_Z = config_ic["cols_z"]

# Parámetros de la curva de pago OLS
CP_ALPHA  = float(curva_pago["alpha"])
CP_BETA   = float(curva_pago["beta"])
CP_BETA2  = float(curva_pago.get("beta2", 0.0))
CP_PM     = float(curva_pago["payout_max"])        # en escala z-score
CP_TIPO   = curva_pago.get("tipo", "OLS_lineal")

# Asegurar que la columna fecha es datetime para el lookup por período
ic_test_df["fecha"] = pd.to_datetime(ic_test_df["fecha"])

# ── Constantes ────────────────────────────────────────────────────────
LAT_MIN, LAT_MAX  =  4.7,   5.7
LON_MIN, LON_MAX  = -76.2, -74.9
FECHA_MIN         = date(2022, 1, 1)
FECHA_MAX         = date(2026, 4, 17)   # ← actualizar cada 16 días
ORIGEN_PERIODOS   = pd.Timestamp("2003-01-01")
BASIS_RISK_MAX_KM = 5.5
COBERTURA_DEFAULT = 0.70
LOADING_DEFAULT   = 0.20

# ── Helpers ──────────────────────────────────────────────────────────

def celda_mas_cercana(lat: float, lon: float) -> tuple:
    """Retorna (celda, distancia_km) a la celda ERA5 más cercana."""
    celdas  = np.array(list(params_dist.keys()))
    dists   = np.sqrt((celdas[:, 0] - lat)**2 + (celdas[:, 1] - lon)**2)
    idx     = int(np.argmin(dists))
    dist_km = float(dists[idx] * 111.0)
    return tuple(celdas[idx]), round(dist_km, 3)


def fecha_a_periodo(fecha_evento: date) -> pd.Timestamp:
    """
    Retorna la fecha de inicio del período de 16 días que contiene
    la fecha_evento, usando los mismos slots que ERA5 (desde 2003-01-01).
    """
    dias = (pd.Timestamp(fecha_evento) - ORIGEN_PERIODOS).days
    slot = (dias // 16) * 16
    return ORIGEN_PERIODOS + pd.Timedelta(days=slot)


def aplicar_curva_pago(ic_val: float) -> float:
    """
    Aplica la curva OLS y normaliza el resultado a [0, 1].

    fraccion_raw  = alpha + beta*IC + beta2*IC²   (escala z-score)
    fraccion_raw  se clipea a [0, PAYOUT_MAX]
    fraccion_norm = fraccion_raw / PAYOUT_MAX      → [0, 1]

    Interpretación:
      0.0 → sin pérdida
      0.5 → pérdida media, paga el 50% de la cobertura contratada
      1.0 → pérdida total (IC ≤ p5), paga el 100% de la cobertura
    """
    raw  = CP_ALPHA + CP_BETA * ic_val + CP_BETA2 * (ic_val ** 2)
    raw  = max(0.0, min(CP_PM, raw))
    return round(raw / CP_PM, 6)


def validar_basis_risk(celda: tuple, dist_km: float):
    """Lanza HTTP 400 si la celda más cercana supera el radio máximo."""
    if dist_km > BASIS_RISK_MAX_KM:
        raise HTTPException(
            status_code=400,
            detail={
                "mensaje": (
                    f"Coordenadas fuera de la zona modelada. "
                    f"La celda más cercana se encuentra a {dist_km:.1f} km "
                    f"(máximo permitido: {BASIS_RISK_MAX_KM} km)."
                ),
                "celda_mas_cercana": {
                    "lat"         : celda[0],
                    "lon"         : celda[1],
                    "distancia_km": dist_km,
                },
            }
        )


def obtener_prima_celda(celda: tuple) -> pd.Series:
    """Busca la fila de primas precalculadas para la celda."""
    fila = primas_df[
        (primas_df["lat"] == celda[0]) &
        (primas_df["lon"] == celda[1])
    ]
    if fila.empty:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No se encontró prima precalculada para la celda "
                f"({celda[0]}, {celda[1]}). Intente con coordenadas "
                f"dentro de la zona cafetera de Caldas."
            )
        )
    return fila.iloc[0]


def obtener_triggers_celda(celda: tuple) -> dict:
    """Busca los triggers p10 y p5 para la celda."""
    trig = triggers.get(celda)
    if trig is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No se encontraron triggers para la celda "
                f"({celda[0]}, {celda[1]})."
            )
        )
    return trig


# ── Mensajes de error en español ──────────────────────────────────────
MENSAJES_VALIDACION = {
    "lat"                  : f"El campo 'lat' debe estar entre {LAT_MIN} y {LAT_MAX}",
    "lon"                  : f"El campo 'lon' debe estar entre {LON_MIN} y {LON_MAX}",
    "hectareas"            : "El campo 'hectareas' debe ser mayor que 0",
    "suma_asegurada_usd_ha": "El campo 'suma_asegurada_usd_ha' debe ser mayor que 0",
    "cobertura"            : "El campo 'cobertura' debe estar entre 0.0 y 1.0",
    "loading"              : "El campo 'loading' debe estar entre 0.0 y 1.0",
    "fecha_evento"         : (
        f"El campo 'fecha_evento' debe estar entre "
        f"{FECHA_MIN.strftime('%d/%m/%Y')} y "
        f"{FECHA_MAX.strftime('%d/%m/%Y')}"
    ),
}


# ── Schemas de entrada ────────────────────────────────────────────────

class CotizacionRequest(BaseModel):
    lat:                   float = Field(..., ge=LAT_MIN, le=LAT_MAX)
    lon:                   float = Field(..., ge=LON_MIN, le=LON_MAX)
    hectareas:             float = Field(..., gt=0)
    suma_asegurada_usd_ha: float = Field(..., gt=0)
    cobertura:             float = Field(default=COBERTURA_DEFAULT, gt=0.0, le=1.0)
    loading:               float = Field(default=LOADING_DEFAULT,   ge=0.0, le=1.0)


class EventoRequest(BaseModel):
    lat:                   float = Field(..., ge=LAT_MIN, le=LAT_MAX)
    lon:                   float = Field(..., ge=LON_MIN, le=LON_MAX)
    fecha_evento:          date  = Field(...)
    hectareas:             float = Field(..., gt=0)
    suma_asegurada_usd_ha: float = Field(..., gt=0)
    cobertura:             float = Field(default=COBERTURA_DEFAULT, gt=0.0, le=1.0)
    loading:               float = Field(default=LOADING_DEFAULT,   ge=0.0, le=1.0)

    @field_validator("fecha_evento")
    @classmethod
    def validar_fecha(cls, v):
        if v < FECHA_MIN or v > FECHA_MAX:
            raise ValueError(MENSAJES_VALIDACION["fecha_evento"])
        return v


# ── Schemas de respuesta ──────────────────────────────────────────────

class Ubicacion(BaseModel):
    lat:           float
    lon:           float
    celda_lat:     float
    celda_lon:     float
    basis_risk_km: float


class ContextoCelda(BaseModel):
    e_loss:                    float
    prob_activacion_historica: float


class Poliza(BaseModel):
    prima_tecnica:         float
    loading:               float
    prima_comercial:       float
    cobertura:             float
    suma_asegurada_usd_ha: float
    hectareas:             float
    prima_total_usd:       float


class IndiceClimatico(BaseModel):
    ic:             float
    periodo:        str
    contribuciones: dict


class Trigger(BaseModel):
    activo:                    bool
    umbral_p10:                float
    umbral_p5:                 float
    prob_activacion_historica: float


class Pago(BaseModel):
    trigger_activo: bool
    fraccion_pago:  float   # fracción normalizada [0,1] de la cobertura
    pago_usd:       float


class CotizacionResponse(BaseModel):
    ubicacion:      Ubicacion
    contexto_celda: ContextoCelda
    poliza:         Poliza


class EventoResponse(BaseModel):
    ubicacion:        Ubicacion
    indice_climatico: IndiceClimatico
    trigger:          Trigger
    pago:             Pago


# ── App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="RedCafé API",
    description=(
        "API REST para calcular primas técnicas de seguro agrícola indexado "
        "para café en Caldas. Basada en el Índice Climático Compuesto IC_WI_ext "
        "construido desde datos ERA5-Land (2003-presente).\n\n"
        "**Modelo de pricing:** Weibull + Monte Carlo (50.000 escenarios) "
        f"con curva de pago {CP_TIPO} calibrada en zona de disparo "
        "(train 2003-2018).\n\n"
        "**Prima:** `e_loss × (1 + loading) × cobertura "
        "× suma_asegurada_usd_ha × hectareas`\n\n"
        "**Pago por evento:** `fraccion_pago × cobertura "
        "× suma_asegurada_usd_ha × hectareas`, donde "
        "`fraccion_pago ∈ [0,1]` es la curva OLS normalizada por PAYOUT_MAX.\n\n"
        "**Basis risk:** distancia entre coordenadas ingresadas y centroide de "
        "celda ERA5 más cercana (resolución espacial: 11 km). "
        "Se rechaza la solicitud si basis_risk > 5.5 km.\n\n"
        f"**Rango de fechas disponible:** "
        f"{FECHA_MIN.strftime('%Y-%m-%d')} a "
        f"{FECHA_MAX.strftime('%Y-%m-%d')} "
        f"(se actualiza cada 16 días)."
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


# ── Endpoints de sistema ──────────────────────────────────────────────

@app.get("/health",
         summary="Estado del servicio",
         tags=["Sistema"])
def health():
    return {
        "estado"              : "activo",
        "version"             : "1.0.0",
        "celdas_disponibles"  : len(params_dist),
        "datos_hasta"         : FECHA_MAX.strftime("%Y-%m-%d"),
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
                "uso"                : ("proxy de pérdida agrícola (NDVI_anom) "
                                        "para calibración del modelo"),
            },
        ],
        "periodo_historico"   : "2003-01-01 / 2021-12-31 (train + val + test)",
        "periodo_scoring"     : (f"{FECHA_MIN.strftime('%Y-%m-%d')} / "
                                 f"{FECHA_MAX.strftime('%Y-%m-%d')}"),
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
            detail=(
                f"Coordenadas fuera del área de Caldas. "
                f"lat debe estar entre {LAT_MIN} y {LAT_MAX}, "
                f"lon entre {LON_MIN} y {LON_MAX}."
            )
        )
    celda, dist_km = celda_mas_cercana(lat, lon)
    validar_basis_risk(celda, dist_km)
    return {
        "lat"           : lat,
        "lon"           : lon,
        "celda_asignada": {"lat": celda[0], "lon": celda[1]},
        "basis_risk_km" : dist_km,
        "mensaje"       : "Coordenadas válidas. Celda asignada correctamente.",
    }


# ── Endpoint 1: Cotización de la póliza ──────────────────────────────

@app.post(
    "/policy/quote",
    response_model=CotizacionResponse,
    summary="Cotización de la póliza — prima en USD",
    tags=["Póliza"],
    description=(
        "Calcula la prima técnica y comercial para asegurar una finca en "
        "Caldas, basada en el historial climático de la celda ERA5 asignada.\n\n"
        "**Prima técnica:** `e_loss` — pérdida esperada promedio por período, "
        "estimada por Monte Carlo (Weibull, 50.000 escenarios).\n\n"
        "**Prima comercial:** `prima_tecnica × (1 + loading)`\n\n"
        "**Prima total:** `prima_comercial × cobertura "
        "× suma_asegurada_usd_ha × hectareas`\n\n"
        "No requiere fecha — la prima se basa en el historial completo "
        "de la celda, no en un período específico."
    )
)
def policy_quote(req: CotizacionRequest):

    # ── 1. Celda + basis risk ─────────────────────────────────────────
    celda, dist_km = celda_mas_cercana(req.lat, req.lon)
    validar_basis_risk(celda, dist_km)

    # ── 2. Prima precalculada de la celda ─────────────────────────────
    row      = obtener_prima_celda(celda)
    e_loss   = float(row["e_loss"])
    prob_act = round(float(row.get("prob_trigger", 0.10)), 4)

    # ── 3. Cálculo de prima ───────────────────────────────────────────
    prima_tecnica   = e_loss
    prima_comercial = e_loss * (1 + req.loading)
    prima_total_usd = round(
        prima_comercial * req.cobertura
        * req.suma_asegurada_usd_ha * req.hectareas,
        2
    )

    return CotizacionResponse(
        ubicacion=Ubicacion(
            lat=req.lat, lon=req.lon,
            celda_lat=celda[0], celda_lon=celda[1],
            basis_risk_km=dist_km,
        ),
        contexto_celda=ContextoCelda(
            e_loss=round(e_loss, 6),
            prob_activacion_historica=prob_act,
        ),
        poliza=Poliza(
            prima_tecnica=round(prima_tecnica, 6),
            loading=req.loading,
            prima_comercial=round(prima_comercial, 6),
            cobertura=req.cobertura,
            suma_asegurada_usd_ha=req.suma_asegurada_usd_ha,
            hectareas=req.hectareas,
            prima_total_usd=prima_total_usd,
        ),
    )


# ── Endpoint 2: Verificación de pago por evento ───────────────────────

@app.post(
    "/event/check",
    response_model=EventoResponse,
    summary="Verificación de pago por evento climático",
    tags=["Evento"],
    description=(
        "Verifica si el IC del período de 16 días que contiene la "
        "fecha_evento activa el trigger del seguro, y calcula el pago "
        "correspondiente en USD.\n\n"
        "**Fracción de pago:** resultado de la curva OLS normalizado a [0,1] "
        f"dividiendo por PAYOUT_MAX ({CP_PM:.4f}). "
        "Representa qué fracción de la cobertura contratada se paga:\n"
        "- `0.0` → sin pérdida detectable\n"
        "- `0.5` → pérdida media, paga el 50% de la cobertura\n"
        "- `1.0` → pérdida total (IC ≤ p5), paga el 100% de la cobertura\n\n"
        "**Pago:** `fraccion_pago × cobertura × suma_asegurada_usd_ha "
        "× hectareas`. Si el trigger no está activo, `pago_usd = 0`.\n\n"
        f"**Rango de fechas disponible:** "
        f"{FECHA_MIN.strftime('%Y-%m-%d')} a "
        f"{FECHA_MAX.strftime('%Y-%m-%d')}."
    )
)
def event_check(req: EventoRequest):

    # ── 1. Celda + basis risk ─────────────────────────────────────────
    celda, dist_km = celda_mas_cercana(req.lat, req.lon)
    validar_basis_risk(celda, dist_km)

    # ── 2. Período de 16 días ─────────────────────────────────────────
    periodo = fecha_a_periodo(req.fecha_evento)

    # ── 3. IC del período ─────────────────────────────────────────────
    ic_fila = ic_test_df[
        (ic_test_df["lat"]   == celda[0]) &
        (ic_test_df["lon"]   == celda[1]) &
        (ic_test_df["fecha"] == periodo)
    ]
    if ic_fila.empty:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No se encontró valor del IC para la celda "
                f"({celda[0]}, {celda[1]}) en el período "
                f"{periodo.strftime('%Y-%m-%d')}. "
                f"Rango disponible: {FECHA_MIN.strftime('%Y-%m-%d')} a "
                f"{FECHA_MAX.strftime('%Y-%m-%d')}."
            )
        )

    ic_row = ic_fila.iloc[0]
    ic_val = round(float(ic_row["IC"]), 4)

    # ── 4. Contribuciones: w_i × Z_i ─────────────────────────────────
    contribuciones = {}
    for c in COLS_Z:
        w_i = float(pesos_wi[c]) if c in pesos_wi     else 0.0
        z_i = float(ic_row[c])   if c in ic_row.index else 0.0
        contribuciones[c] = round(w_i * z_i, 4)

    # ── 5. Trigger ────────────────────────────────────────────────────
    trig     = obtener_triggers_celda(celda)
    p10      = float(trig["p10_ic"])
    p5       = float(trig["p5_ic"])
    activo   = ic_val < p10

    row      = obtener_prima_celda(celda)
    prob_act = round(float(row.get("prob_trigger", 0.10)), 4)

    # ── 6. Pago ───────────────────────────────────────────────────────
    # fraccion_pago ∈ [0,1]: curva OLS normalizada por PAYOUT_MAX
    # pago_usd = fraccion_pago × cobertura × suma_asegurada × hectareas
    if activo:
        fraccion_pago = aplicar_curva_pago(ic_val)
        pago_usd = round(
            fraccion_pago * req.cobertura
            * req.suma_asegurada_usd_ha * req.hectareas,
            2
        )
    else:
        fraccion_pago = 0.0
        pago_usd      = 0.0

    return EventoResponse(
        ubicacion=Ubicacion(
            lat=req.lat, lon=req.lon,
            celda_lat=celda[0], celda_lon=celda[1],
            basis_risk_km=dist_km,
        ),
        indice_climatico=IndiceClimatico(
            ic=ic_val,
            periodo=periodo.strftime("%Y-%m-%d"),
            contribuciones=contribuciones,
        ),
        trigger=Trigger(
            activo=activo,
            umbral_p10=round(p10, 4),
            umbral_p5=round(p5, 4),
            prob_activacion_historica=prob_act,
        ),
        pago=Pago(
            trigger_activo=activo,
            fraccion_pago=fraccion_pago,
            pago_usd=pago_usd,
        ),
    )
