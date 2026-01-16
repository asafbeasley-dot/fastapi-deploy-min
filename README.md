# FastAPI Deploy Showcase

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688)](https://fastapi.tiangolo.com/)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED)](https://www.docker.com/)
[![Cloud%20Run](https://img.shields.io/badge/Deploy-Cloud%20Run-4285F4)](https://cloud.google.com/run)
[![Railway](https://img.shields.io/badge/Deploy-Railway-0B0D0E)](https://railway.app/)
[![Render](https://img.shields.io/badge/Deploy-Render-46E3B7)](https://render.com/)
[![Vercel](https://img.shields.io/badge/Deploy-Vercel-000000)](https://vercel.com/)

Minimal FastAPI app designed as a **deployment showcase** for Cloud Run, Railway, Render, and Vercel.

## Features

- Root **dashboard** (`/`) with platform badge, endpoint explorer, metrics, and response viewer
- In-memory **metrics** (`/metrics`) with latency stats and request counts
- Detailed **health** (`/health`) with platform, uptime, and system stats (CPU/memory if available)
- Test endpoints: fast, slow, external HTTP, upload/download, error simulation, WebSocket echo
- CORS enabled, security headers added, simple in-memory rate limiting

## Endpoints

- `GET /` — interactive HTML dashboard
- `GET /health` — detailed health status
- `GET /metrics` — in-memory metrics
- `GET /fast` — baseline latency
- `POST /slow` — async delay (sleep or external HTTP)
- `GET /external` — outbound HTTP test
- `GET /error/404` — simulate 404
- `GET /error/500` — simulate 500
- `POST /upload` — file upload
- `GET /download` — file download
- `WS /ws` — WebSocket echo (platform dependent)

## Local run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Docker

```bash
docker build -t fastapi-deploy-showcase .
docker run -p 8080:8080 fastapi-deploy-showcase
```

## Deploy: Google Cloud Run

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

gcloud run deploy fastapi-deploy-showcase   --source .   --region us-central1   --allow-unauthenticated
```

Health check: use `/health` in monitoring.

## Deploy: Railway

1. Create a new project in Railway.
2. Add a service from this GitHub repo.
3. Railway auto-detects the Dockerfile and deploys.

Health check: set **Healthcheck Path** to `/health` in Railway settings.

## Deploy: Render

1. Create a new **Web Service** on Render.
2. Connect the GitHub repo.
3. Choose **Docker** as the environment.
4. Render will build and deploy the container.

Health check: set **Health Check Path** to `/health`.

## Deploy: Vercel

This repo includes `vercel.json` for FastAPI on Vercel.

1. Import the repo into Vercel.
2. Build uses the Python runtime.
3. Deploy.

**Note:** Vercel serverless does not support WebSockets.

## Rate limiting

A simple in-memory limit is enabled (60 req/min per IP). Adjust in `app/main.py` if needed.

## Security headers

The app adds basic headers:
- `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`
- CSP allows Tailwind CDN + inline scripts/styles

## Sample requests

```bash
curl http://localhost:8080/health
curl http://localhost:8080/fast
curl -X POST http://localhost:8080/slow -H "Content-Type: application/json" -d '{"sleep_sec": 1.3}'
curl -X POST http://localhost:8080/slow -H "Content-Type: application/json" -d '{"mode": "http"}'
```
