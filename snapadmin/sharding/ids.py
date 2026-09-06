"""
snapadmin/sharding/ids.py

``uuid7()`` — a distributed, time-ordered primary key helper for a model
spread across shards, so two shards can never mint colliding primary keys the
way two independent auto-increment sequences could.

Opt-in and stdlib-only, deliberately not wired into any ``SnapModel``
automatically: switching a model's primary key type *is* a schema change
(the one explicit exception to this package's "no migration" norm — see
``snapadmin.sharding``'s docs), so a project reaches for this only when it
actually declares a UUID primary key itself::

    from snapadmin.sharding.ids import uuid7

    class Order(SnapModel):
        id = SnapUUIDField(primary_key=True, default=uuid7, editable=False)
        shard_key = "id"

Implements `RFC 9562 <https://www.rfc-editor.org/rfc/rfc9562.html>`_ UUID
version 7: a 48-bit big-endian Unix millisecond timestamp in the high bits,
followed by 74 bits of cryptographically random data (with the 4 version and
2 variant bits RFC 9562 reserves). Sorting by value therefore sorts by
creation time to the millisecond — the property that makes it useful as a
primary key: unlike a random UUIDv4, index writes stay roughly sequential
rather than scattering across the whole keyspace.
"""

from __future__ import annotations

import os
import time
import uuid

#: RFC 9562 reserves the top two bits of octet 8 for the variant; ``10``
#: (``0x80``) is "RFC 9562 variant", the only one this module ever produces.
_VARIANT_BITS = 0x80
_VARIANT_MASK = 0x3F

#: The top nibble of octet 6 carries the version; ``0111`` (``0x70``) is
#: version 7.
_VERSION_BITS = 0x70
_VERSION_MASK = 0x0F


def uuid7() -> uuid.UUID:
    """A fresh, time-ordered UUID version 7 (RFC 9562)."""
    millis = int(time.time() * 1000)
    timestamp = millis.to_bytes(6, "big")
    random_bytes = bytearray(os.urandom(10))

    random_bytes[0] = (random_bytes[0] & _VERSION_MASK) | _VERSION_BITS
    random_bytes[2] = (random_bytes[2] & _VARIANT_MASK) | _VARIANT_BITS

    return uuid.UUID(bytes=timestamp + bytes(random_bytes))
