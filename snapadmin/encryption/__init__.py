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

The cipher (AES-256-GCM over a versioned ``snap1.`` envelope) and the
``SnapEncrypted*Field`` family land on top of this, in #CRYPT1b/#CRYPT1c.

Keys are generated with ``python manage.py snapadmin_encryption_key``; see the
:mod:`~snapadmin.encryption.keys` docstring for the full configuration shape
and ``SECURITY.md`` for the threat model.
"""

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
    "ENV_KEY_FILE",
    "ENV_KEYS",
    "KEY_BYTES",
    "SETTING_NAME",
    "EncryptionKey",
    "Keyset",
    "KeySource",
    "displayable_id",
    "encode_key",
    "generate_key",
    "get_keyset",
    "has_encrypted_fields",
    "is_configured",
    "is_strict",
    "require_keyset",
    "reset_keyset",
]
