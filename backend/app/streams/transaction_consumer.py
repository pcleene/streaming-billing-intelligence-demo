"""MSK transaction consumer — pulls events and writes via insert_extref.

Pairs with `app.workers.transaction_simulator` (the producer side) and
the V3 write path in `transaction_repo.insert_extref`.

Per-batch contract:
- One `BatchCache(cycle_repo=...)` is shared across the messages in a
  poll batch so repeated customer + cycle lookups don't hammer Mongo.
- Each message becomes one `insert_extref(event, cache=cache)` call.
- Unknown-customer (`KeyError`) is logged + skipped — at-least-once
  delivery means we tolerate stale producer state without poisoning the
  partition. Other exceptions are logged + skipped and counted toward
  the batch `errors` total so an operator alert can fire.
- Offsets commit after each batch's `process_batch(...)` returns. We do
  not commit per-message — duplicate inserts would be cheaper than a
  silently-dropped offset.

Run via:
    python -m app.streams.transaction_consumer
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
from typing import Any

from aiokafka import AIOKafkaConsumer
from aiokafka.helpers import create_ssl_context

from app.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.metrics import transactions_window
from app.deps import connect_mongo, disconnect_mongo, get_db
from app.repositories.bill_cycle_repo import BillCycleRepository
from app.repositories.charge_code_repo import ChargeCodeRepository
from app.repositories.transaction_repo import BatchCache, TransactionRepository
from app.routes.stream import publish_new_transaction
from app.services.charge_code_cache import charge_code_cache
from app.streaming.msk_client import _MSKTokenProvider

logger = get_logger(__name__)

# How long getmany() waits for a batch before yielding (ms).
POLL_TIMEOUT_MS = 1_000
# Cap on messages per batch — keeps BatchCache memory bounded.
MAX_RECORDS_PER_BATCH = 500
DEFAULT_GROUP_ID = "acme-billing-extref-consumer"


def _decorate_event(value: dict, *, topic: str, partition: int, offset: int) -> dict:
    """Stamp source-trace metadata that `insert_extref` persists on the doc."""
    value.setdefault("_source_topic", topic)
    value.setdefault("_source_partition", partition)
    value.setdefault("_source_offset", offset)
    return value


class TransactionConsumer:
    """aiokafka consumer that fans every event through `insert_extref`."""

    def __init__(
        self,
        *,
        repository: TransactionRepository | None = None,
        topic: str | None = None,
        group_id: str = DEFAULT_GROUP_ID,
    ) -> None:
        self._consumer: AIOKafkaConsumer | None = None
        self._repository = repository
        self._topic = topic or settings.kafka_topic
        self._group_id = group_id
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._repository is None:
            db = get_db()
            self._repository = TransactionRepository(
                db, cycle_repo=BillCycleRepository(db)
            )
            # Warm the catalog cache so the first event doesn't pay
            # cold-start. The change-stream watcher keeps it coherent.
            try:
                await charge_code_cache.load(ChargeCodeRepository(db))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "transaction_consumer_charge_code_warmup_failed",
                    error=str(exc),
                )
        self._consumer = AIOKafkaConsumer(
            self._topic,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            security_protocol="SASL_SSL",
            sasl_mechanism="OAUTHBEARER",
            sasl_oauth_token_provider=_MSKTokenProvider(settings.aws_region),
            ssl_context=create_ssl_context(),
            group_id=self._group_id,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            key_deserializer=lambda k: k.decode("utf-8") if k is not None else None,
            enable_auto_commit=False,
            auto_offset_reset="latest",
            max_poll_records=MAX_RECORDS_PER_BATCH,
        )
        await self._consumer.start()
        logger.info(
            "transaction_consumer_started",
            topic=self._topic,
            group=self._group_id,
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None
        logger.info("transaction_consumer_stopped")

    async def run(self) -> None:
        if self._consumer is None or self._repository is None:
            raise RuntimeError("TransactionConsumer.start() must be called first")
        while not self._stop.is_set():
            batches = await self._consumer.getmany(
                timeout_ms=POLL_TIMEOUT_MS,
                max_records=MAX_RECORDS_PER_BATCH,
            )
            if not batches:
                continue
            messages: list[Any] = []
            for tp_msgs in batches.values():
                messages.extend(tp_msgs)
            await self.process_batch(messages)
            await self._consumer.commit()

    async def process_batch(
        self,
        messages: list[Any],
        *,
        cache: BatchCache | None = None,
    ) -> dict[str, int]:
        """Insert one Kafka batch via the V3 write path.

        Returns `{accepted, skipped_unknown, errors}` so callers and tests
        can assert on outcomes. A single bad event must not poison the
        batch: unknown-customer → log + skip; other exceptions → log +
        skip and count toward `errors` so an alert can fire.
        """
        if self._repository is None:
            raise RuntimeError("TransactionConsumer.start() must be called first")
        cache = cache or BatchCache(cycle_repo=self._repository._cycle_repo)
        accepted = 0
        skipped_unknown = 0
        errors = 0
        for msg in messages:
            event = _decorate_event(
                dict(msg.value),
                topic=msg.topic,
                partition=msg.partition,
                offset=msg.offset,
            )
            try:
                await self._repository.insert_extref(event, cache=cache)
                accepted += 1
                transactions_window.record()
                # Publish a compact projection for the dashboard's live
                # feed. Narrow try/except so any publish failure can't
                # poison the batch (SSE is best-effort by design).
                try:
                    publish_new_transaction(
                        {
                            "transaction_id": event.get("transaction_id"),
                            "customer_id": event.get("customer_id"),
                            "amount": event.get("amount"),
                            "charge_code": event.get("charge_code"),
                            "status": event.get("status"),
                            "merchant": event.get("merchant"),
                            "timestamp": event.get("timestamp"),
                            "entity": event.get("entity"),
                            "source_partition": event.get("_source_partition"),
                            "source_offset": event.get("_source_offset"),
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "transaction_consumer_publish_failed",
                        transaction_id=event.get("transaction_id"),
                        error=str(exc),
                    )
            except KeyError as exc:
                logger.warning(
                    "transaction_consumer_skipped_unknown_customer",
                    transaction_id=event.get("transaction_id"),
                    customer_id=event.get("customer_id"),
                    error=str(exc),
                )
                skipped_unknown += 1
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "transaction_consumer_insert_failed",
                    transaction_id=event.get("transaction_id"),
                    customer_id=event.get("customer_id"),
                    error=str(exc),
                )
                errors += 1
        logger.info(
            "transaction_consumer_batch",
            accepted=accepted,
            skipped_unknown=skipped_unknown,
            errors=errors,
        )
        return {
            "accepted": accepted,
            "skipped_unknown": skipped_unknown,
            "errors": errors,
        }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="transaction_consumer")
    parser.add_argument("--topic", default=settings.kafka_topic)
    parser.add_argument("--group-id", default=DEFAULT_GROUP_ID)
    return parser.parse_args()


async def main() -> None:
    configure_logging()
    args = _parse_args()
    await connect_mongo()
    consumer = TransactionConsumer(topic=args.topic, group_id=args.group_id)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(consumer.stop()))
    try:
        await consumer.start()
        await consumer.run()
    finally:
        await consumer.stop()
        await disconnect_mongo()


if __name__ == "__main__":
    asyncio.run(main())
