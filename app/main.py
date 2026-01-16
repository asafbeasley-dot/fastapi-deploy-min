from __future__ import annotations

import asyncio
import io
import json
import os
import random
import statistics
import time
from collections import Counter, deque
from typing import Any, Deque

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse

try:  # optional
    import psutil  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    psutil = None


APP_VERSION = "0.2.0"
START_TIME = time.time()

REQUEST_COUNTS = Counter()
PATH_COUNTS = Counter()
LATENCIES_MS: Deque[float] = deque(maxlen=500)

RATE_LIMIT_WINDOW_SEC = 60
RATE_LIMIT_MAX = 60
RATE_BUCKETS: dict[str, dict[str, float]] = {}

app = FastAPI(title="Deploy Test API", version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _detect_platform() -> dict[str, str]:
    env = os.environ
    if env.get("K_SERVICE") or env.get("CLOUD_RUN_JOB"):
        return {"name": "cloud_run", "evidence": "K_SERVICE/CLOUD_RUN_JOB"}
    if env.get("RAILWAY_ENVIRONMENT") or env.get("RAILWAY_PROJECT_ID"):
        return {"name": "railway", "evidence": "RAILWAY_ENVIRONMENT/PROJECT_ID"}
    if env.get("RENDER") or env.get("RENDER_SERVICE_ID"):
        return {"name": "render", "evidence": "RENDER/RENDER_SERVICE_ID"}
    if env.get("VERCEL") or env.get("VERCEL_URL"):
        return {"name": "vercel", "evidence": "VERCEL/VERCEL_URL"}
    return {"name": "local", "evidence": "none"}


def _uptime_sec() -> float:
    return max(0.0, time.time() - START_TIME)


def _system_stats() -> dict[str, Any]:
    stats: dict[str, Any] = {}
    if psutil:
        stats["cpu_percent"] = psutil.cpu_percent(interval=None)
        stats["memory"] = {
            "total": psutil.virtual_memory().total,
            "available": psutil.virtual_memory().available,
            "used": psutil.virtual_memory().used,
            "percent": psutil.virtual_memory().percent,
        }
        return stats
    try:
        load_1, load_5, load_15 = os.getloadavg()
        stats["load_avg"] = {"1": load_1, "5": load_5, "15": load_15}
    except Exception:
        pass
    try:
        import resource  # noqa: WPS433

        usage = resource.getrusage(resource.RUSAGE_SELF)
        stats["memory"] = {"ru_maxrss": usage.ru_maxrss}
    except Exception:
        pass
    return stats


def _record_latency(path: str, ms: float) -> None:
    REQUEST_COUNTS["total"] += 1
    PATH_COUNTS[path] += 1
    LATENCIES_MS.append(ms)


def _rate_limit_check(request: Request) -> tuple[bool, dict[str, str]]:
    now = time.time()
    client = request.client.host if request.client else "unknown"
    bucket = RATE_BUCKETS.get(client)
    if not bucket or (now - bucket["start"]) >= RATE_LIMIT_WINDOW_SEC:
        RATE_BUCKETS[client] = {"start": now, "count": 1}
        remaining = RATE_LIMIT_MAX - 1
        reset = int(RATE_LIMIT_WINDOW_SEC)
        return False, {"X-RateLimit-Limit": str(RATE_LIMIT_MAX), "X-RateLimit-Remaining": str(remaining), "X-RateLimit-Reset": str(reset)}
    bucket["count"] += 1
    remaining = max(0, RATE_LIMIT_MAX - int(bucket["count"]))
    reset = int(RATE_LIMIT_WINDOW_SEC - (now - bucket["start"]))
    headers = {"X-RateLimit-Limit": str(RATE_LIMIT_MAX), "X-RateLimit-Remaining": str(remaining), "X-RateLimit-Reset": str(reset)}
    if bucket["count"] > RATE_LIMIT_MAX:
        return True, headers
    return False, headers


def _security_headers() -> dict[str, str]:
    return {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; script-src 'self' https://cdn.tailwindcss.com 'unsafe-inline'; style-src 'self' 'unsafe-inline'",
    }


@app.middleware("http")
async def _metrics_middleware(request: Request, call_next):
    blocked, rate_headers = _rate_limit_check(request)
    if blocked:
        return JSONResponse({"error": "rate_limited"}, status_code=429, headers=rate_headers)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except HTTPException as exc:
        response = JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    except Exception:
        response = JSONResponse({"error": "internal_error"}, status_code=500)
    duration_ms = (time.perf_counter() - start) * 1000
    _record_latency(request.url.path, duration_ms)
    response.headers.update(rate_headers)
    response.headers.update(_security_headers())
    response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
    print(f"{request.method} {request.url.path} -> {response.status_code} ({duration_ms:.2f} ms)")
    return response


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    platform_info = _detect_platform()
    return _dashboard_html(platform_info)


@app.get("/platform")
async def platform() -> dict[str, str]:
    return _detect_platform()


@app.get("/health")
async def health() -> dict[str, Any]:
    platform_info = _detect_platform()
    return {
        "status": "ok",
        "uptime_sec": round(_uptime_sec(), 2),
        "platform": platform_info,
        "stats": _system_stats(),
        "version": APP_VERSION,
        "python": f"{os.sys.version_info.major}.{os.sys.version_info.minor}.{os.sys.version_info.micro}",
    }


@app.get("/metrics")
async def metrics() -> dict[str, Any]:
    latencies = list(LATENCIES_MS)
    return {
        "requests_total": REQUEST_COUNTS["total"],
        "requests_by_path": dict(PATH_COUNTS),
        "uptime_sec": round(_uptime_sec(), 2),
        "latency_ms": {
            "avg": round(statistics.fmean(latencies), 2) if latencies else 0,
            "p50": round(statistics.median(latencies), 2) if latencies else 0,
            "p95": round(statistics.quantiles(latencies, n=20)[18], 2) if len(latencies) >= 20 else 0,
            "max": round(max(latencies), 2) if latencies else 0,
            "count": len(latencies),
        },
    }


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


@app.get("/external")
async def external(url: str = "https://httpbin.org/get") -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
    return {"status_code": resp.status_code, "url": url, "headers": dict(resp.headers), "json": resp.json()}


@app.get("/error/404")
async def error_404() -> None:
    raise HTTPException(status_code=404, detail="simulated_not_found")


@app.get("/error/500")
async def error_500() -> None:
    raise RuntimeError("simulated_internal_error")


@app.post("/upload")
async def upload(file: UploadFile = File(...)) -> dict[str, Any]:
    data = await file.read()
    return {
        "filename": file.filename,
        "content_type": file.content_type,
        "size_bytes": len(data),
    }


@app.get("/download")
async def download(size: int = 1024) -> StreamingResponse:
    size = max(1, min(size, 1024 * 1024))
    data = (b"0123456789abcdef" * (size // 16 + 1))[:size]
    bio = io.BytesIO(data)
    headers = {"Content-Disposition": f"attachment; filename=sample_{size}.bin"}
    return StreamingResponse(bio, media_type="application/octet-stream", headers=headers)


@app.websocket("/ws")
async def websocket_echo(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_text()
            await ws.send_text(msg)
    except WebSocketDisconnect:
        return


@app.exception_handler(404)
async def not_found_handler(_: Request, __: Exception):
    return JSONResponse({"error": "not_found"}, status_code=404)


def _dashboard_html(platform_info: dict[str, str]) -> str:
    platform_name = platform_info.get("name", "local")
    platform_evidence = platform_info.get("evidence", "none")
    return f"""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>FastAPI Deploy Showcase</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
      .json-key {{ color: #7dd3fc; }}
      .json-string {{ color: #a7f3d0; }}
      .json-number {{ color: #fca5a5; }}
      .json-boolean {{ color: #fde68a; }}
      .json-null {{ color: #c4b5fd; }}
    </style>
  </head>
  <body class="bg-slate-950 text-slate-100">
    <div class="max-w-5xl mx-auto p-6">
      <div class="flex items-center justify-between">
        <div>
          <h1 class="text-2xl font-semibold">FastAPI Deployment Showcase</h1>
          <p class="text-sm text-slate-400">Single-page dashboard with endpoint explorer and metrics.</p>
        </div>
        <button id="modeToggle" class="px-3 py-2 rounded bg-slate-800 text-sm">Toggle light</button>
      </div>

      <div class="mt-6 grid grid-cols-1 md:grid-cols-3 gap-4">
        <div class="p-4 rounded border border-slate-800 bg-slate-900">
          <div class="text-sm text-slate-400">Platform</div>
          <div class="text-lg font-semibold">{platform_name}</div>
          <div class="text-xs text-slate-500">evidence: {platform_evidence}</div>
        </div>
        <div class="p-4 rounded border border-slate-800 bg-slate-900">
          <div class="text-sm text-slate-400">Uptime</div>
          <div class="text-lg font-semibold" id="uptimeValue">—</div>
          <div class="text-xs text-slate-500">from /metrics</div>
        </div>
        <div class="p-4 rounded border border-slate-800 bg-slate-900">
          <div class="text-sm text-slate-400">Requests</div>
          <div class="text-lg font-semibold" id="reqValue">—</div>
          <div class="text-xs text-slate-500">from /metrics</div>
        </div>
      </div>

      <div class="mt-6 grid grid-cols-1 md:grid-cols-2 gap-4">
        <div class="p-4 rounded border border-slate-800 bg-slate-900">
          <h2 class="text-lg font-semibold mb-2">Endpoint Explorer</h2>
          <div class="space-y-2">
            <button class="w-full text-left px-3 py-2 bg-slate-800 rounded" data-action="GET /health">GET /health</button>
            <button class="w-full text-left px-3 py-2 bg-slate-800 rounded" data-action="GET /fast">GET /fast</button>
            <button class="w-full text-left px-3 py-2 bg-slate-800 rounded" data-action="POST /slow">POST /slow</button>
            <button class="w-full text-left px-3 py-2 bg-slate-800 rounded" data-action="GET /external">GET /external</button>
            <button class="w-full text-left px-3 py-2 bg-slate-800 rounded" data-action="GET /error/404">GET /error/404</button>
            <button class="w-full text-left px-3 py-2 bg-slate-800 rounded" data-action="GET /error/500">GET /error/500</button>
            <button class="w-full text-left px-3 py-2 bg-slate-800 rounded" data-action="POST /upload">POST /upload</button>
            <button class="w-full text-left px-3 py-2 bg-slate-800 rounded" data-action="GET /download">GET /download</button>
            <button class="w-full text-left px-3 py-2 bg-slate-800 rounded" data-action="WS /ws">WS /ws</button>
          </div>
        </div>

        <div class="p-4 rounded border border-slate-800 bg-slate-900">
          <h2 class="text-lg font-semibold mb-2">Request Inputs</h2>
          <div class="space-y-3 text-sm">
            <div>
              <label class="block text-slate-400 mb-1">/slow payload</label>
              <textarea id="slowPayload" class="w-full p-2 bg-slate-950 border border-slate-800 rounded" rows="3">{{ "mode": "sleep", "sleep_sec": 1.2 }}</textarea>
            </div>
            <div>
              <label class="block text-slate-400 mb-1">/external url</label>
              <input id="externalUrl" class="w-full p-2 bg-slate-950 border border-slate-800 rounded" value="https://httpbin.org/get" />
            </div>
            <div>
              <label class="block text-slate-400 mb-1">/download size (bytes)</label>
              <input id="downloadSize" type="number" class="w-full p-2 bg-slate-950 border border-slate-800 rounded" value="1024" />
            </div>
            <div>
              <label class="block text-slate-400 mb-1">Upload file</label>
              <input id="uploadFile" type="file" class="w-full text-slate-300" />
            </div>
            <div class="flex gap-2">
              <input id="wsMessage" class="flex-1 p-2 bg-slate-950 border border-slate-800 rounded" placeholder="WebSocket message" />
              <button id="wsSend" class="px-3 py-2 bg-slate-800 rounded">Send</button>
            </div>
          </div>
        </div>
      </div>

      <div class="mt-6 p-4 rounded border border-slate-800 bg-slate-900">
        <h2 class="text-lg font-semibold mb-2">Response</h2>
        <pre id="responseBox" class="text-xs whitespace-pre-wrap"></pre>
      </div>

      <div class="mt-6 p-4 rounded border border-slate-800 bg-slate-900">
        <h2 class="text-lg font-semibold mb-2">Metrics</h2>
        <div id="metricsBox" class="text-xs whitespace-pre-wrap"></div>
      </div>
    </div>

    <script>
      const responseBox = document.getElementById("responseBox");
      const metricsBox = document.getElementById("metricsBox");
      const uptimeValue = document.getElementById("uptimeValue");
      const reqValue = document.getElementById("reqValue");
      const modeToggle = document.getElementById("modeToggle");
      let ws = null;

      function syntaxHighlight(json) {{
        if (typeof json !== "string") {{
          json = JSON.stringify(json, null, 2);
        }}
        json = json.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        return json.replace(/(\"(\\\\u[a-zA-Z0-9]{{4}}|\\\\[^u]|[^\\\\\"])*\"\\s*:)|(\"(\\\\u[a-zA-Z0-9]{{4}}|\\\\[^u]|[^\\\\\"])*\")|\\b(true|false|null)\\b|-?\\d+(?:\\.\\d*)?(?:[eE][+\\-]?\\d+)?/g,
          function (match) {{
            let cls = "json-number";
            if (/^\"/.test(match)) {{
              cls = /:$/.test(match) ? "json-key" : "json-string";
            }} else if (/true|false/.test(match)) {{
              cls = "json-boolean";
            }} else if (/null/.test(match)) {{
              cls = "json-null";
            }}
            return '<span class=\"' + cls + '\">' + match + '</span>';
          }});
      }}

      function showResponse(data) {{
        responseBox.innerHTML = syntaxHighlight(data);
      }}

      async function fetchMetrics() {{
        const res = await fetch("/metrics");
        const data = await res.json();
        metricsBox.innerHTML = syntaxHighlight(data);
        uptimeValue.textContent = data.uptime_sec + "s";
        reqValue.textContent = data.requests_total;
      }}

      async function handleAction(action) {{
        try {{
          if (action === "GET /health") {{
            const res = await fetch("/health");
            showResponse(await res.json());
          }} else if (action === "GET /fast") {{
            const res = await fetch("/fast");
            showResponse(await res.json());
          }} else if (action === "POST /slow") {{
            const payload = JSON.parse(document.getElementById("slowPayload").value || "{{}}");
            const res = await fetch("/slow", {{
              method: "POST",
              headers: {{ "Content-Type": "application/json" }},
              body: JSON.stringify(payload),
            }});
            showResponse(await res.json());
          }} else if (action === "GET /external") {{
            const url = encodeURIComponent(document.getElementById("externalUrl").value);
            const res = await fetch(`/external?url=${{url}}`);
            showResponse(await res.json());
          }} else if (action === "GET /error/404") {{
            const res = await fetch("/error/404");
            showResponse(await res.json());
          }} else if (action === "GET /error/500") {{
            const res = await fetch("/error/500");
            showResponse(await res.json());
          }} else if (action === "POST /upload") {{
            const fileInput = document.getElementById("uploadFile");
            if (!fileInput.files.length) {{
              showResponse({{ error: "select_file_first" }});
              return;
            }}
            const form = new FormData();
            form.append("file", fileInput.files[0]);
            const res = await fetch("/upload", {{ method: "POST", body: form }});
            showResponse(await res.json());
          }} else if (action === "GET /download") {{
            const size = document.getElementById("downloadSize").value || "1024";
            const res = await fetch(`/download?size=${{size}}`);
            const blob = await res.blob();
            showResponse({{ size_bytes: blob.size, content_type: blob.type }});
          }} else if (action === "WS /ws") {{
            if (ws) {{
              ws.close();
              ws = null;
              showResponse({{ websocket: "closed" }});
              return;
            }}
            ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
            ws.onmessage = (evt) => showResponse({{ websocket: "message", data: evt.data }});
            ws.onopen = () => showResponse({{ websocket: "open" }});
            ws.onclose = () => showResponse({{ websocket: "closed" }});
          }}
          await fetchMetrics();
        }} catch (err) {{
          showResponse({{ error: String(err) }});
        }}
      }}

      document.querySelectorAll("[data-action]").forEach((btn) => {{
        btn.addEventListener("click", () => handleAction(btn.dataset.action));
      }});

      document.getElementById("wsSend").addEventListener("click", () => {{
        if (!ws || ws.readyState !== WebSocket.OPEN) {{
          showResponse({{ error: "websocket_not_connected" }});
          return;
        }}
        const msg = document.getElementById("wsMessage").value || "";
        ws.send(msg);
      }});

      modeToggle.addEventListener("click", () => {{
        const body = document.body;
        const dark = body.classList.contains("bg-slate-950");
        body.classList.toggle("bg-slate-950", !dark);
        body.classList.toggle("text-slate-100", !dark);
        body.classList.toggle("bg-slate-50", dark);
        body.classList.toggle("text-slate-900", dark);
      }});

      fetchMetrics();
    </script>
  </body>
</html>
"""
