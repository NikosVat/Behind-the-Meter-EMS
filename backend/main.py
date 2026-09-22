"""FastAPI application entrypoint for Greek Commercial Behind-the-Meter EMS.

Provides:
- Application factory create_app() with lifespan initialization
- Automatic SQLite WAL database initialization and default commercial facility seeding
- Mounted REST routers for telemetry ingestion (/api/v1/telemetry) and facilities (/api/v1/facilities)
- Health check endpoints and CORS configuration
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.database.sqlite_store import get_store
from backend.routes.dashboard import router as dashboard_router
from backend.routes.facilities import router as facilities_router
from backend.routes.market import router as market_router
from backend.routes.optimization import router as optimization_router
from backend.routes.schedules import router as schedules_router
from backend.routes.telemetry import router as telemetry_router
from backend.routes.viber import router as viber_router

logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app(db_path: str | None = None) -> FastAPI:
    """Create and configure FastAPI application instance with lifespan event."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        # Startup: initialize database and seed standard Greek commercial facilities
        target_db = getattr(app.state, "db_path", db_path) or settings.SQLITE_DB_PATH
        logger.info("Initializing SQLite time-series store at: %s", target_db)
        store = get_store(target_db)
        store.init_db()
        store.seed_default_facilities()
        # Initialize market price service
        from backend.market.service import MarketPriceService
        app.state.market_service = MarketPriceService(store=store)
        logger.info("EMS database initialized and default facilities seeded successfully.")
        yield
        # Shutdown cleanup if needed
        logger.info("EMS application shutting down.")

    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        description="Behind-the-Meter Energy Management System for Greek Commercial SMBs (Γ21, Γ22, Γ23)",
        lifespan=lifespan,
    )
    app.state.db_path = db_path

    # Configure CORS for dashboards and authorized clients
    cors_origins = settings.ALLOWED_ORIGINS
    allow_all = "*" in cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=not allow_all,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount API routers under /api/v1
    app.include_router(telemetry_router, prefix=settings.API_V1_STR)
    app.include_router(facilities_router, prefix=settings.API_V1_STR)
    app.include_router(market_router, prefix=settings.API_V1_STR)
    app.include_router(viber_router, prefix=settings.API_V1_STR)
    app.include_router(optimization_router, prefix=settings.API_V1_STR)
    app.include_router(schedules_router, prefix=settings.API_V1_STR)
    app.include_router(dashboard_router)

    @app.get("/", tags=["system"])
    def root() -> dict[str, str]:
        """Root status and metadata endpoint."""
        return {
            "name": settings.PROJECT_NAME,
            "version": settings.VERSION,
            "status": "healthy",
            "environment": settings.ENVIRONMENT,
        }

    @app.get("/health", tags=["system"])
    def health_check() -> dict[str, str]:
        """Health check endpoint for container orchestrators and monitoring probes."""
        return {
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    return app


# Default application instance for uvicorn (e.g. uvicorn backend.main:app)
app = create_app()
