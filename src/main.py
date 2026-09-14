import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse
from sqlalchemy import text

from src.auth import get_current_user
from src.audit import AuditLog, request_id_context
from src.cache import redis_client
from src.config import ORTHANC_URL, orthanc_auth
from src.jobs import FailedInstance, JobRecord
from src.database import DATABASE_URL, SessionLocal
from src.metrics import REQUEST_COUNT, REQUEST_LATENCY, metrics_payload
from src.ratelimit import _client_identifier, rate_limit
from src.routers import auth, studies, instances, upload, forward, jobs, admin, query
from src.worker import celery_app


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_context.get(),
        }
        for field in ("method", "path", "status_code", "duration_ms"):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload)


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="MSV-med PACS API",
    description="Upload, forward, anonymize, and query DICOM studies via Orthanc.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    token = request_id_context.set(request_id)
    started = time.perf_counter()
    response = None
    try:
        special_route = (
            (request.method == "POST" and request.url.path == "/studies")
            or (request.method == "POST" and request.url.path.endswith("/forward"))
            or (request.method == "POST" and request.url.path == "/query/ingest/all")
            or (request.method == "GET" and request.url.path == "/query/search")
        )
        if request.method != "OPTIONS" and request.url.path != "/health/live" and not special_route:
            allowed, remaining = rate_limit(
                f"ratelimit:default:{_client_identifier(request)}", 120, 60
            )
            request.state.rate_limit = {"limit": 120, "remaining": remaining}
            if not allowed:
                response = JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded"},
                    headers={"Retry-After": "60"},
                )
                return response
        response = await call_next(request)
        return response
    except Exception:
        logging.getLogger("msv-med.request").exception(
            "%s %s failed", request.method, request.url.path,
            extra={"request_id": request_id},
        )
        raise
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        status_code = response.status_code if response is not None else 500
        route = getattr(request.scope.get("route"), "path", request.url.path)
        REQUEST_COUNT.labels(request.method, route, str(status_code)).inc()
        REQUEST_LATENCY.labels(request.method, route).observe(time.perf_counter() - started)
        logging.getLogger("msv-med.request").info(
            "%s %s %s %.2fms",
            request.method,
            request.url.path,
            status_code,
            duration_ms,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration_ms": duration_ms,
            },
        )
        if response is not None:
            rate_state = getattr(request.state, "rate_limit", {})
            response.headers["X-RateLimit-Limit"] = str(rate_state.get("limit", 120))
            response.headers["X-RateLimit-Remaining"] = str(rate_state.get("remaining", 0))
            response.headers["X-Request-ID"] = request_id
        request_id_context.reset(token)

# Keep the original routes for existing clients while exposing a stable API namespace.
api_routers = (
    studies.router,
    instances.router,
    upload.router,
    forward.router,
    auth.router,
    jobs.router,
    admin.router,
    query.router,
)
for api_router in api_routers:
    app.include_router(api_router)
    app.include_router(api_router, prefix="/api/v1")


def init_db():
    auth.ensure_admin_user()


@app.get("/", tags=["Health"])
def root(_current_user=Depends(get_current_user)):
    return {"status": "MSV-med API running", "version": "1.0.0"}


@app.get("/health", tags=["Health"])
def health(_current_user=Depends(get_current_user)):
    checked_at = datetime.now(timezone.utc).isoformat()
    pacs = _check_pacs()
    database = _check_database()
    redis = _check_redis()
    worker = _check_worker()

    if database["status"] == "error" or redis["status"] == "error":
        overall_status = "unhealthy"
    elif pacs["status"] == "error" or worker["status"] != "ok":
        overall_status = "degraded"
    else:
        overall_status = "healthy"

    payload = {
        "api": "ok",
        "status": overall_status,
        "pacs_reachable": pacs["status"] == "ok",
        "components": {
            "api": {"status": "ok"},
            "pacs": pacs,
            "database": database,
            "redis": redis,
            "worker": worker,
        },
        "checked_at": checked_at,
    }
    return payload


def _check_pacs() -> dict:
    started = time.perf_counter()
    try:
        response = httpx.get(f"{ORTHANC_URL}/system", auth=orthanc_auth(), timeout=3)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        if response.status_code == 200:
            return {"status": "ok", "latency_ms": latency_ms}
        return {
            "status": "error",
            "latency_ms": latency_ms,
            "error": f"PACS returned HTTP {response.status_code}",
        }
    except Exception as exc:
        return {
            "status": "error",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": str(exc),
        }


def _check_database() -> dict:
    db = SessionLocal()
    try:
        db.execute(text("SET LOCAL statement_timeout = 2000"))
        db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    finally:
        db.close()


def _check_redis() -> dict:
    try:
        redis_client.ping()
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _check_worker() -> dict:
    try:
        responding_workers = celery_app.control.inspect(timeout=2).ping() or {}
        online = len(responding_workers)
        if online == 0:
            return {"status": "no_workers", "online": 0}
        return {"status": "ok", "online": online}
    except Exception as exc:
        return {"status": "error", "online": 0, "error": str(exc)}


@app.get("/health/live", tags=["Health"])
def health_live():
    return {"status": "ok"}


@app.get("/metrics", include_in_schema=False)
def metrics():
    return Response(metrics_payload(), media_type="text/plain; version=0.0.4")


@app.get("/health/ready", tags=["Health"])
def health_ready():
    database = _check_database()
    redis = _check_redis()
    content = {
        "status": "ready" if database["status"] == "ok" and redis["status"] == "ok" else "not_ready",
        "components": {"database": database, "redis": redis},
    }
    if content["status"] == "not_ready":
        return JSONResponse(status_code=503, content=content)
    return content
