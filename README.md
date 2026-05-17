# 🌿 RedCafé — Seguro Agrícola Indexado para Café en Caldas

Proyecto Aplicado en Analítica de Datos — MIAD  
Laura Andrea Martínez · Daniela Naranjo · María Paula Polanía  
Mayo 2026

---

## Descripción

API REST que calcula primas técnicas de seguro indexado para café en Caldas,
basadas en un Índice Climático Compuesto (IC_WI_ext) construido desde datos
ERA5-Land y validado contra NDVI de MODIS.

**Pipeline:** ERA5 + MODIS → Procesamiento → IC (WI sobre extremos + QR) → Weibull + MC → Curvas de pago OLS calibradas → Prima → API

---

## Estructura del proyecto

```
ProyectoFinal_RedCafe/
├── notebooks/
│   ├── 00_Descarga_GEE.ipynb        # Descarga ERA5 + MODIS desde GEE
│   ├── 01_Procesamiento.ipynb       # QA, filtro espacial, splits, feature eng.
│   ├── 02_IC_construccion.ipynb     # PCA, WI, backtest, IC_WI_ext
│   └── 03_Pricing.ipynb             # Weibull, Monte Carlo, OLS, primas
├── src/
│   └── api/main.py                  # FastAPI: /policy/quote, /event/check
├── config/
│   └── params.yaml                  # Parámetros centralizados del proyecto
├── data/                            # Datos crudos — gestionados por DVC
│   └── raw/
│       ├── ERA5_Caldas/             # Archivos .tif ERA5 por período de 16 días
│       ├── MODIS_Caldas/            # Archivos .tif NDVI por período de 16 días
│       ├── ERA5_Caldas.dvc          # Puntero DVC → Google Drive
│       └── MODIS_Caldas.dvc         # Puntero DVC → Google Drive
├── output/                          # Artefactos del modelo — en git (15 MB total)
│   ├── ic/                          # Modelos IC + parquets con IC calculado
│   └── pa3/                         # Weibull params, triggers, curva pago, primas
├── .github/workflows/
│   └── deploy_api.yml               # Deploy automático a Railway al hacer push a main
├── Procfile                         # Comando de inicio para Railway
├── requirements.txt                 # Dependencias del API (Railway)
├── requirements_model.txt           # Dependencias completas (notebooks + API)
└── ULTIMA_ACTUALIZACION.md          # Fecha de la última actualización de datos
```

---

## Acceso al API desplegado

El API está desplegado en Railway y no requiere instalación local para consumirlo.

| Recurso | URL |
|---|---|
| API Base | `https://web-production-320c0.up.railway.app` |
| Swagger UI | `https://web-production-320c0.up.railway.app/docs` |
| Health check | `https://web-production-320c0.up.railway.app/health` |

> **Nota:** si el servicio está inactivo, la primera respuesta puede tardar
> hasta 15 segundos (cold start). Las consultas siguientes responden en menos de 2 segundos.

---

## Instalación local

```bash
# 1. Clonar el repositorio
git clone https://github.com/MpaulaPo/ProyectoFinal_RedCafe.git
cd ProyectoFinal_RedCafe

# 2. Crear entorno e instalar dependencias
conda create -n redcafe python=3.10
conda activate redcafe
pip install -r requirements_model.txt

# 3. Bajar datos crudos desde Google Drive (DVC)
# Los artefactos del modelo ya están en el repositorio (output/).
# Solo es necesario bajar los datos crudos si se quiere regenerar
# el pipeline desde cero.
pip install dvc dvc-gdrive
dvc pull

# 4. Lanzar el API localmente
uvicorn src.api.main:app --reload --port 8000
```

Swagger UI disponible en: `http://localhost:8000/docs`

> **Nota para el equipo evaluador:** reemplazar `ee-naranjocdaniela` en la
> celda de inicialización de GEE (`00_Descarga_GEE.ipynb`) con el ID de su
> propio proyecto de Google Earth Engine.

---

## API — Endpoints

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/health` | Estado del servicio y fecha de última actualización |
| GET | `/sources` | Fuentes de datos disponibles |
| POST | `/field-verification` | Valida lat/lon dentro de la zona modelada |
| POST | `/policy/quote` | Cotización de la póliza — prima en USD |
| POST | `/event/check` | Verificación de pago por evento climático |

### Endpoint `/policy/quote` — Cotización de la póliza

**Entrada:**
```json
{
  "lat": 4.97,
  "lon": -75.61,
  "hectareas": 3.5,
  "suma_asegurada_usd_ha": 300.0,
  "cobertura": 0.70,
  "loading": 0.20
}
```

**Salida:**
```json
{
  "ubicacion": {
    "lat": 4.97, "lon": -75.61,
    "celda_lat": 4.97, "celda_lon": -75.61,
    "basis_risk_km": 0.0
  },
  "contexto_celda": {
    "e_loss": 0.127341,
    "prob_activacion_historica": 0.102
  },
  "poliza": {
    "prima_tecnica": 0.127341,
    "loading": 0.20,
    "prima_comercial": 0.152809,
    "cobertura": 0.70,
    "suma_asegurada_usd_ha": 300.0,
    "hectareas": 3.5,
    "prima_total_usd": 112.95
  }
}
```

### Endpoint `/event/check` — Verificación de pago por evento

**Entrada:**
```json
{
  "lat": 4.97,
  "lon": -75.61,
  "fecha_evento": "2024-03-01",
  "hectareas": 3.5,
  "suma_asegurada_usd_ha": 300.0,
  "cobertura": 0.70,
  "loading": 0.20
}
```

**Salida:**
```json
{
  "ubicacion": {
    "lat": 4.97, "lon": -75.61,
    "celda_lat": 4.97, "celda_lon": -75.61,
    "basis_risk_km": 0.0
  },
  "indice_climatico": {
    "ic": -0.432,
    "periodo": "2024-02-17",
    "contribuciones": {
      "Z_BAL": -0.198, "Z_ppt": -0.112, "Z_tmax": 0.043
    }
  },
  "trigger": {
    "activo": false,
    "umbral_p10": -1.385,
    "umbral_p5": -1.451,
    "prob_activacion_historica": 0.102
  },
  "pago": {
    "trigger_activo": false,
    "fraccion_pago": 0.0,
    "pago_usd": 0.0
  }
}
```

### Códigos de error

| Código | Cuándo ocurre |
|---|---|
| HTTP 422 | Campo faltante, tipo incorrecto o fuera de rango |
| HTTP 400 | Celda más cercana a > 5.5 km de las coordenadas ingresadas |
| HTTP 404 | Sin datos para la celda o período solicitado |

---

## Actualización de datos (cada 16 días)

Los datos de ERA5 tienen un rezago de 2-3 meses. La actualización se realiza
manualmente siguiendo estos pasos:

**Paso 1 — Ejecutar `00_Descarga_GEE.ipynb`**

Cambiar `PRIMERA_VEZ = False` y ejecutar. El notebook detecta automáticamente 
la última fecha disponible en ERA5 y lanza las tareas de GEE solo para los 
períodos nuevos. Las tareas pueden tardar varias horas. Monitorear en 
`https://code.earthengine.google.com/tasks`

**Paso 2 — Bajar archivos nuevos de Drive a local**

Cuando todas las tareas terminen, descargar los `.tif` nuevos desde Google Drive 
y copiarlos en:
- `data/raw/ERA5_Caldas/`
- `data/raw/MODIS_Caldas/`

**Paso 3 — Ejecutar el pipeline**

```
01_Procesamiento.ipynb   → PRIMERA_VEZ = False
02_IC_construccion.ipynb → PRIMERA_VEZ = False (celda incremental al inicio)
03_Pricing.ipynb         → PRIMERA_VEZ = False (celda incremental al inicio)
```

> **Importante:** no recalcular `scaler_params`, pesos del IC_WI_ext ni
> modelo de dependencia. Estos parámetros están fijos en train (2003-2018)
> para evitar data leakage.

**Paso 4 — Versionar datos y redesplegar**

```bash
dvc add data/raw/ERA5_Caldas data/raw/MODIS_Caldas
dvc push
git add data/raw/ERA5_Caldas.dvc data/raw/MODIS_Caldas.dvc output/ ULTIMA_ACTUALIZACION.md
git commit -m "data: actualización incremental — YYYY-MM-DD"
git push
```

Railway detecta el push y redespliega automáticamente con los artefactos nuevos.

---

## Métricas del proyecto

| Requerimiento | Criterio | Resultado |
|---|---|---|
| R1 — Hedge Effectiveness | ≥ 55% | 39.6% ⚠️ |
| R2 — Dispersión de primas | ≥ 20% diferencia alto/bajo riesgo | 45.1% ✅ |
| R3 — Reproducibilidad | Varianza = 0 con semilla fija | 0.0 ✅ |
| R4 — Correlación IC-NDVI | ρ ≥ 0.60 | ρ = 0.604 ✅ |
| R5 — Recall eventos extremos | ≥ 60% | 85.1% ✅ |
| R6 — Precisión y estabilidad temporal del IC | Pinball loss ≤ 22% | 21% ✅ |
| R7 — Desempeño y disponibilidad del API desplegado | ≤ 2 seg | 239 ms ✅ |
| R8 — Actualización periódica y mantenimiento del modelo | c/16 días | Últ. act: 08/05/2026 ✅ |
| R9 — Manejo de errores e integridad del sistema | Errores documentados | HTTP 400/404/422/500/503 ✅ |
| R10 — Interpretabilidad y auditabilidad del modelo para el actuario | | Modelo interpretable ✅ |
| R11 — Integración estándar y trazabilidad de resultados | | API REST con JSON estructurado ✅ |

> R1 no cumple debido a la alta nubosidad en Caldas (~50% de faltantes en
> NDVI MODIS), que limita la validación del proxy de pérdida. Mejora
> prevista en V2 con datos de rendimiento de la FNC y pruebas de otros IC.

---

## Licencia

Proyecto académico — MIAD Universidad de los Andes, 2026.
