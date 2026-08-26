"""
snapadmin/crypto.py

Streaming AGE encryption for backup artefacts (``SNAPADMIN_BACKUP_AGE_*``).

age (https://age-encryption.org) encrypts a stream once to any number of
recipients; any single matching identity decrypts it independently — no shared
secret, no key ceremony, no re-encryption to add a reader. Two backends
implement the same :func:`encrypt_stream` / :func:`decrypt_stream` interface
and produce/consume the identical, standardised file format, so a bundle
encrypted with one backend restores fine with the other — or with the plain
``age`` command run by hand:

* **``pyrage``** (MIT) — in-process bindings over the Rust ``age`` crate, the
  optional ``[age]`` extra (``pip install django-snapadmin[age]``). Best for
  portability: identical behaviour on every platform pip works on, no OS
  package, no subprocess management.
* **the ``age`` CLI** (BSD-3-Clause, https://github.com/FiloSottile/age) —
  shelled out to via :mod:`subprocess`, streamed through stdin/stdout exactly
  like ``pg_dump`` already is in :mod:`snapadmin.backup`. Best for a
  production host that would rather manage encryption tooling through the OS
  package manager (``apt install age`` on Debian 12+ / Ubuntu 22.04+,
  ``brew install age`` on macOS) than add a compiled Python wheel to the venv.

``SNAPADMIN_BACKUP_AGE_BACKEND`` picks between them: ``"auto"`` (the default —
prefers ``pyrage`` if importable, else the CLI if found on ``PATH``),
``"pyrage"`` or ``"binary"``. The two explicit modes fail loudly, naming their
own missing dependency, rather than silently falling through to the other —
the choice is always observable, never an environment-dependent coin-flip.
"""
from __future__ import annotations

import functools
import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import BinaryIO, Sequence

from django.core.exceptions import ImproperlyConfigured

from snapadmin.logging_config import get_logger

logger = get_logger(__name__)

#: Prefix of a native age identity ("secret key") string, as written by age-keygen
#: and accepted by ``x25519.Identity.from_str``.
_AGE_IDENTITY_PREFIX = "AGE-SECRET-KEY-1"

#: Backend names accepted by SNAPADMIN_BACKUP_AGE_BACKEND.
BACKENDS = ("auto", "pyrage", "binary")


class AgeError(Exception):
    """Raised when AGE encryption or decryption itself fails.

    Covers a malformed recipient/identity, a wrong identity at decrypt time, or
    a non-zero exit from the ``age`` binary — always with the underlying
    library's or CLI's own message attached, never a bare traceback. Missing
    *dependencies* (no pyrage, no `age` on PATH) raise
    :class:`~django.core.exceptions.ImproperlyConfigured` instead, per the
    house lazy-optional-dependency pattern.
    """


@functools.lru_cache(maxsize=1)
def _load_pyrage():
    """Return the imported ``pyrage`` package, imported lazily and cached.

    Mirrors ``sanitize._load_nh3()`` / ``exporting._load_openpyxl()``: the
    import is attempted only when the pyrage backend is actually selected, and
    ``ImportError`` becomes an actionable ``ImproperlyConfigured`` naming
    exactly what to install, instead of a bare ``ModuleNotFoundError`` surfacing
    from inside a backup run.
    """
    try:
        import pyrage
        from pyrage import ssh, x25519  # noqa: F401 - submodules used by callers below
    except ImportError as exc:
        raise ImproperlyConfigured(
            "AGE backup encryption needs the pyrage library, which could not be "
            "imported. Install it (`pip install django-snapadmin[age]`), or set "
            "SNAPADMIN_BACKUP_AGE_BACKEND='binary' to use the `age` command-line "
            "tool instead (e.g. `apt install age` on Debian/Ubuntu, "
            "`brew install age` on macOS)."
        ) from exc
    return pyrage


def _binary_path(binary_path: str = "") -> str | None:
    """Resolve the ``age`` executable, or ``None`` if it cannot be found.

    Not cached — unlike the pyrage import, this depends on the (possibly
    overridden-in-tests) ``binary_path`` argument and the live ``PATH``, both
    cheap to re-check on every call.
    """
    if binary_path:
        return binary_path if shutil.which(binary_path) else None
    return shutil.which("age")


def resolve_backend(configured: str, binary_path: str = "") -> str:
    """``"auto"``/``"pyrage"``/``"binary"`` -> the concrete backend to use, or raise.

    The two explicit modes fail with ``ImproperlyConfigured`` naming exactly
    what is missing for *that* mode; ``"auto"`` tries pyrage first (fewer
    moving parts — no subprocess), then the binary, and only raises if neither
    is available, naming both install paths.
    """
    if configured == "pyrage":
        _load_pyrage()
        return "pyrage"
    if configured == "binary":
        if _binary_path(binary_path) is None:
            raise ImproperlyConfigured(
                "SNAPADMIN_BACKUP_AGE_BACKEND is 'binary', but the `age` "
                "command-line tool was not found on PATH. Install it (e.g. "
                "`apt install age` on Debian/Ubuntu, `brew install age` on "
                "macOS) or set SNAPADMIN_BACKUP_AGE_BINARY_PATH to its location."
            )
        return "binary"
    if configured == "auto":
        try:
            _load_pyrage()
            return "pyrage"
        except ImproperlyConfigured:
            pass
        if _binary_path(binary_path) is not None:
            return "binary"
        raise ImproperlyConfigured(
            "AGE backup encryption needs either the pyrage library "
            "(`pip install django-snapadmin[age]`) or the `age` command-line "
            "tool on PATH (e.g. `apt install age` on Debian/Ubuntu, "
            "`brew install age` on macOS)."
        )
    raise ImproperlyConfigured(
        f"SNAPADMIN_BACKUP_AGE_BACKEND={configured!r} is not one of "
        f"{BACKENDS!r}."
    )


def looks_like_recipient(value: str) -> bool:
    """Best-effort, backend-agnostic shape check for a configured recipient string.

    Deliberately does not import pyrage or shell out to ``age`` — a project may
    intend to use only the other backend, and this is used by the startup
    system check (:data:`snapadmin.checks.check_backup_age_recipients`), which
    must not force either dependency just to validate a setting.
    """
    value = value.strip()
    if value.startswith("age1") and len(value) > 4:
        return True
    return value.startswith(("ssh-ed25519 ", "ssh-rsa ", "ssh-ecdsa "))


def fingerprint(recipient: str) -> str:
    """A short, stable, safe-to-display identifier for a recipient string.

    The recipient string itself is already public (it's a public key) and
    safe to print in full — this exists purely as a display compaction for
    ``snapadmin_info``, so an operator can compare "does my key's fingerprint
    appear in this backup's list" without eyeballing a long Bech32/SSH string.
    """
    return hashlib.sha256(recipient.strip().encode()).hexdigest()[:12]


# ─────────────────────────────────────────────────────────────────────────────
# pyrage backend
# ─────────────────────────────────────────────────────────────────────────────

def _parse_recipient_pyrage(value: str):
    pyrage = _load_pyrage()
    value = value.strip()
    if value.startswith("age1"):
        try:
            return pyrage.x25519.Recipient.from_str(value)
        except Exception as exc:
            raise AgeError(f"Invalid age recipient {value!r}: {exc}") from exc
    try:
        return pyrage.ssh.Recipient.from_str(value)
    except Exception as exc:
        raise AgeError(
            f"Invalid recipient {value!r}: not a valid age (age1…) or SSH "
            f"public key ({exc})."
        ) from exc


def _parse_identity_pyrage(identity_path: str):
    pyrage = _load_pyrage()
    raw = Path(identity_path).read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(_AGE_IDENTITY_PREFIX):
            try:
                return pyrage.x25519.Identity.from_str(line)
            except Exception as exc:
                raise AgeError(
                    f"Invalid age identity in {identity_path!r}: {exc}"
                ) from exc
    try:
        return pyrage.ssh.Identity.from_buffer(raw)
    except Exception as exc:
        raise AgeError(
            f"Could not load an identity from {identity_path!r}: not a "
            f"recognised age or SSH private key ({exc})."
        ) from exc


def _encrypt_pyrage(reader: BinaryIO, writer: BinaryIO, recipients: Sequence[str]) -> None:
    pyrage = _load_pyrage()
    # Recipients are parsed (and any AgeError raised) before encrypt_io ever
    # runs, so nothing inside this try block can raise our own AgeError —
    # only pyrage's native exception types reach the except clause below.
    parsed = [_parse_recipient_pyrage(value) for value in recipients]
    try:
        pyrage.encrypt_io(reader, writer, parsed)
    except Exception as exc:
        raise AgeError(f"AGE encryption failed: {exc}") from exc


def _decrypt_pyrage(reader: BinaryIO, writer: BinaryIO, identity_path: str) -> None:
    pyrage = _load_pyrage()
    identity = _parse_identity_pyrage(identity_path)
    try:
        pyrage.decrypt_io(reader, writer, [identity])
    except Exception as exc:
        raise AgeError(f"AGE decryption failed: {exc}") from exc


# ─────────────────────────────────────────────────────────────────────────────
# binary backend
# ─────────────────────────────────────────────────────────────────────────────

def _run_age(command: list[str], reader: BinaryIO, writer: BinaryIO) -> None:
    # Streamed straight through, same shape as the existing pg_dump invocation
    # in backup.py: an argument list (never shell=True, so no shell-injection
    # surface) with the OS pipe wired directly into stdin/stdout.
    process = subprocess.run(command, stdin=reader, stdout=writer, stderr=subprocess.PIPE)
    if process.returncode != 0:
        raise AgeError(f"`age` failed: {process.stderr.decode(errors='replace').strip()}")


def _encrypt_binary(
    reader: BinaryIO, writer: BinaryIO, recipients: Sequence[str], binary_path: str
) -> None:
    binary = _binary_path(binary_path) or "age"
    command = [binary]
    for value in recipients:
        command += ["-r", value]
    _run_age(command, reader, writer)


def _decrypt_binary(
    reader: BinaryIO, writer: BinaryIO, identity_path: str, binary_path: str
) -> None:
    binary = _binary_path(binary_path) or "age"
    _run_age([binary, "-d", "-i", identity_path], reader, writer)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────────

def encrypt_stream(
    reader: BinaryIO,
    writer: BinaryIO,
    recipients: Sequence[str],
    *,
    backend: str = "auto",
    binary_path: str = "",
) -> None:
    """Encrypt everything read from ``reader`` into ``writer``, for every recipient.

    Any one of the recipients' matching identities decrypts the result
    independently later — that is the entire point of the age format. Both
    arguments are binary file-like objects; nothing is buffered in full.
    """
    if not recipients:
        raise ValueError("encrypt_stream() requires at least one recipient.")
    resolved = resolve_backend(backend, binary_path)
    logger.info("age_backend_selected", backend=resolved, operation="encrypt")
    if resolved == "pyrage":
        _encrypt_pyrage(reader, writer, recipients)
    else:
        _encrypt_binary(reader, writer, recipients, binary_path)


def decrypt_stream(
    reader: BinaryIO,
    writer: BinaryIO,
    identity_path: str,
    *,
    backend: str = "auto",
    binary_path: str = "",
) -> None:
    """Decrypt everything read from ``reader`` into ``writer``, using one identity file.

    ``identity_path`` is a path on disk — the key material itself is never
    accepted as a setting or a string argument here, only as a file the caller
    supplies at the moment of restore.
    """
    resolved = resolve_backend(backend, binary_path)
    logger.info("age_backend_selected", backend=resolved, operation="decrypt")
    if resolved == "pyrage":
        _decrypt_pyrage(reader, writer, identity_path)
    else:
        _decrypt_binary(reader, writer, identity_path, binary_path)
