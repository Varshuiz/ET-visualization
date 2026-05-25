# ET Visualization (Django)

A Django web application for evapotranspiration (ET) analysis, weather-driven irrigation guidance, and AquaCrop-based crop simulation focused on Alberta workflows.

This project provides:
- ET calculators (Priestley-Taylor, Penman-Monteith, Hargreaves-Samani, Maulé)
- Historical ET comparison workflows
- Forecast-based irrigation recommendations
- AquaCrop simulations with charting and result summaries
- ECCC-first weather sourcing with Open-Meteo fallback

---

## 1) Tech Stack

- Python 3.11
- Django 5.2
- Pandas / NumPy / Matplotlib
- Requests (external weather APIs)
- AquaCrop-OSPy (`aquacrop`)
- SQLite locally, PostgreSQL on Render (via `DATABASE_URL`)

---

## 2) Project Structure

- `et/` - main app (views, ET methods, weather ingestion, templates)
- `et/templates/et/` - UI pages
- `et_site/` - Django project settings/urls/wsgi
- `manage.py` - Django entrypoint
- `requirements.txt` - Python dependencies
- `render.yaml` - Render deployment config

Key modules:
- `et/et_methods.py` - ET equations and vectorized implementations
- `et/eccc_weather.py` - ECCC climate fetch/enrichment logic
- `et/weather_ingestion.py` - historical data ingestion (ECCC-primary pipeline)
- `et/environment_canada_scraper.py` - ECCC forecast scraping (MSC Datamart XML)
- `et/forecast_recommendations.py` - forecast driver merges and confidence logic
- `et/aquacrop_simulator.py` - AquaCrop run, parameter handling, result extraction
- `et/aquacrop_aggregation.py` - weekly/biweekly chart aggregation

---

## 3) Local Setup

### Prerequisites

- Python 3.11+
- `pip`

### Install

```bash
pip install -r requirements.txt
```

### Database + static

```bash
python manage.py migrate
python manage.py collectstatic --noinput
```

### Run dev server

```bash
python manage.py runserver
```

Open:
- [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- App routes are under `/et/` (root redirects to `/et/fetch-data/`)

---

## 4) Data Sources and Priority

## Weather Source Policy

The application is configured to **prefer ECCC data first** and use Open-Meteo only for fallback/gap filling where needed.

### Historical flows

- Primary: ECCC climate-daily based pipeline
- Fallback: Open-Meteo archive
- Additional enrichment: ECCC RH/Rn overlays where available

### AquaCrop historical weather

- Primary: ECCC climate-daily
- Gap fill: Open-Meteo temperatures for missing ECCC temperature days
- Additional safeguards:
  - minimum coverage checks
  - warnings surfaced to UI
  - `ReferenceET` floor to avoid divide-by-zero

### Forecast flows

- Primary forecast: ECCC (MSC Datamart citypage XML)
- Open-Meteo forecast drivers (RH/u2/Rs): used only for missing fields
- Historical confidence support: routed through ECCC-first historical fetch path

### External endpoints used

- ECCC/MSC:
  - `https://dd.weather.gc.ca/...`
  - `https://api.weather.gc.ca/...`
  - `https://climate.weather.gc.ca/climate_data/bulk_data_e.html`
- Open-Meteo:
  - `https://archive-api.open-meteo.com/v1/archive`
  - `https://api.open-meteo.com/v1/forecast`

---

## 5) ET Methods Implemented

Implemented in `et/et_methods.py` with scalar and vectorized variants.

### Priestley-Taylor

- Uses default `alpha = 1.26`
- Daily implementation assumes negligible soil heat flux (`G ~= 0`)
- Currently fixed coefficient (no auto-calibration)

### Penman-Monteith (FAO-56 style)

- Includes standard terms/coefficient structure used in FAO-56 formulation
- Handles RH/wind/radiation inputs with practical defaults in ingestion views when missing

### Hargreaves-Samani

- Uses default coefficient `0.0023`
- Radiation handled in code with explicit conversion path (`Ra / lambda`)
- Currently fixed coefficient (no auto-calibration)

### Maulé

- Implemented as code-based empirical relation with Prairie-tuned constants:
  - `k = 0.0055`, `a = 17.8`
  - plus bounded humidity and temperature-range correction factors

---

## 6) AquaCrop Notes

AquaCrop behavior is handled in `et/aquacrop_simulator.py` and related templates/views.

Recent robustness aspects include:
- Explicit crop parameter setting (including CD timing fields for Wheat)
- Partial-result handling without hard crashes
- Canopy max reporting for summaries
- Mature vs partial status propagation to UI
- Weather preprocessing guards (including positive ET floor)

If simulations look incomplete, check:
- Date range sufficiency for crop maturity
- Weather coverage warnings in UI
- Source coverage info shown in AquaCrop result panel

---

## 7) Main User Flows

- `GET /et/fetch-data/` - ET setup + historical data retrieval
- `GET /et/comparison-acis/` - ET method comparison on fetched data
- `GET /et/comparison/` - method comparison page
- `GET /et/env-canada-forecast/` - forecast irrigation guidance
- `GET /et/aquacrop/` - AquaCrop simulation workflow
- `GET /et/methods/` - method guide and references
- `GET /et/help/` - usage help

---

## 8) Deployment (Render)

Configured via `render.yaml`:
- Build:
  - `pip install -r requirements.txt`
  - `python manage.py collectstatic --noinput`
  - `python manage.py migrate`
- Start:
  - `gunicorn et_site.wsgi:application`

Environment variables:
- `SECRET_KEY`
- `DEBUG=False` in production
- `DATABASE_URL` for Postgres
- `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` as needed

---

## 9) Troubleshooting

### `npm run dev` fails

This is a Django app, not a Node frontend app. Use:

```bash
python manage.py runserver
```

### Missing weather rows / source errors

- Try another city/date span
- Reduce date range if endpoint coverage is sparse
- Upload CSV as backup

### AquaCrop partial/zero-looking summaries

- Check if run is partial or mature in UI
- Ensure end date allows maturity for selected crop/location
- Review weather warning and source-coverage boxes in results

---

## 10) References

Primary references and source credits are centralized in:
- `et/templates/et/method_info.html` (`#references-data-sources`)

This includes ET method literature, AquaCrop credits, and weather data providers.

