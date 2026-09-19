"""
snapadmin/encryption/cipher.py

The cipher behind encrypted model fields: AES-256-GCM over a versioned,
text-safe envelope.

One algorithm, no menu. AES-256-GCM is an AEAD — it encrypts *and*
authenticates, so a modified ciphertext fails to decrypt instead of decrypting
into something else. There is deliberately no setting to choose a different
cipher, no "fast mode" and no fallback: a configurable cipher is a cipher that
gets misconfigured, and the envelope's ``snap1`` version tag is what makes a
second algorithm addable later without a data migration.

**The envelope.** Every encrypted value is stored as one dot-delimited ASCII
string::

    snap1.<key_id>.<base64url nonce>.<base64url ciphertext+tag>

Text, not bytes: the column is a ``TextField``, so ``dumpdata``, fixtures,
``loaddata``, a CSV export and a psql session all keep working. The ``snap1``
prefix makes ciphertext greppable in a dump, makes double-encryption detectable
(:func:`looks_encrypted`), and reserves ``snap2`` for a future format. The
``key_id`` is what makes rotation possible at all: the row itself records which
key opens it, so a keyset can carry an old key purely to read old rows.

**The nonce.** 96 bits, freshly random on every single write —
:func:`secrets.token_bytes`, never a counter and never derived from the value.
Reusing a nonce under the same key breaks GCM completely (it leaks the XOR of
two plaintexts and the authentication key), which is why the test suite asserts
that encrypting one value twice produces two different ciphertexts rather than
trusting this docstring.

**The AAD.** ``app_label.model.field`` is authenticated but not encrypted, so a
ciphertext lifted out of ``patient.ssn`` and pasted into ``note.body`` fails to
decrypt rather than quietly relocating a secret. The binding deliberately
excludes the primary key, so ``INSERT … SELECT`` row copies, fixture loads and
PK-changing restores keep working. The flip side, documented rather than
hidden: **renaming the app, the model or the field changes the AAD**, and rows
written under the old name need re-encrypting (``snapadmin_encrypt_fields``).

**Failure is always loud.** A missing key, an unknown key id, a tampered
payload and a truncated envelope all raise :class:`DecryptionError`. Nothing
here returns ``None``, returns the raw envelope, or swallows a failure — a
half-readable table is worse than an obviously broken one, because it looks
like data loss long after the key that would have fixed it is gone.

**Nothing renders a secret.** No plaintext and no key material reaches an
exception message, a ``repr`` or a log line, on any path. Errors carry a key
*id* and a field path, both of which are already public knowledge. The module
does not log on the encrypt/decrypt path at all: a per-value log line is a
per-value copy of whatever went wrong.

``cryptography`` (Apache-2.0 **and** BSD-3) is loaded lazily through
:func:`_load_cryptography`, mirroring ``sanitize._load_nh3()`` and
``crypto._load_pyrage()``: a project that encrypts nothing never imports it,
and a project that does gets an :class:`~django.core.exceptions.ImproperlyConfigured`
naming ``pip install django-snapadmin[encryption]`` rather than a bare
``ModuleNotFoundError`` from inside a save.
"""

from __future__ import annotations

import base64
import binascii
import functools
import re
import secrets
from dataclasses import dataclass, field as dataclass_field
from typing import Any

from snapadmin.encryption.keys import (
    KEY_ID_PATTERN,
    Keyset,
    displayable_id,
    require_keyset,
)
from snapadmin.logging_config import get_logger

logger = get_logger(__name__)

#: The envelope format this module writes. A future algorithm becomes
#: ``snap2`` and is added here; ``snap1`` rows keep being readable.
ENVELOPE_VERSION = "snap1"

#: Nonce length in bytes (96 bits — the size AES-GCM is specified for).
NONCE_BYTES = 12

#: Number of dot-delimited parts in an envelope.
_ENVELOPE_PARTS = 4

#: Cheap recogniser for "this string is *some* SnapAdmin envelope", including a
#: version this build cannot read. Used before parsing so a ``snap2`` value is
#: never treated as plaintext and encrypted a second time.
_VERSION_PREFIX = re.compile(r"\Asnap[0-9]+\.")

#: base64url alphabet, unpadded — what :func:`_b64encode` emits.
_B64_PATTERN = re.compile(r"\A[A-Za-z0-9_-]+\Z")


class EncryptionError(Exception):
    """Something went wrong encrypting or decrypting a value.

    Carries a key id and a field path at most — never key material, never the
    value it was handed.
    """


class DecryptionError(EncryptionError):
    """A stored value could not be turned back into its plaintext.

    Raised for a malformed envelope, a key id the keyset does not carry, a
    ciphertext bound to a different column, and a payload that failed
    authentication. Never raised *instead* of returning a value: there is no
    path on which a failed decryption yields ``None`` or the raw envelope.
    """


@dataclass(frozen=True)
class _Backend:
    """The two names this module needs from ``cryptography``."""

    aesgcm: Any
    invalid_tag: type[BaseException]


@functools.lru_cache(maxsize=1)
def _load_cryptography() -> _Backend:
    """Import ``cryptography`` lazily, once, with an actionable failure.

    The house lazy-optional-dependency pattern (see ``sanitize._load_nh3()``):
    the import happens the first time something is actually encrypted, and an
    ``ImportError`` becomes an ``ImproperlyConfigured`` naming the extra rather
    than a bare ``ModuleNotFoundError`` surfacing from inside ``Model.save()``.
    """
    # Imported here rather than at module level so that a project which
    # declares no encrypted field never pays for — or needs — the extra.
    from django.core.exceptions import ImproperlyConfigured

    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise ImproperlyConfigured(
            "Field-level encryption needs the `cryptography` library, which could not "
            "be imported. Install the extra that ships it: "
            "`pip install django-snapadmin[encryption]`."
        ) from exc
    return _Backend(aesgcm=AESGCM, invalid_tag=InvalidTag)


def _b64encode(raw: bytes) -> str:
    """base64url without padding — ``=`` adds nothing inside a dotted envelope."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str, *, label: str) -> bytes:
    if not _B64_PATTERN.match(text):
        raise DecryptionError(
            f"Stored value is not a readable SnapAdmin ciphertext: its {label} is not base64url."
        )
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise DecryptionError(
            f"Stored value is not a readable SnapAdmin ciphertext: its {label} could "
            "not be decoded."
        ) from exc


def aad_for(app_label: str, model_name: str, field_name: str) -> str:
    """The additional authenticated data binding a ciphertext to one column.

    The app label and model name are lower-cased — Django normalises both
    already, so this only stops a caller that spelled the model class name
    from producing a second, incompatible binding for the same column.

    **The field name is used verbatim**, deliberately. Folding its case would
    make ``ssn`` and ``SSN`` on one model share a binding, and the binding is
    the only thing stopping a ciphertext being moved between two columns. Two
    such fields are a bizarre thing to declare and Django permits it; weakening
    the AAD to tidy up the spelling would be trading a security boundary for
    nothing.

    Contains no primary key: a row copied to a new pk must still decrypt.
    """
    return f"{app_label.lower()}.{model_name.lower()}.{field_name}"


@dataclass(frozen=True)
class Envelope:
    """A parsed ``snap1.<key_id>.<nonce>.<payload>`` value."""

    key_id: str
    nonce: bytes
    payload: bytes
    version: str = dataclass_field(default=ENVELOPE_VERSION)

    def serialise(self) -> str:
        """The stored text form."""
        return f"{self.version}.{self.key_id}.{_b64encode(self.nonce)}.{_b64encode(self.payload)}"

    @classmethod
    def parse(cls, token: object) -> Envelope:
        """Read a stored value, or raise :class:`DecryptionError` explaining why not.

        Every rejection names the shape problem and never echoes the value: a
        string that failed to parse as an envelope may well be the plaintext
        somebody stored by mistake.
        """
        if not isinstance(token, str):
            raise DecryptionError(
                "Stored value is not a SnapAdmin ciphertext: expected a string, got "
                f"{type(token).__name__}."
            )

        parts = token.split(".")
        if len(parts) != _ENVELOPE_PARTS or not _VERSION_PREFIX.match(token):
            raise DecryptionError(
                "Stored value is not a SnapAdmin ciphertext envelope (expected "
                f"'{ENVELOPE_VERSION}.<key id>.<nonce>.<payload>'). A column that was "
                "switched to an encrypted field still holds its old plaintext until "
                "`python manage.py snapadmin_encrypt_fields --adopt` has run."
            )

        version, key_id, nonce_text, payload_text = parts
        if version != ENVELOPE_VERSION:
            raise DecryptionError(
                f"Stored value uses envelope format {version!r}, which this version of "
                f"SnapAdmin cannot read (it writes {ENVELOPE_VERSION!r}). The row was "
                "written by a newer SnapAdmin — upgrade before reading it."
            )
        if not KEY_ID_PATTERN.match(key_id):
            raise DecryptionError(
                f"Stored value names {displayable_id(key_id)} as its key id, which is "
                "not a usable id. The envelope is corrupt."
            )

        nonce = _b64decode(nonce_text, label="nonce")
        if len(nonce) != NONCE_BYTES:
            raise DecryptionError(
                f"Stored value carries a {len(nonce)}-byte nonce, but {NONCE_BYTES} "
                "bytes are required. The envelope is corrupt."
            )
        return cls(
            key_id=key_id,
            nonce=nonce,
            payload=_b64decode(payload_text, label="payload"),
            version=version,
        )

    def __repr__(self) -> str:
        return f"<Envelope {self.version} key_id={self.key_id!r} payload={len(self.payload)} bytes>"

    __str__ = __repr__


def looks_encrypted(value: object) -> bool:
    """Whether *value* is already a SnapAdmin envelope.

    The idempotency guard: re-saving an instance that was loaded from the
    database must not encrypt the ciphertext a second time, which would make
    the row unreadable with no error anywhere. Deliberately recognises *any*
    ``snapN`` version, including one this build cannot parse — an unreadable
    row is recoverable, a double-encrypted one is not.
    """
    if not isinstance(value, str):
        return False
    return bool(_VERSION_PREFIX.match(value)) and value.count(".") == _ENVELOPE_PARTS - 1


def encrypt(plaintext: str, *, aad: str, keyset: Keyset | None = None) -> str:
    """Encrypt *plaintext* under the keyset's active key, bound to *aad*.

    ``keyset`` defaults to the project's resolved keyset; passing one
    explicitly is what ``snapadmin_encrypt_fields`` and the tests do. Without
    any configured key this raises rather than storing the value — an encrypted
    column never silently receives plaintext.
    """
    if not isinstance(plaintext, str):
        raise EncryptionError(
            f"Only str can be encrypted, got {type(plaintext).__name__}. Encrypted "
            "fields serialise their value to text before it reaches the cipher."
        )
    keyset = keyset if keyset is not None else require_keyset()
    key = keyset.active
    nonce = secrets.token_bytes(NONCE_BYTES)
    payload = (
        _load_cryptography()
        .aesgcm(key.material)
        .encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
    )
    return Envelope(key_id=key.id, nonce=nonce, payload=payload).serialise()


def decrypt(token: str, *, aad: str, keyset: Keyset | None = None) -> str:
    """Decrypt a stored envelope bound to *aad*, or raise :class:`DecryptionError`.

    The two failures worth telling apart, because the remedies differ:

    * the keyset does not carry the key id the row names — a key was dropped
      before the rows written with it were rotated, and the error names the id
      and the ids that *are* available;
    * the key is there but authentication failed — the payload was modified,
      the value was copied out of a different column, or the key under that id
      is not the key the row was written with.
    """
    envelope = Envelope.parse(token)
    keyset = keyset if keyset is not None else require_keyset()
    key = keyset.get(envelope.key_id)
    if key is None:
        raise DecryptionError(
            f"No encryption key with id {displayable_id(envelope.key_id)} is configured, "
            f"so this value cannot be read. Configured key ids: {list(keyset.ids)}. A key "
            "that still has rows written under it must stay in the keyset until "
            "`python manage.py snapadmin_encrypt_fields --rotate` has emptied it."
        )
    backend = _load_cryptography()
    try:
        plaintext = backend.aesgcm(key.material).decrypt(
            envelope.nonce, envelope.payload, aad.encode("utf-8")
        )
    except backend.invalid_tag as exc:
        raise DecryptionError(
            f"Value stored for {aad!r} failed authentication under key "
            f"{displayable_id(envelope.key_id)}. Either the stored bytes were modified, "
            "the ciphertext was copied from a different column (each envelope is bound "
            "to its own app.model.field), or the key configured under that id is not the "
            "key the value was written with."
        ) from exc
    return plaintext.decode("utf-8")
