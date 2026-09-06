"""
tests/test_sharding_ids.py — #SHARD1e

``snapadmin.sharding.ids.uuid7`` — a stdlib-only RFC 9562 UUID version 7
generator: opt-in, collision-resistant, time-ordered primary keys for a model
spread across shards.
"""

import time
import uuid

from snapadmin.sharding.ids import uuid7


class TestUuid7:
    def test_returns_a_uuid_instance(self):
        assert isinstance(uuid7(), uuid.UUID)

    def test_version_is_7(self):
        assert uuid7().version == 7

    def test_variant_is_rfc_9562(self):
        assert uuid7().variant == uuid.RFC_4122

    def test_two_calls_are_unique(self):
        assert uuid7() != uuid7()

    def test_many_calls_are_unique(self):
        values = {uuid7() for _ in range(10_000)}
        assert len(values) == 10_000

    def test_sorts_by_creation_time(self):
        """The defining property: string/integer order matches call order,
        so an index built on this column stays roughly append-only."""
        first = uuid7()
        time.sleep(0.002)
        second = uuid7()
        assert first.int < second.int
        assert str(first) < str(second)

    def test_embeds_the_current_millisecond_timestamp(self):
        before = int(time.time() * 1000)
        value = uuid7()
        after = int(time.time() * 1000)

        embedded_millis = value.int >> 80  # top 48 bits
        assert before <= embedded_millis <= after
