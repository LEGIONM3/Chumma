import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.errors import (
    DealFlowException,
    dealflow_exception_handler,
    generic_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.core.logging import RequestLoggingMiddleware
from app.db.session import engine

logger = logging.getLogger("dealflow360")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Starting DealFlow360 backend application...")
    try:
        from app.jobs.scheduler import start_scheduler, stop_scheduler
        start_scheduler()
    except Exception as e:
        logger.warning(f"Scheduler initialization deferred: {e}")

    yield

    logger.info("Shutting down DealFlow360 backend application...")
    try:
        from app.jobs.scheduler import stop_scheduler
        stop_scheduler()
    except Exception as e:
        logger.warning(f"Scheduler shutdown skipped: {e}")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Cross-Origin Resource Sharing
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Structured JSON request logging
app.add_middleware(RequestLoggingMiddleware)

# Standardized Error Envelope Exception Handlers
app.add_exception_handler(DealFlowException, dealflow_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)

# Include v1 API router
app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["Health"])
def health_check() -> Dict[str, str]:
    db_status = "connected"
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"disconnected: {str(e)}"

    scheduler_status = "running"
    try:
        from app.jobs.scheduler import scheduler
        if not scheduler or not scheduler.running:
            scheduler_status = "stopped"
    except Exception:
        scheduler_status = "uninitialized"

    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "database": db_status,
        "scheduler": scheduler_status,
        "version": "1.0.0",
    }
