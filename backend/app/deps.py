"""FastAPI dependency providers — DB client lifecycle + service singletons.

PyMongo `AsyncMongoClient` (4.9+) — NOT Motor. See ADR-002.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.config import settings
from app.core.errors import DatabaseError
from app.core.logging import get_logger

logger = get_logger(__name__)


# --- Module-level client singleton -----------------------------------
_client: AsyncMongoClient | None = None
_db: AsyncDatabase | None = None


async def connect_mongo() -> None:
    """Open the MongoDB connection. Called on FastAPI lifespan startup."""
    global _client, _db

    kwargs: dict = {
        "appname": "acme-billing",
        # Conservative pool sizing for the demo; tune for production.
        "maxPoolSize": 100,
        "minPoolSize": 5,
        "serverSelectionTimeoutMS": 5_000,
        "connectTimeoutMS": 10_000,
        "socketTimeoutMS": 30_000,
        # Return BSON Date values as tz-aware UTC datetimes so every
        # downstream JSON encoder emits ISO strings with the `+00:00`
        # offset — without this pymongo returns naive UTC and the
        # browser misreads it as local time (an 8h skew for users in
        # Asia/Kuala_Lumpur).
        "tz_aware": True,
    }
    if settings.tls_cert_path:
        kwargs["tls"] = True
        kwargs["tlsCertificateKeyFile"] = settings.tls_cert_path

    _client = AsyncMongoClient(settings.mongodb_url, **kwargs)
    try:
        await _client.admin.command("ping")
    except Exception as e:  # narrow → DatabaseError at boundary
        raise DatabaseError(f"MongoDB connection failed: {e}") from e

    _db = _client[settings.acme_db]
    logger.info("mongo.connected", db=settings.acme_db)


async def disconnect_mongo() -> None:
    """Close the MongoDB connection on shutdown."""
    global _client, _db
    if _client is not None:
        await _client.close()
        logger.info("mongo.disconnected")
    _client = None
    _db = None


def get_client() -> AsyncMongoClient:
    if _client is None:
        raise DatabaseError("MongoDB client not initialised")
    return _client


def get_db() -> AsyncDatabase:
    if _db is None:
        raise DatabaseError("MongoDB database not initialised")
    return _db


# --- FastAPI annotated aliases ---------------------------------------
DBDep = Annotated[AsyncDatabase, Depends(get_db)]
