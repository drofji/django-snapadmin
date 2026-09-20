"""
snapadmin/encryption/keys.py

Where field-level encryption gets its key material, and the rules that keep
that material out of everything else.

A project turns encryption on by declaring an encrypted field on a model; the
key itself is configured once, through :data:`SNAPADMIN_ENCRYPTION`, and is
resolved here — never read directly by a field, a cipher or a management
command::

    SNAPADMIN_ENCRYPTION = {
        # Ordered keyset: the FIRST key encrypts, EVERY key can decrypt.
        # Rotation = prepend a new key, deploy, re-encrypt, drop the old one.
        "KEYS": [{"id": "2026-09", "key": "<32 bytes, base64url>"}],

        "KEY_PROVIDER": "my_app.secrets.load_snapadmin_keys",  # KMS / Vault hook
        "KEY_FILE": "/run/secrets/snapadmin_encryption",       # mounted secret

        "STRICT": True,   # default: a missing keyset is a startup error
    }

Four sources, most secure first, **first hit wins — sources are never merged**:

1. ``KEY_PROVIDER`` — a dotted path to a callable returning the keyset. The
   escape hatch for KMS / Vault / Secrets Manager: nothing secret touches
   settings *or* the environment. Called once per process (see
   :func:`get_keyset`), so a provider that hits the network is not a per-query
   cost.
2. ``KEY_FILE`` in the dict, or the ``SNAPADMIN_ENCRYPTION_KEY_FILE``
   environment variable — a Docker/Kubernetes secret mount.
3. ``SNAPADMIN_ENCRYPTION_KEYS`` in the environment — the 12-factor / ``.env``
   path, ``id:key`` entries separated by commas or newlines.
4. ``KEYS`` in the settings dict — supported for tests and small deployments,
   but a key in a settings module is a key in version control, so
   :func:`snapadmin.checks.check_encryption_keys` warns about it whenever
   ``DEBUG`` is off.

Two rules this module exists to enforce:

**Never ``SECRET_KEY``.** Deriving the encryption key from Django's
``SECRET_KEY`` is the classic mistake in this area: ``SECRET_KEY`` is rotated
for session and CSRF reasons, and each rotation would silently make every
encrypted column unreadable. SnapAdmin keeps its own keyset, and a startup
check errors if the two are the same value.

**Never render the material.** No ``__repr__``, no ``str()``, no exception
message and no log call in this package emits key bytes — only a key's *id* and
its :attr:`~EncryptionKey.fingerprint`, a short digest that is safe to print,
compare across environments, and show in ``snapadmin_info``. The tests assert
this directly rather than trusting it.

This module is deliberately stdlib-only: resolving and validating a key must
work in a container that has not installed the ``[encryption]`` extra, so
``manage.py check`` can explain the problem instead of failing on an import.
The cipher that consumes these keys lives in :mod:`snapadmin.encryption.cipher`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from snapadmin.conf import get_setting
from snapadmin.logging_config import get_logger

logger = get_logger(__name__)

#: The settings dict every option below is read from.
SETTING_NAME = "SNAPADMIN_ENCRYPTION"

#: Key length in bytes. 32 = AES-256, the only size the cipher accepts; a
#: shorter key is a configuration error, never a silently weaker cipher.
KEY_BYTES = 32

#: ``id:key`` entries, comma- or newline-separated (12-factor / ``.env``).
ENV_KEYS = "SNAPADMIN_ENCRYPTION_KEYS"

#: Path to a file holding the same content (a mounted container secret).
ENV_KEY_FILE = "SNAPADMIN_ENCRYPTION_KEY_FILE"

#: The id given to a key configured without one.
DEFAULT_KEY_ID = "default"

#: A key id is written verbatim into every ciphertext envelope, which is
#: dot-delimited (``snap1.<key_id>.<nonce>.<payload>``) — so a dot, a colon
#: (the ``id:key`` separator) and whitespace are all excluded by construction.
KEY_ID_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")

#: Domain separation for fingerprints: a fingerprint of a key must never
#: collide with a digest of the same bytes computed for some other purpose.
_FINGERPRINT_DOMAIN = b"snapadmin/encryption/fingerprint/v1:"

#: Length of every fingerprint this module produces, matching
#: :func:`snapadmin.crypto.fingerprint`'s convention for age recipients.
_FINGERPRINT_CHARS = 12

#: Sentinel distinguishing "not resolved yet" from the resolved-and-real
#: ``None`` that an unconfigured project produces.
_UNRESOLVED = object()

#: Process-wide cache. See :func:`get_keyset` / :func:`reset_keyset`.
_cached: Any = _UNRESOLVED


class KeySource(str, Enum):
    """Where a resolved keyset came from — reported by ``snapadmin_info``."""

    PROVIDER = "provider"
    FILE = "file"
    ENV = "env"
    SETTINGS = "settings"


#: Longest key id echoed verbatim in an error message. A 32-byte key is 43
#: base64url characters unpadded, 44 padded, so nothing at or below this length
#: can be key material — see :func:`displayable_id`.
_ECHOABLE_ID_CHARS = 16


def displayable_id(key_id: str) -> str:
    """A key id rendered for an error message, or a redaction if it could be a key.

    Configuration is hand-written, and the reliable way to get key material into
    an exception is to write an entry backwards — ``<key>:<id>`` instead of
    ``<id>:<key>``. The "id" is then the key, and every message naming the id
    would print the secret into a traceback, a 500 page and the logs. Anything
    long enough to be a key is therefore described rather than echoed; a real id
    is short and still names itself, so a typo stays diagnosable.
    """
    if len(key_id) <= _ECHOABLE_ID_CHARS:
        return repr(key_id)
    return f"<a {len(key_id)}-character value, not shown>"


def _fingerprint(payload: bytes) -> str:
    return hashlib.sha256(_FINGERPRINT_DOMAIN + payload).hexdigest()[:_FINGERPRINT_CHARS]


def encode_key(material: bytes) -> str:
    """Render raw key bytes as the base64url string configuration accepts.

    A deliberate module-level function rather than a method on
    :class:`EncryptionKey`: encoding is what ``snapadmin_encryption_key`` does
    with material it has just generated, and keeping it off the key object
    means no accidental ``str(key)`` can ever produce something usable.
    """
    return base64.urlsafe_b64encode(material).decode("ascii")


def generate_key() -> str:
    """A fresh, cryptographically random key, ready to paste into configuration."""
    return encode_key(secrets.token_bytes(KEY_BYTES))


@dataclass(frozen=True)
class EncryptionKey:
    """One key: a public id and 32 secret bytes that are never rendered."""

    id: str
    material: bytes

    @classmethod
    def from_encoded(cls, key_id: str, encoded: str) -> EncryptionKey:
        """Validate a configured ``(id, base64url)`` pair into a key.

        Every failure raises :class:`~django.core.exceptions.ImproperlyConfigured`
        naming the key *id* and the problem — never the material it was handed.
        """
        key_id = (key_id or "").strip()
        if not KEY_ID_PATTERN.match(key_id):
            raise ImproperlyConfigured(
                f"{SETTING_NAME}: {displayable_id(key_id)} is not a usable key id — use 1-64 "
                "characters "
                "from A-Z, a-z, 0-9, '-' and '_' (the id is stored in every ciphertext, so "
                "'.', ':' and whitespace are not available)."
            )

        if not isinstance(encoded, str | None):
            # A provider or a settings dict can hand over bytes or a number; the
            # parser below would raise AttributeError/TypeError out of
            # configuration loading instead of saying what is wrong. The value
            # itself is never echoed — it may be the key.
            raise ImproperlyConfigured(
                f"{SETTING_NAME}: key {displayable_id(key_id)} must be a base64url string, got "
                f"{type(encoded).__name__}. Generate one with "
                "`python manage.py snapadmin_encryption_key`."
            )
        candidate = (encoded or "").strip()
        if not candidate:
            raise ImproperlyConfigured(
                f"{SETTING_NAME}: key {displayable_id(key_id)} is empty — generate one with "
                "`python manage.py snapadmin_encryption_key`."
            )

        padding = "=" * (-len(candidate) % 4)
        try:
            material = base64.urlsafe_b64decode(candidate + padding)
        except (binascii.Error, ValueError) as exc:
            raise ImproperlyConfigured(
                f"{SETTING_NAME}: key {displayable_id(key_id)} is not valid base64url. "
                "Generate one with `python manage.py snapadmin_encryption_key`."
            ) from exc

        if len(material) != KEY_BYTES:
            raise ImproperlyConfigured(
                f"{SETTING_NAME}: key {displayable_id(key_id)} decodes to {len(material)} bytes, but "
                f"{KEY_BYTES} bytes are required (AES-256). Generate one with "
                "`python manage.py snapadmin_encryption_key`."
            )

        return cls(id=key_id, material=material)

    @property
    def fingerprint(self) -> str:
        """A short digest that identifies the key without revealing it."""
        return _fingerprint(self.material)

    def __repr__(self) -> str:
        return f"<EncryptionKey id={self.id!r} fingerprint={self.fingerprint}>"

    __str__ = __repr__


@dataclass(frozen=True)
class Keyset:
    """The ordered keys a project encrypts and decrypts with.

    The first key is the *active* one — everything written from now on uses it.
    Every other key stays only to read rows written before the last rotation;
    dropping one makes those rows permanently unreadable, so
    ``snapadmin_encrypt_fields --rotate`` (#CRYPT1f) exists to empty them out
    first.
    """

    keys: tuple[EncryptionKey, ...]
    source: KeySource

    @classmethod
    def build(cls, keys: Sequence[EncryptionKey], *, source: KeySource) -> Keyset:
        """Validate an ordered sequence of keys into a keyset."""
        keys = tuple(keys)
        if not keys:
            raise ImproperlyConfigured(
                f"{SETTING_NAME}: the {source.value} configuration contains no keys. "
                "Remove it, or generate a key with "
                "`python manage.py snapadmin_encryption_key`."
            )
        seen: set[str] = set()
        for key in keys:
            if key.id in seen:
                raise ImproperlyConfigured(
                    f"{SETTING_NAME}: key id {displayable_id(key.id)} appears more than once. Every key "
                    "needs its own id — a ciphertext names the id it was written with."
                )
            seen.add(key.id)
        return cls(keys=keys, source=source)

    @property
    def active(self) -> EncryptionKey:
        """The key new ciphertext is written with."""
        return self.keys[0]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(key.id for key in self.keys)

    @property
    def fingerprint(self) -> str:
        """Identifies the whole set — the value to compare across environments.

        A restore into an environment whose keyset fingerprint differs is the
        one failure mode that looks like data corruption but is not, so
        ``snapadmin_info`` prints this and ``SECURITY.md`` explains it.
        """
        payload = b"|".join(f"{key.id}:{key.fingerprint}".encode() for key in self.keys)
        return _fingerprint(payload)

    def get(self, key_id: str) -> EncryptionKey | None:
        """The key a ciphertext names, or ``None`` if this keyset lacks it."""
        for key in self.keys:
            if key.id == key_id:
                return key
        return None

    def __len__(self) -> int:
        return len(self.keys)

    def __iter__(self) -> Iterator[EncryptionKey]:
        return iter(self.keys)

    def __repr__(self) -> str:
        return (
            f"<Keyset source={self.source.value} keys={list(self.ids)} "
            f"fingerprint={self.fingerprint}>"
        )

    __str__ = __repr__


# ─────────────────────────────────────────────────────────────────────────────
# Settings access
# ─────────────────────────────────────────────────────────────────────────────


def encryption_settings() -> Mapping[str, Any]:
    """The ``SNAPADMIN_ENCRYPTION`` dict, or an empty mapping when unset."""
    # Spelled out rather than passed as SETTING_NAME: the documentation
    # completeness audit (tests/test_docs_completeness.py) discovers settings by
    # scanning for literal get_setting("SNAPADMIN_...") call sites, and a
    # setting it cannot see is a setting nobody has to document.
    configured = get_setting("SNAPADMIN_ENCRYPTION", None)
    if configured is None:
        return {}
    if not isinstance(configured, Mapping):
        raise ImproperlyConfigured(
            f"{SETTING_NAME} must be a dict, got {type(configured).__name__}."
        )
    return configured


def is_strict() -> bool:
    """Whether a missing keyset blocks startup (default: yes).

    ``STRICT: False`` only relaxes the *startup* check — for a build container
    that runs ``collectstatic`` or ``check`` without access to the secret
    store. It never relaxes the runtime guarantee: encrypting or decrypting
    without a keyset always raises, so a column that is supposed to hold
    ciphertext can never quietly receive plaintext instead.
    """
    return bool(encryption_settings().get("STRICT", True))


def configured_key_file() -> str:
    """The configured key-file path (settings first, then the environment)."""
    return str(encryption_settings().get("KEY_FILE") or os.environ.get(ENV_KEY_FILE, "") or "")


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────


def _parse_entries(text: str, source: KeySource) -> list[EncryptionKey]:
    """Parse ``id:key`` entries separated by commas or newlines.

    Shared by the environment variable and the key file so a mounted secret and
    a ``.env`` line accept exactly the same content. ``#`` starts a comment, so
    a rotated key file can record when each key was added.
    """
    keys: list[EncryptionKey] = []
    for raw_line in text.replace(",", "\n").splitlines():
        entry = raw_line.strip()
        if not entry or entry.startswith("#"):
            continue
        key_id, separator, encoded = entry.partition(":")
        if not separator:
            key_id, encoded = DEFAULT_KEY_ID, entry
        keys.append(EncryptionKey.from_encoded(key_id, encoded))
    if not keys:
        raise ImproperlyConfigured(
            f"{SETTING_NAME}: the {source.value} configuration contains no keys."
        )
    return keys


def _keys_from_mappings(entries: Sequence[Any], source: KeySource) -> list[EncryptionKey]:
    """Parse ``[{"id": ..., "key": ...}, ...]`` — the settings and provider shape."""
    keys: list[EncryptionKey] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ImproperlyConfigured(
                f"{SETTING_NAME}: each key in the {source.value} configuration must be a "
                "mapping like {'id': '2026-09', 'key': '<base64url>'}, got "
                f"{type(entry).__name__}."
            )
        if "key" not in entry:
            raise ImproperlyConfigured(
                f"{SETTING_NAME}: a key in the {source.value} configuration has no 'key' "
                "entry (the base64url material)."
            )
        keys.append(
            EncryptionKey.from_encoded(str(entry.get("id") or DEFAULT_KEY_ID), entry["key"])
        )
    return keys


def _strip_quotes(value: str) -> str:
    """Drop the surrounding quotes some ``.env`` loaders leave on a value."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1].strip()
    return value


# ─────────────────────────────────────────────────────────────────────────────
# Resolution
# ─────────────────────────────────────────────────────────────────────────────


def _from_provider(dotted_path: str) -> Keyset:
    try:
        provider = import_string(dotted_path)
    except ImportError as exc:
        raise ImproperlyConfigured(
            f"{SETTING_NAME}: KEY_PROVIDER {dotted_path!r} could not be imported ({exc})."
        ) from exc

    produced = provider()
    if isinstance(produced, Keyset):
        return produced
    if isinstance(produced, (str, bytes)) or not isinstance(produced, Sequence):
        raise ImproperlyConfigured(
            f"{SETTING_NAME}: KEY_PROVIDER {dotted_path!r} must return a Keyset or a "
            "sequence of {'id': ..., 'key': ...} mappings, got "
            f"{type(produced).__name__}."
        )
    if not produced:
        raise ImproperlyConfigured(
            f"{SETTING_NAME}: KEY_PROVIDER {dotted_path!r} returned no keys. A provider "
            "that resolves to nothing is a misconfigured secret store, not an "
            "unconfigured project."
        )
    return Keyset.build(
        _keys_from_mappings(produced, KeySource.PROVIDER), source=KeySource.PROVIDER
    )


def _from_file(path: str) -> Keyset:
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ImproperlyConfigured(
            f"{SETTING_NAME}: the key file {path!r} cannot be read ({exc.strerror}). "
            "A configured key file that is missing is a deployment error, not an "
            "unconfigured project."
        ) from exc
    return Keyset.build(_parse_entries(content, KeySource.FILE), source=KeySource.FILE)


def _resolve() -> Keyset | None:
    """Run the four sources in order and return the first that is configured."""
    options = encryption_settings()

    provider = str(options.get("KEY_PROVIDER") or "").strip()
    if provider:
        return _from_provider(provider)

    key_file = configured_key_file()
    if key_file:
        return _from_file(key_file)

    env_value = _strip_quotes(os.environ.get(ENV_KEYS, ""))
    if env_value:
        return Keyset.build(_parse_entries(env_value, KeySource.ENV), source=KeySource.ENV)

    declared = options.get("KEYS")
    if declared:
        return Keyset.build(
            _keys_from_mappings(list(declared), KeySource.SETTINGS), source=KeySource.SETTINGS
        )

    return None


def get_keyset() -> Keyset | None:
    """The project's keyset, or ``None`` when encryption is unconfigured.

    Resolved once per process and cached — a ``KEY_PROVIDER`` that calls out to
    a secret store must not be paid per query, and a key file must not be
    re-read on every save. Call :func:`reset_keyset` after changing
    configuration (tests, a rotation job) to force a fresh resolution.
    """
    global _cached
    if _cached is _UNRESOLVED:
        keyset = _resolve()
        if keyset is not None:
            logger.info(
                "encryption_keyset_resolved",
                source=keyset.source.value,
                key_ids=list(keyset.ids),
                fingerprint=keyset.fingerprint,
            )
        _cached = keyset
    return _cached


def reset_keyset() -> None:
    """Drop the cached keyset so the next :func:`get_keyset` re-resolves."""
    global _cached
    _cached = _UNRESOLVED


def is_configured() -> bool:
    """Whether any key material resolves for this project."""
    return get_keyset() is not None


def require_keyset() -> Keyset:
    """The keyset, or a loud :class:`ImproperlyConfigured` explaining how to get one.

    Every encrypt/decrypt path goes through this, which is what makes
    ``STRICT: False`` a relaxation of the startup check only: the runtime still
    refuses to touch an encrypted field without a key rather than falling back
    to plaintext.
    """
    keyset = get_keyset()
    if keyset is None:
        raise ImproperlyConfigured(
            "This project uses encrypted fields but no encryption key is configured. "
            f"Generate one with `python manage.py snapadmin_encryption_key` and set it "
            f"via the {ENV_KEYS} environment variable, {SETTING_NAME}['KEY_FILE'], or "
            f"{SETTING_NAME}['KEY_PROVIDER']."
        )
    return keyset


def matches_secret_key(key: EncryptionKey) -> bool:
    """Whether a key is Django's ``SECRET_KEY`` in disguise.

    Checked at startup rather than trusted: reusing ``SECRET_KEY`` ties every
    encrypted column to a value that gets rotated for unrelated reasons, and
    the damage only becomes visible after the rotation, when the data is
    already unreadable.
    """
    try:
        secret = str(getattr(settings, "SECRET_KEY", "") or "")
    except ImproperlyConfigured:
        # Django refuses to hand out an empty SECRET_KEY at all. A project in
        # that state has bigger problems than this check, and a system check
        # must report them rather than die on the way to reporting them.
        return False
    if hmac.compare_digest(key.material, secret.encode("utf-8", "ignore")):
        return True
    padding = "=" * (-len(secret) % 4)
    try:
        decoded = base64.urlsafe_b64decode(secret + padding)
    except (binascii.Error, ValueError):
        return False
    return hmac.compare_digest(key.material, decoded)


def has_encrypted_fields() -> bool:
    """Whether any installed model declares an encrypted field.

    The trigger for the fail-closed startup check. Detection is by marker
    attribute rather than by class, so the check keeps working for a project's
    own subclass of a ``SnapEncrypted*Field`` and needs no import of the field
    module (which would pull in the cipher, and with it the ``[encryption]``
    extra, just to run ``manage.py check``).
    """
    for model in apps.get_models():
        for field in model._meta.get_fields():
            if getattr(field, "is_snap_encrypted", False):
                return True
    return False
