"""FastAPI Application Server for the Autonomous AI ERP Reporting System.

Mounts:
- Dynamic Lifespan Context Manager pre-warming semantic metastore and SQLite fallback engine
- High-Performance CORS Middleware supporting React & Vite frontend clients
- Healthcheck endpoints (/health and /api/v1/health)
- API v1 Router (/api/v1/reports/query)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from typing import Any, AsyncGenerator

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.reports import router as reports_router
from app.api.v1.capture import router as capture_router
from app.config import get_settings
from app.connectors.executor import DatabaseExecutor
from app.inference.llm_client import LLMClient
from app.metastore.loader import load_metastore

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Lifespan Architecture: Pre-warming Metastore & Database Engine
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Pre-warm system state at startup and cleanly release resources on shutdown."""
    settings = get_settings()
    logger.info("Initializing Autonomous AI ERP Reporting Orchestrator...")

    # 1. Pre-warm Metastore
    try:
        metastore = load_metastore(settings.metastore_path)
        app.state.metastore = metastore
        table_count = len(metastore.get("tables", {}))
        logger.info("Semantic Metastore pre-warmed successfully (%d tables loaded).", table_count)
    except Exception as exc:
        logger.error("Failed to pre-warm semantic metastore: %s", exc)
        app.state.metastore = {"tables": {}, "security": {}}

    # 2. Initialize Database Execution Engine & Pre-provision SQLite fallback
    try:
        executor = DatabaseExecutor(settings=settings)
        # Pre-provision SQLite if in sqlite/auto mode so first request is 0-latency
        if executor.resolve_mode() == "sqlite":
            executor.provision_sqlite()
        app.state.executor = executor
        app.state.db_executor = executor
        logger.info("DatabaseExecutor initialized (Active Mode: %s).", executor.active_mode)
    except Exception as exc:
        logger.error("Failed to initialize DatabaseExecutor: %s", exc)
        app.state.executor = None
        app.state.db_executor = None

    # 3. Pre-warm LLM Client
    try:
        app.state.llm_client = LLMClient(settings=settings)
        logger.info("LLMClient pre-warmed (Provider: %s).", settings.llm_provider)
    except Exception as exc:
        logger.error("Failed to initialize LLMClient: %s", exc)
        app.state.llm_client = None

    yield

    # Shutdown Cleanup
    logger.info("Shutting down Autonomous AI ERP Reporting Orchestrator...")
    if hasattr(app.state, "executor") and app.state.executor is not None:
        try:
            app.state.executor.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# FastAPI Application Factory
# --------------------------------------------------------------------------- #
app = FastAPI(
    title="Autonomous AI ERP Reporting System",
    description="Enterprise natural language reporting orchestrator with self-correction and dynamic UI schema derivation.",
    version="1.0.0",
    lifespan=lifespan,
)

settings = get_settings()

# Configure CORS Middleware
origins = settings.cors_origins if settings.cors_origins else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Healthcheck Endpoints
# --------------------------------------------------------------------------- #
async def health_check() -> dict[str, Any]:
    """System health status reporting operational database mode and LLM provider."""
    current_settings = get_settings()
    executor = getattr(app.state, "executor", None)
    active_mode = executor.active_mode if executor is not None else "sqlite_fallback"
    return {
        "status": "healthy",
        "active_mode": active_mode,
        "database": {
            "mode": active_mode,
            "dialect": current_settings.sql_dialect,
        },
        "llm_provider": current_settings.llm_provider,
        "llm": {
            "provider": current_settings.llm_provider,
            "model": current_settings.llm_model,
        },
        "version": "1.0.0",
    }


# Mount root healthcheck
app.get("/health", tags=["system"])(health_check)

# Mount API v1 Router
api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.get("/health", tags=["system"])(health_check)
api_v1_router.include_router(reports_router)
api_v1_router.include_router(capture_router)

app.include_router(api_v1_router)
