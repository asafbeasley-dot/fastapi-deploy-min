# FastAPI Deploy Minimal

Minimal FastAPI app to test deployments to Cloud Run, Railway, and Render.

## Endpoints

- `GET /health` — quick health check
- `GET /fast` — simple JSON response
- `POST /slow` — accepts JSON and simulates work
  - default: sleeps 1–2 seconds
  - optional HTTP mode: `{ "mode": "http", "url": "https://httpbin.org/delay/1" }`

## Local run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Docker

```bash
docker build -t fastapi-deploy-min .
docker run -p 8080:8080 fastapi-deploy-min
```

## Deploy: Google Cloud Run

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

gcloud run deploy fastapi-deploy-min   --source .   --region us-central1   --allow-unauthenticated
```

## Deploy: Railway

1. Create a new project in Railway.
2. Add a service from GitHub repo.
3. Railway auto-detects the Dockerfile and deploys.

## Deploy: Render

1. Create a new **Web Service** on Render.
2. Connect the GitHub repo.
3. Choose **Docker** as the environment.
4. Render will build and deploy the container.

## Sample requests

```bash
curl http://localhost:8080/health
curl http://localhost:8080/fast
curl -X POST http://localhost:8080/slow -H "Content-Type: application/json" -d '{"sleep_sec": 1.3}'
curl -X POST http://localhost:8080/slow -H "Content-Type: application/json" -d '{"mode": "http"}'
```
