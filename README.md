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
red_cafe/
├── notebooks/
│   ├── 01_Procesamiento.ipynb       # QA, filtro espacial, splits, feature eng.
│   ├── 02_IC_construccion.ipynb     # PCA, WI, backtest, PA2, IC_WI_ext
│   ├── 03_Pricing.ipynb             # STL, PAYOUT_MAX calibrado, Plan A (OLS) vs Plan B (lineal), comparación, HE, exportación
│   └── 00_Descarga_GEE.ipynb        # Descarga ERA5 + MODIS (pendiente)
├── src/
│   ├── api/main.py                  # FastAPI: /indicator, /simulation, etc.
│   └── pipeline/00_descarga_gee.py  # Script DVC para descarga automatizada
├── config/
│   └── params.yaml                  # Parámetros centralizados del proyecto
├── data/                            # Gestionada por DVC (no en git)
│   ├── raw/                         # ERA5 + MODIS sin procesar
│   └── processed/                   # Parquets train/val/test
├── output/                          # Gestionada por DVC (no en git)
│   ├── ic/                          # Modelos IC + parquets con IC
│   └── pa3/                         # Weibull params, triggers, primas
├── dvc.yaml                         # Pipeline DVC de 4 etapas
├── .github/workflows/
│   ├── pipeline_update.yml          # Actualización automática cada 16 días
│   └── deploy_api.yml               # Deploy a Railway al hacer push a main
└── requirements.txt
```

---

## Instalación local

```bash
# 1. Clonar el repositorio
git clone https://github.com/TU_ORG/red-cafe.git
cd red-cafe

# 2. Crear entorno virtual e instalar dependencias
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Configurar DVC remote para datos crudos
dvc remote modify myremote gdrive_use_service_account true
# Nota: los artefactos del modelo ya están incluidos en el repositorio (output/).
# dvc pull solo es necesario si se quiere regenerar los datos crudos desde cero
# (data/raw/ y data/processed/), que no son necesarios para correr el API.

# 4. Lanzar la API localmente
### Pendiente
```

#### Swagger UI disponible en: ----pendiente----

---

## Pipeline DVC

El pipeline tiene 4 etapas encadenadas:

```
descarga → procesamiento → ic_construccion → pricing
```

Para reproducir todo desde cero:

```bash
dvc repro
```

Para reproducir solo una etapa:

```bash
dvc repro pricing   # solo la etapa de pricing
```

Para actualizar datos con una nueva fecha:

```bash
# Editar config/params.yaml → fecha_fin: "2026-05-14"
dvc repro descarga
dvc push
```

---

## Actualización (manual por ahora -- futuro automática) (cada 16 días)

Ejecutar manualmente desde **Actions → Actualización pipeline → Run workflow**.

Secrets requeridos en GitHub:
| Secret | Descripción |
|--------|-------------|
| `GEE_SERVICE_ACCOUNT_KEY` | JSON de cuenta de servicio de GEE |
| `GDRIVE_CREDENTIALS_DATA` | Credenciales OAuth DVC para Google Drive |
| `RAILWAY_TOKEN` | Token de despliegue Railway |

---

## API — Endpoints principales - PENDIENTE

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/sources` | Fuentes de datos disponibles |
| POST | `/field-verification` | Valida lat/lon dentro de Caldas |
| POST | `/indicator/generate` | IC para una ubicación y fecha |
| POST | `/simulation/run` | E(Loss) + prima técnica + trazabilidad |

**Ejemplo de solicitud:**
```bash
curl -X POST http://localhost:8000/simulation/run \
  -H "Content-Type: application/json" \
  -d '{"lat": 5.05, "lon": -75.50, "fecha": "2026-04-28"}'
```

**Ejemplo de respuesta:**
```json
{
  "fecha": "2026-04-28",
  "lat": 5.05, "lon": -75.50,
  "celda_lat": 5.05, "celda_lon": -75.50,
  "basis_risk_km": 0.0,
  "ic": -0.432,
  "trigger_p10": -0.518,
  "trigger_p5": -0.823,
  "activo": true,
  "e_loss": 0.127341,
  "prima_pura": 0.127341,
  "loading": 0.20,
  "prima_cargada": 0.152809,
  "distribucion": "weibull",
  "n_sim": 50000
}
```

---

## Métricas del modelo (TEST SET)

| Requerimiento | Criterio | Resultado |
|---|---|---|
| R1 — Hedge Effectiveness | ≥ 55% | 39.6% - No cumple |
| R2 — Dispersión de primas | ≥ 20% diferencia alto/bajo riesgo | 45.1% ✅ |
| R3 — Reproducibilidad | Varianza = 0 con semilla fija | 0 ✅ |
| R4 — Correlación IC-NDVI | ρ ≥ 0.60 | ρ = 0.604 ✅ |
| R5 — Recall eventos extremos | ≥ 60% | 85.1% ✅ |

---

## Licencia

Proyecto académico — MIAD Universidad de los Andes, 2026.
