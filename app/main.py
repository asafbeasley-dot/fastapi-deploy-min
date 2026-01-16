from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx
from fastapi import FastAPI

app = FastAPI(title="Deploy Test API", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/fast")
async def fast() -> dict[str, str]:
    return {"message": "fast response"}


@app.post("/slow")
async def slow(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    mode = str(payload.get("mode", "sleep"))
    started = time.time()

    if mode == "http":
        url = str(payload.get("url", "https://httpbin.org/delay/1"))
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            data = resp.json()
        elapsed = time.time() - started
        return {"mode": "http", "url": url, "elapsed_sec": round(elapsed, 3), "data": data}

    sleep_for = float(payload.get("sleep_sec", random.uniform(1.0, 2.0)))
    await asyncio.sleep(sleep_for)
    elapsed = time.time() - started
    return {"mode": "sleep", "sleep_sec": round(sleep_for, 3), "elapsed_sec": round(elapsed, 3)}
