"""
snapadmin/encryption/blind_index.py

Equality matching on a column the database cannot read.

Encryption at rest takes every lookup with it. The database sees random bytes,
so ``icontains``, ``gt``, ``startswith`` and ``ORDER BY`` are *impossible* — not
slow, not unsupported, impossible — and even plain ``exact`` compares against a
ciphertext carrying a fresh random nonce, which matches nothing every time. One
of those lookups matters far more than the rest: finding the row for a known
value. That is what a blind index restores, and only that.

**What it is.** A sibling column, ``<field>_bi``, holding
``HMAC-SHA256(index key, NFC(value))`` in base64url. Writes fill it from the
plaintext; an ``exact`` or ``in`` lookup on the encrypted field is rewritten
onto this column, so the query the database runs mentions no secret at all.

**The index key is never the encryption key.** It is HKDF-derived from a keyset
entry, with the field's own ``app.model.field`` as the derivation info, so the
index for one column cannot be correlated with the index for another even when
both hold the same value, and a leaked index reveals nothing usable about the
key that decrypts the data.

**Rotation.** Writes index under the *active* key; lookups try every key in the
keyset (:func:`index_candidates`). Prepending a new key therefore keeps existing
rows findable with no downtime and no rebuild, exactly as it keeps them
readable. ``snapadmin_encrypt_fields --rotate`` converges the stored indexes
afterwards so the old key can eventually be dropped.

**The leak, stated plainly, because it is the whole trade.** A blind index makes
**equality observable**: two rows with the same value have the same index, and
anyone who can read the column can see that — count duplicates, spot the most
common value, confirm a guess. Against a low-entropy column with a known format
(a national ID, a postcode, a birth date) that is a dictionary attack away from
the value itself. It is a reasonable trade for an email address, where equality
is what you are storing the column for, and a bad one for a national ID. The
default is off, deliberately.

Normalisation is Unicode NFC and nothing else — in particular **case is
significant**, matching Django's own ``exact``, which is case-sensitive on every
supported backend. Two spellings that differ in case are two values here, the
same as they would be on an unencrypted column.

Stdlib only: ``hmac`` and ``hashlib`` cover HKDF and the index itself, so this
module works in a container that never installed the ``[encryption]`` extra.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import unicodedata

from snapadmin.encryption.keys import EncryptionKey, Keyset, require_keyset

#: Length of the derived index key and of the HMAC, in bytes.
INDEX_BYTES = 32

#: Characters an index occupies once base64url-encoded without padding —
#: the ``max_length`` of the sibling column.
INDEX_CHARS = 43

#: Domain separation for the HKDF extract step. Fixed, public, and different
#: from every other digest this package computes.
_HKDF_SALT = b"snapadmin/encryption/blind-index/v1"

#: Prefix for the HKDF info string, which carries the field's identity.
_HKDF_INFO = b"snapadmin/blind-index/"


def normalize(value: str) -> str:
    """The canonical form a value is indexed in.

    NFC only. Two spellings that differ merely in Unicode composition are one
    value — a user typing ``é`` as one codepoint or as ``e`` plus a combining
    accent means the same thing, and an index that disagreed would lose the row.
    Case is deliberately *not* folded: ``exact`` is case-sensitive on an
    unencrypted column, and encryption is not a licence to change that.
    """
    return unicodedata.normalize("NFC", value)


def derive_index_key(key: EncryptionKey, *, aad: str) -> bytes:
    """HKDF-SHA256 a per-column index key out of one keyset entry.

    Two separations matter here and both are cheap. The **salt** keeps this
    derivation apart from every other use of the key, so the index key is not
    the encryption key and learning one says nothing about the other. The
    **info** is the field's own ``app.model.field``, so the same value stored in
    two columns produces two unrelated indexes and neither column can be used to
    probe the other.
    """
    prk = hmac.new(_HKDF_SALT, key.material, hashlib.sha256).digest()
    # One HKDF-Expand block is enough: SHA-256 outputs exactly INDEX_BYTES.
    return hmac.new(prk, _HKDF_INFO + aad.encode("utf-8") + b"\x01", hashlib.sha256).digest()[
        :INDEX_BYTES
    ]


def index_value(plaintext: str, *, aad: str, key: EncryptionKey) -> str:
    """The blind index of *plaintext* for one column under one key."""
    material = derive_index_key(key, aad=aad)
    digest = hmac.new(material, normalize(plaintext).encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def index_for_write(plaintext: str, *, aad: str, keyset: Keyset | None = None) -> str:
    """The index to store — always under the keyset's **active** key.

    Writing under the active key is what makes a rotation converge: every row
    touched after the new key is prepended carries the new index, and
    ``snapadmin_encrypt_fields --rotate`` finishes the rest.
    """
    keyset = keyset if keyset is not None else require_keyset()
    return index_value(plaintext, aad=aad, key=keyset.active)


def index_candidates(plaintext: str, *, aad: str, keyset: Keyset | None = None) -> list[str]:
    """Every index *plaintext* could be stored under, newest key first.

    A lookup matches against all of them. During a rotation the table holds a
    mixture — rows written before the new key was prepended carry the old
    index — and a query that only tried the active key would silently stop
    finding half the table, which is precisely the invisible-data failure this
    whole feature exists to avoid.
    """
    keyset = keyset if keyset is not None else require_keyset()
    return [index_value(plaintext, aad=aad, key=key) for key in keyset]


__all__ = [
    "INDEX_BYTES",
    "INDEX_CHARS",
    "derive_index_key",
    "index_candidates",
    "index_for_write",
    "index_value",
    "normalize",
]
