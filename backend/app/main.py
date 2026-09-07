import os
import logging

# Ensure Hugging Face / sentence-transformers and Chroma operate in strictly air-gapped / offline mode
# Note: Requires the embedding model to already be cached locally (from prior runs).
# If the cache is ever missing, this will now cause a clear local error instead of a silent network call, which is intentional.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["ANONYMIZED_TELEMETRY"] = "False"

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from app.database import engine, Base
import app.models  # Ensures all models are bound to Base.metadata
from app.routers import health, files, tasks, kb, monitor, memory, graph, audit, auth
from app.monitor.network_watch import start_network_monitor_loop, stop_network_monitor_loop

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Air-gap security confirmations
    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    print("ChromaDB telemetry disabled")
    print("HF offline mode enabled")
    logging.info("ChromaDB telemetry disabled")
    logging.info("HF offline mode enabled")

    try:
        async with engine.begin() as conn:
            if engine.dialect.name == "postgresql":
                await conn.execute(text("SET LOCAL lock_timeout = '2s';"))
            await conn.run_sync(Base.metadata.create_all)
            if engine.dialect.name == "postgresql":
                try:
                    await conn.execute(text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS source_task_id UUID REFERENCES tasks(id);"))
                    await conn.execute(text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS confidence_score FLOAT;"))
                    await conn.execute(text("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'cross_doc_query';"))
                    await conn.execute(text("ALTER TYPE taskstatus ADD VALUE IF NOT EXISTS 'pending_approval';"))
                    await conn.execute(text("ALTER TYPE taskstatus ADD VALUE IF NOT EXISTS 'rejected';"))
                except Exception:
                    pass
            elif engine.dialect.name == "sqlite":
                try:
                    await conn.execute(text("ALTER TABLE tasks ADD COLUMN confidence_score FLOAT;"))
                except Exception:
                    pass
        print(f"Database initialized successfully ({engine.dialect.name})")
        logging.info(f"Database initialized successfully ({engine.dialect.name})")
    except Exception as db_err:
        print(f"Warning: Database unavailable at startup ({db_err})")
        logging.warning(f"Database unavailable at startup: {db_err}")
    # Start background network watcher (2s loop)
    start_network_monitor_loop()
    yield
    stop_network_monitor_loop()

os.makedirs("./storage", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("./storage/backend_debug.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

app = FastAPI(title="Sovereign On-Prem Agentic AI Workbench", version="1.0.0", lifespan=lifespan)

@app.middleware("http")
async def log_requests(request, call_next):
    logging.info(f"Incoming HTTP request: {request.method} {request.url.path}")
    try:
        response = await call_next(request)
        logging.info(f"HTTP response: {request.method} {request.url.path} -> {response.status_code}")
        return response
    except Exception as exc:
        logging.error(f"HTTP unhandled exception on {request.method} {request.url.path}: {exc}", exc_info=True)
        origin = request.headers.get("origin", "*")
        return JSONResponse(
            status_code=500,
            content={
                "detail": f"Internal server error: {str(exc)}",
                "error_type": type(exc).__name__
            },
            headers={
                "Access-Control-Allow-Origin": origin if origin else "*",
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Methods": "*",
                "Access-Control-Allow-Headers": "*",
            }
        )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logging.error(f"Global unhandled exception on {request.method} {request.url.path}: {exc}", exc_info=True)
    origin = request.headers.get("origin", "*")
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"Internal server error: {str(exc)}",
            "error_type": type(exc).__name__
        },
        headers={
            "Access-Control-Allow-Origin": origin if origin else "*",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "*",
            "Access-Control-Allow-Headers": "*",
        }
    )

# Allow all frontend requests and preflight origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:5175",
        "http://127.0.0.1:3000",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:[0-9]+)?$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH", "HEAD"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.include_router(health.router)
app.include_router(files.router)
app.include_router(tasks.router)
app.include_router(kb.router)
app.include_router(memory.router)
app.include_router(graph.router)
app.include_router(monitor.router)
app.include_router(audit.router)
app.include_router(auth.router)
