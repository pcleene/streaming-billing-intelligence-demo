"""Idempotent MSK topic creation using aiokafka admin client.

Same OAUTHBEARER auth path as the producer. Topic config matches
FuelRetail-Demo conventions; safe to re-run on each deploy.
"""

from __future__ import annotations

from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError
from aiokafka.helpers import create_ssl_context

from app.config import settings
from app.core.logging import get_logger
from app.streaming.msk_client import _MSKTokenProvider

logger = get_logger(__name__)


async def ensure_topic(
    name: str,
    *,
    partitions: int = 3,
    replication_factor: int = 3,
    retention_ms: int = 7 * 24 * 3600 * 1000,
) -> bool:
    admin = AIOKafkaAdminClient(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        security_protocol="SASL_SSL",
        sasl_mechanism="OAUTHBEARER",
        sasl_oauth_token_provider=_MSKTokenProvider(settings.aws_region),
        ssl_context=create_ssl_context(),
    )
    await admin.start()
    try:
        topic = NewTopic(
            name=name,
            num_partitions=partitions,
            replication_factor=replication_factor,
            topic_configs={"retention.ms": str(retention_ms)},
        )
        try:
            await admin.create_topics([topic])
            logger.info("topic_created", topic=name)
            return True
        except TopicAlreadyExistsError:
            logger.info("topic_exists", topic=name)
            return False
    finally:
        await admin.close()
