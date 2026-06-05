"""AWS MSK producer — aiokafka with IAM/OAUTHBEARER token provider.

Mirrors the proven FuelRetail-Demo pattern: aws_msk_iam_sasl_signer generates a
short-lived auth token; aiokafka rotates it via the OAUTHBEARER mechanism.
TLS is enabled via SASL_SSL.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from aiokafka import AIOKafkaProducer
from aiokafka.abc import AbstractTokenProvider
from aiokafka.helpers import create_ssl_context
from aws_msk_iam_sasl_signer import MSKAuthTokenProvider

from app.config import settings
from app.core.errors import MSKProduceFailed
from app.core.logging import get_logger

logger = get_logger(__name__)


class _MSKTokenProvider(AbstractTokenProvider):
    """aiokafka calls token() each refresh; signer returns (token, expiry_ms).

    Must subclass `aiokafka.abc.AbstractTokenProvider` — aiokafka 0.13+
    raises ValueError at producer construction time when the provider
    isn't a registered subclass.
    """

    def __init__(self, region: str) -> None:
        self._region = region

    async def token(self) -> str:
        token, _expiry_ms = MSKAuthTokenProvider.generate_auth_token(self._region)
        return token


class MSKProducer:
    def __init__(self) -> None:
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        if self._producer is not None:
            return
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            security_protocol="SASL_SSL",
            sasl_mechanism="OAUTHBEARER",
            sasl_oauth_token_provider=_MSKTokenProvider(settings.aws_region),
            ssl_context=create_ssl_context(),
            value_serializer=lambda v: json.dumps(v, default=_default).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if isinstance(k, str) else k,
            acks="all",
            enable_idempotence=True,
            request_timeout_ms=30_000,
        )
        await self._producer.start()
        logger.info("msk_producer_started", brokers=settings.kafka_bootstrap_servers)

    async def stop(self) -> None:
        if self._producer is None:
            return
        await self._producer.stop()
        self._producer = None
        logger.info("msk_producer_stopped")

    async def send(self, topic: str, value: dict[str, Any], *, key: str | None = None) -> None:
        if self._producer is None:
            raise MSKProduceFailed("MSK producer is not started")
        try:
            await self._producer.send_and_wait(topic, value=value, key=key)
        except Exception as exc:  # noqa: BLE001
            logger.error("msk_send_failed", topic=topic, error=str(exc))
            raise MSKProduceFailed(f"send to {topic} failed: {exc}") from exc


def _default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"not serialisable: {type(o)}")
