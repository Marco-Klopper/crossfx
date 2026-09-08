"""
The settlement message queue — Redis Streams.

Implements steps 2-3 of the brief's mandated asynchronous flow: a
settlement message is added to the queue when ZAR cash-in is confirmed,
and a worker reads it. Redis Streams is one of the brokers the brief
names, and is used here with a *consumer group* rather than a plain
XREAD so the queue has the properties settlement actually needs:

  - at-least-once delivery: a message stays pending until it is acked,
    so a worker that dies mid-settlement does not drop a remittance
  - competing consumers: N workers can share one stream, which is what
    makes Track 4's queue-throughput benchmark meaningful
  - redelivery: which is precisely why the worker must be idempotent
    (spec §9.5)

Publisher contract for Track 3's cash-in confirmation:

    SettlementQueue().publish(
        remittance.idempotency_key, remittance.id
    )
"""
import logging

import redis

from app.config import settings

logger = logging.getLogger(__name__)


class SettlementQueue:
    """
    One stream and one consumer group.

    The Redis connection is created lazily, on first use, so
    constructing this (or importing anything that does, including the
    API) does not require a running Redis.
    """

    def __init__(
        self,
        client: redis.Redis | None = None,
        *,
        stream: str | None = None,
        group: str | None = None,
        block_ms: int | None = None,
    ) -> None:
        self._client = client
        self.stream = stream or settings.settlement_stream_name
        self.group = group or settings.settlement_consumer_group
        self.block_ms = (
            block_ms if block_ms is not None else settings.settlement_block_ms
        )

    @property
    def client(self) -> redis.Redis:
        if self._client is None:
            # decode_responses keeps stream fields as str rather than
            # bytes, so callers never have to track which they hold.
            self._client = redis.Redis.from_url(
                settings.redis_url, decode_responses=True
            )
        return self._client

    def ensure_consumer_group(self) -> None:
        """
        Idempotently creates the consumer group, and the stream with it
        (MKSTREAM) so a worker can start before the first remittance
        exists.
        """
        try:
            self.client.xgroup_create(
                name=self.stream,
                groupname=self.group,
                id="0",
                mkstream=True,
            )
            logger.info("Created consumer group %s", self.group)
        except redis.ResponseError as exc:
            # BUSYGROUP means it already exists, which is the normal
            # case on every start after the first. Anything else is a
            # real failure.
            if "BUSYGROUP" not in str(exc):
                raise

    def publish(self, idempotency_key: str, remittance_id) -> str:
        """
        Adds a settlement message and returns its stream entry ID.

        The message carries only identifiers — never amounts,
        addresses or key material. The worker re-reads the
        authoritative figures from the Remittance row, so a stale or
        tampered message cannot change what gets settled.
        """
        return self.client.xadd(
            self.stream,
            {
                "idempotency_key": str(idempotency_key),
                "remittance_id": str(remittance_id),
            },
        )

    def read_new(self, consumer: str, count: int = 1) -> list:
        """
        Blocking read of undelivered messages. Returns a list of
        (entry_id, fields) tuples — empty when the block timeout
        expires with nothing new, which is the normal idle case.
        """
        response = self.client.xreadgroup(
            groupname=self.group,
            consumername=consumer,
            streams={self.stream: ">"},
            count=count,
            block=self.block_ms,
        )
        return self._entries(response)

    def read_pending(self, consumer: str, count: int = 10) -> list:
        """
        Messages this consumer was delivered but never acked — what was
        in flight when it last crashed. A worker drains these on
        startup before taking new work, which is the half of
        at-least-once delivery that only matters once something has
        already gone wrong.
        """
        response = self.client.xreadgroup(
            groupname=self.group,
            consumername=consumer,
            streams={self.stream: "0"},
            count=count,
        )
        return self._entries(response)

    def acknowledge(self, entry_id: str) -> None:
        """
        Marks a message done. Called only after the settlement
        transaction has committed — acking earlier would turn a worker
        crash into a lost remittance.
        """
        self.client.xack(self.stream, self.group, entry_id)

    def depth(self) -> int:
        """Stream length, for Track 4's load-test metrics surface."""
        return self.client.xlen(self.stream)

    @staticmethod
    def _entries(response) -> list:
        # xreadgroup returns [(stream_name, [(entry_id, fields), ...])];
        # only one stream is ever read here.
        if not response:
            return []
        return response[0][1]
