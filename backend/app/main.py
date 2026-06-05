"""FastAPI application entry point.

Lifespan:
  - on startup: connect Mongo (PyMongo AsyncMongoClient)
  - on shutdown: disconnect

Workers run as separate process entry points (see app.workers.*) so the
API process stays a single async event loop.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.errors import AcmeError
from app.core.logging import configure_logging, get_logger
from app.deps import connect_mongo, disconnect_mongo, get_db
from app.repositories.charge_code_repo import ChargeCodeRepository
from app.routes import (
    analyst,
    atlas,
    before_after,
    customers,
    dashboard,
    drift,
    features,
    health,
    metrics,
    quarantine,
    rules,
    stream,
    system_metrics,
)
from app.services import sse_change_stream_tail
from app.services.charge_code_cache import charge_code_cache

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info("acme_backend_starting")
    await connect_mongo()
    # PR-5: warm the charge-code catalog cache so the first transaction
    # write doesn't pay the cold-start price. The change-stream watcher
    # (`app.workers.charge_code_change_watcher`) keeps the cache coherent
    # across processes thereafter; absence of the watcher just means
    # admin edits land on next process restart.
    try:
        await charge_code_cache.load(ChargeCodeRepository(get_db()))
    except Exception as exc:  # noqa: BLE001
        logger.warning("charge_code_cache_warmup_failed", error=str(exc))
    try:
        from app.services.quarantine_iforest_scorer import init_scorer

        init_scorer()
    except Exception as exc:  # noqa: BLE001
        logger.warning("quarantine_iforest_init_failed", error=str(exc))
    # In-process change-stream tail that bridges writes (from ASP, the EC2
    # MSK consumer, or local workers) into SSE for dashboard clients
    # attached to this API process. Without it, `publish_new_transaction`
    # / `publish_new_case` only fire when the write happens inside this
    # process — which never happens for the MSK-driven topology.
    sse_tail = None
    try:
        sse_tail = sse_change_stream_tail.start(get_db())
        logger.info("sse_tail.started")
    except Exception as exc:  # noqa: BLE001
        logger.warning("sse_tail.start_failed", error=str(exc))
    try:
        yield
    finally:
        if sse_tail is not None:
            try:
                await sse_change_stream_tail.stop(sse_tail)
            except Exception as exc:  # noqa: BLE001
                logger.warning("sse_tail.stop_failed", error=str(exc))
        await disconnect_mongo()
        logger.info("acme_backend_stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Streaming Billing — Quarantine Intelligence",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AcmeError)
    async def _acme_error_handler(_request: Request, exc: AcmeError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": str(exc)}},
        )

    app.include_router(health.router)
    app.include_router(stream.router)
    app.include_router(customers.router)
    app.include_router(quarantine.router)
    app.include_router(rules.router)
    app.include_router(analyst.router)
    app.include_router(drift.router)
    app.include_router(features.router)
    app.include_router(system_metrics.router)
    app.include_router(before_after.router)
    app.include_router(metrics.router)
    app.include_router(dashboard.router)
    app.include_router(atlas.router)
    return app


app = create_app()
