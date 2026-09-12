"""
snapadmin/encryption/

Field-level encryption: ciphertext at rest in the database, ordinary Python
values in application code.

Nothing here runs unless a model actually declares an encrypted field. An
existing project is unaffected — no new setting is required, no dependency is
imported, and no query pays for the feature being available.

The subpackage is layered so that configuration can be validated without the
cipher being installed:

``snapadmin.encryption.keys``
    Key material — resolution from a provider, a mounted secret file, the
    environment or settings; validation; fingerprints; and the guarantee that
    no key is ever rendered into a log, a ``repr`` or an exception. Stdlib
    only, so ``manage.py check`` can explain a misconfiguration in a container
    that has not installed the ``[encryption]`` extra.

``snapadmin.encryption.blind_index``
    The opt-in ``blind_index=True`` sibling column: an HKDF-derived, per-column
    HMAC that restores equality matching on a column SQL cannot read — and the
    equality leak that buys it. Stdlib only.

``snapadmin.encryption.cipher``
    AES-256-GCM over the versioned ``snap1.<key id>.<nonce>.<payload>``
    envelope, the AAD that binds a ciphertext to its own column, and the
    lazily-imported ``cryptography`` backend. This is the only layer that
    touches the ``[encryption]`` extra.

The ``SnapEncrypted*Field`` family in :mod:`snapadmin.fields` sits on top of
both, turning a declared field into ciphertext at rest and an ordinary Python
value in application code.

Keys are generated with ``python manage.py snapadmin_encryption_key``; see the
:mod:`~snapadmin.encryption.keys` docstring for the full configuration shape
and ``SECURITY.md`` for the threat model.
"""

from snapadmin.encryption.blind_index import (
    INDEX_CHARS,
    index_candidates,
    index_for_write,
)
from snapadmin.encryption.cipher import (
    ENVELOPE_VERSION,
    NONCE_BYTES,
    DecryptionError,
    EncryptionError,
    Envelope,
    aad_for,
    decrypt,
    encrypt,
    looks_encrypted,
)
from snapadmin.encryption.keys import (
    DEFAULT_KEY_ID,
    ENV_KEY_FILE,
    ENV_KEYS,
    KEY_BYTES,
    SETTING_NAME,
    EncryptionKey,
    Keyset,
    KeySource,
    displayable_id,
    encode_key,
    generate_key,
    get_keyset,
    has_encrypted_fields,
    is_configured,
    is_strict,
    require_keyset,
    reset_keyset,
)

__all__ = [
    "DEFAULT_KEY_ID",
    "ENVELOPE_VERSION",
    "INDEX_CHARS",
    "ENV_KEY_FILE",
    "ENV_KEYS",
    "KEY_BYTES",
    "NONCE_BYTES",
    "SETTING_NAME",
    "DecryptionError",
    "EncryptionError",
    "EncryptionKey",
    "Envelope",
    "Keyset",
    "KeySource",
    "aad_for",
    "decrypt",
    "displayable_id",
    "encode_key",
    "encrypt",
    "generate_key",
    "index_candidates",
    "index_for_write",
    "get_keyset",
    "has_encrypted_fields",
    "is_configured",
    "is_strict",
    "looks_encrypted",
    "require_keyset",
    "reset_keyset",
]
