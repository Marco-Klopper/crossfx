"""
app.services.settlement_queue.SettlementQueue — the Redis Streams
settlement queue. The Redis client is injected, so these run without a
broker.
"""
import uuid
from unittest.mock import MagicMock

import pytest
import redis

from app.services.settlement_queue import SettlementQueue

STREAM = "test-settlement-stream"
GROUP = "test-settlement-workers"


@pytest.fixture()
def client():
    return MagicMock()


@pytest.fixture()
def queue(client):
    return SettlementQueue(client, stream=STREAM, group=GROUP, block_ms=10)


class TestConnection:
    def test_client_is_not_built_until_used(self):
        """
        Constructing this — and therefore importing the API — must not
        require a running Redis.
        """
        assert SettlementQueue()._client is None

    def test_an_injected_client_is_used_as_is(self, client):
        assert SettlementQueue(client).client is client


class TestPublish:
    def test_carries_only_identifiers(self, queue, client):
        """
        A settlement message must not carry amounts or addresses: the
        worker reads those from the Remittance row, so a tampered
        message cannot change what gets settled.
        """
        queue.publish("idem-1", uuid.uuid4())

        stream, fields = client.xadd.call_args[0]
        assert stream == STREAM
        assert set(fields) == {"idempotency_key", "remittance_id"}

    def test_stringifies_a_uuid(self, queue, client):
        remittance_id = uuid.uuid4()
        queue.publish("idem-2", remittance_id)

        _stream, fields = client.xadd.call_args[0]
        assert fields["remittance_id"] == str(remittance_id)


class TestConsumerGroup:
    def test_creates_the_stream_too(self, queue, client):
        queue.ensure_consumer_group()

        kwargs = client.xgroup_create.call_args.kwargs
        assert kwargs["groupname"] == GROUP
        # mkstream lets a worker start before the first remittance.
        assert kwargs["mkstream"] is True

    def test_tolerates_an_existing_group(self, queue, client):
        """BUSYGROUP is normal on every start after the first."""
        client.xgroup_create.side_effect = redis.ResponseError(
            "BUSYGROUP Consumer Group name already exists"
        )
        queue.ensure_consumer_group()  # must not raise

    def test_propagates_other_errors(self, queue, client):
        client.xgroup_create.side_effect = redis.ResponseError(
            "NOAUTH Authentication required"
        )
        with pytest.raises(redis.ResponseError):
            queue.ensure_consumer_group()


class TestReads:
    def test_read_new_unwraps_the_stream_response(self, queue, client):
        client.xreadgroup.return_value = [
            (STREAM, [("1700000000-0", {"idempotency_key": "idem-1"})])
        ]

        entries = queue.read_new("worker-1")

        assert entries == [("1700000000-0", {"idempotency_key": "idem-1"})]
        assert client.xreadgroup.call_args.kwargs["consumername"] == "worker-1"

    def test_read_new_returns_empty_on_idle_timeout(self, queue, client):
        client.xreadgroup.return_value = None
        assert queue.read_new("worker-1") == []

    def test_read_new_asks_only_for_undelivered(self, queue, client):
        client.xreadgroup.return_value = None
        queue.read_new("worker-1")
        assert client.xreadgroup.call_args.kwargs["streams"] == {STREAM: ">"}

    def test_read_pending_asks_for_this_consumers_unacked(
        self, queue, client
    ):
        """Id "0" is what was delivered to this consumer but unacked."""
        client.xreadgroup.return_value = None
        queue.read_pending("worker-1")
        assert client.xreadgroup.call_args.kwargs["streams"] == {STREAM: "0"}


class TestAcknowledge:
    def test_acks_in_the_group(self, queue, client):
        queue.acknowledge("1700000000-0")
        client.xack.assert_called_once_with(STREAM, GROUP, "1700000000-0")
