"""Tests for streaming AGE encryption (SNAPADMIN_BACKUP_AGE_*).

Covers both backends for real against the actually-installed dependencies
(pyrage via pip, the `age` CLI via the OS package manager in CI) — only the
*missing*-dependency error paths mock the absence, per the house pattern
(`tests/test_wysiwyg_sanitize.py`).
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured

from snapadmin import crypto

AGE_INSTALLED = shutil.which("age") is not None
requires_age_binary = pytest.mark.skipif(not AGE_INSTALLED, reason="`age` not on PATH")


def _keypair():
    pyrage = crypto._load_pyrage()
    identity = pyrage.x25519.Identity.generate()
    return str(identity), str(identity.to_public())


@pytest.fixture
def keypairs():
    """Three independent (identity_str, recipient_str) age keypairs."""
    return [_keypair() for _ in range(3)]


@pytest.fixture
def identity_files(tmp_path, keypairs):
    """Each identity written to its own file, as SNAPADMIN_BACKUP_AGE_IDENTITY_FILE would hold."""
    paths = []
    for i, (identity, _recipient) in enumerate(keypairs):
        path = tmp_path / f"identity{i}.txt"
        path.write_text(identity + "\n")
        paths.append(path)
    return paths


PLAINTEXT = b"snapadmin backup crypto round-trip payload " * 500  # exercise real streaming


class TestLoadPyrageCaching:
    def test_successful_import_is_cached(self):
        crypto._load_pyrage.cache_clear()
        try:
            first = crypto._load_pyrage()
            second = crypto._load_pyrage()
            assert first is second
        finally:
            crypto._load_pyrage.cache_clear()

    def test_missing_pyrage_raises_improperly_configured(self):
        crypto._load_pyrage.cache_clear()
        with mock.patch.dict(sys.modules, {"pyrage": None}):
            with pytest.raises(ImproperlyConfigured) as exc:
                crypto._load_pyrage()
        crypto._load_pyrage.cache_clear()
        msg = str(exc.value)
        assert "pyrage" in msg
        assert "django-snapadmin[age]" in msg


class TestResolveBackend:
    def test_explicit_pyrage(self):
        assert crypto.resolve_backend("pyrage") == "pyrage"

    def test_explicit_pyrage_missing_raises_naming_extra(self):
        crypto._load_pyrage.cache_clear()
        with mock.patch.dict(sys.modules, {"pyrage": None}):
            with pytest.raises(ImproperlyConfigured, match="django-snapadmin\\[age\\]"):
                crypto.resolve_backend("pyrage")
        crypto._load_pyrage.cache_clear()

    @requires_age_binary
    def test_explicit_binary(self):
        assert crypto.resolve_backend("binary") == "binary"

    def test_explicit_binary_missing_raises_naming_path(self):
        with pytest.raises(ImproperlyConfigured, match="PATH"):
            crypto.resolve_backend("binary", binary_path="/no/such/age/binary")

    def test_auto_prefers_pyrage_when_available(self):
        assert crypto.resolve_backend("auto") == "pyrage"

    @requires_age_binary
    def test_auto_falls_back_to_binary_when_pyrage_missing(self):
        crypto._load_pyrage.cache_clear()
        with mock.patch.dict(sys.modules, {"pyrage": None}):
            assert crypto.resolve_backend("auto") == "binary"
        crypto._load_pyrage.cache_clear()

    def test_auto_raises_naming_both_when_neither_available(self):
        crypto._load_pyrage.cache_clear()
        with mock.patch.dict(sys.modules, {"pyrage": None}):
            with pytest.raises(ImproperlyConfigured) as exc:
                crypto.resolve_backend("auto", binary_path="/no/such/age/binary")
        crypto._load_pyrage.cache_clear()
        msg = str(exc.value)
        assert "django-snapadmin[age]" in msg
        assert "age" in msg

    def test_unknown_backend_name_raises(self):
        with pytest.raises(ImproperlyConfigured, match="not one of"):
            crypto.resolve_backend("carrier-pigeon")


class TestLooksLikeRecipient:
    @pytest.mark.parametrize(
        "value",
        [
            "age1scr8rpq5lxtaqqskkawrft82at865e4j3gvs30cjv79q5qq3gc7qwj8um3",
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBaU comment",
            "ssh-rsa AAAAB3NzaC1yc2EAAAADAQAB comment",
        ],
    )
    def test_accepts_plausible_shapes(self, value):
        assert crypto.looks_like_recipient(value) is True

    @pytest.mark.parametrize("value", ["", "not-a-key", "age1", "ssh-dss AAAA"])
    def test_rejects_implausible_shapes(self, value):
        assert crypto.looks_like_recipient(value) is False


class TestFingerprint:
    def test_deterministic_and_short(self):
        recipient = "age1scr8rpq5lxtaqqskkawrft82at865e4j3gvs30cjv79q5qq3gc7qwj8um3"
        first = crypto.fingerprint(recipient)
        second = crypto.fingerprint(recipient)
        assert first == second
        assert len(first) == 12

    def test_different_recipients_differ(self):
        assert crypto.fingerprint("age1aaaa") != crypto.fingerprint("age1bbbb")

    def test_never_contains_identity_material(self):
        # A pointed regression pin: the fingerprint is a hash, not a substring
        # slice — accidentally passing an identity in must not leak any of it.
        identity = "AGE-SECRET-KEY-1QKS35M0TQ9JHF9D6H4Y5PWC3JAGD0ZRHR7FPVDHM0PGJAQNLC5QQAGTSXY"
        assert identity[:12] not in crypto.fingerprint(identity)


# ─────────────────────────────────────────────────────────────────────────────
# Round-trips — the user's core requirement: N recipients, any ONE identity
# decrypts alone. Parametrized over both backends.
# ─────────────────────────────────────────────────────────────────────────────

BACKENDS = ["pyrage"] + (["binary"] if AGE_INSTALLED else [])


def _encrypt_to_file(tmp_path, name, plaintext, recipients, backend):
    """Real files in and out — the `binary` backend needs a real fd on both
    ends of the subprocess (unlike pyrage, it can't work against a bare
    io.BytesIO), and this shape is also what backup.py actually uses."""
    src = tmp_path / f"src-{name}"
    src.write_bytes(plaintext)
    dst = tmp_path / name
    with open(src, "rb") as reader, open(dst, "wb") as writer:
        crypto.encrypt_stream(reader, writer, recipients, backend=backend)
    return dst


def _decrypt_to_bytes(tmp_path, name, ciphertext_path, identity_path, backend):
    dst = tmp_path / name
    with open(ciphertext_path, "rb") as reader, open(dst, "wb") as writer:
        crypto.decrypt_stream(reader, writer, str(identity_path), backend=backend)
    return dst.read_bytes()


@pytest.mark.parametrize("backend", BACKENDS)
class TestRoundTrip:
    def test_single_recipient(self, backend, keypairs, identity_files, tmp_path):
        _identity_str, recipient = keypairs[0]
        ciphertext = _encrypt_to_file(tmp_path, "ct.age", PLAINTEXT, [recipient], backend)
        plaintext_out = _decrypt_to_bytes(tmp_path, "pt.bin", ciphertext, identity_files[0], backend)
        assert plaintext_out == PLAINTEXT

    def test_three_recipients_each_identity_decrypts_alone(self, backend, keypairs, identity_files, tmp_path):
        recipients = [recipient for _identity, recipient in keypairs]
        ciphertext = _encrypt_to_file(tmp_path, "ct.age", PLAINTEXT, recipients, backend)

        for i, path in enumerate(identity_files):
            out = _decrypt_to_bytes(tmp_path, f"pt{i}.bin", ciphertext, path, backend)
            assert out == PLAINTEXT

    def test_wrong_identity_fails_cleanly(self, backend, keypairs, identity_files, tmp_path):
        recipient = keypairs[0][1]
        ciphertext = _encrypt_to_file(tmp_path, "ct.age", PLAINTEXT, [recipient], backend)

        wrong_identity, _wrong_recipient = _keypair()
        wrong_path = tmp_path / "wrong.txt"
        wrong_path.write_text(wrong_identity + "\n")

        with pytest.raises(crypto.AgeError):
            _decrypt_to_bytes(tmp_path, "pt.bin", ciphertext, wrong_path, backend)

    def test_ssh_recipient_and_identity(self, backend, tmp_path):
        priv_path = tmp_path / "id_ed25519"
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(priv_path), "-N", "", "-q"],
            check=True,
        )
        pub = (priv_path.with_suffix(priv_path.suffix + ".pub")).read_text().strip()

        ciphertext = _encrypt_to_file(tmp_path, "ct.age", PLAINTEXT, [pub], backend)
        out = _decrypt_to_bytes(tmp_path, "pt.bin", ciphertext, priv_path, backend)
        assert out == PLAINTEXT

    def test_real_pipe_reader_streams_without_buffering_everything_up_front(
        self, backend, keypairs, identity_files, tmp_path
    ):
        """The shape backup.py actually uses: a real OS pipe as the reader."""
        _identity, recipient = keypairs[0]
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "rb")
        writer_fh = os.fdopen(write_fd, "wb")

        import threading

        def produce():
            writer_fh.write(PLAINTEXT)
            writer_fh.close()

        thread = threading.Thread(target=produce)
        thread.start()
        dst = tmp_path / "ct.age"
        try:
            with open(dst, "wb") as writer:
                crypto.encrypt_stream(reader, writer, [recipient], backend=backend)
        finally:
            thread.join(timeout=5)
            reader.close()

        out = _decrypt_to_bytes(tmp_path, "pt.bin", dst, identity_files[0], backend)
        assert out == PLAINTEXT


class TestEncryptStreamRequiresRecipients:
    def test_empty_recipients_raises(self):
        with pytest.raises(ValueError, match="at least one recipient"):
            crypto.encrypt_stream(io.BytesIO(b"x"), io.BytesIO(), [])


@pytest.mark.parametrize("backend", BACKENDS)
class TestBadRecipient:
    def test_malformed_recipient_raises_age_error(self, backend, tmp_path):
        with pytest.raises(crypto.AgeError):
            _encrypt_to_file(tmp_path, "ct.age", b"x", ["not-a-real-recipient"], backend)


class TestPyrageParsingErrors:
    """Failure paths internal to the pyrage backend's own parsing — the binary
    backend hands recipient/identity strings straight to the `age` CLI and
    never runs this code at all."""

    def test_malformed_age_prefixed_recipient_raises_age_error(self, tmp_path):
        with pytest.raises(crypto.AgeError, match="Invalid age recipient"):
            _encrypt_to_file(tmp_path, "ct.age", b"x", ["age1not-valid-bech32"], backend="pyrage")

    def test_non_utf8_identity_file_falls_through_to_ssh_and_fails(self, tmp_path, keypairs):
        recipient = keypairs[0][1]
        ciphertext = _encrypt_to_file(tmp_path, "ct.age", PLAINTEXT, [recipient], backend="pyrage")

        garbage = tmp_path / "garbage.bin"
        garbage.write_bytes(b"\xff\xfe\x00not valid utf-8 or an ssh key \xac")
        with pytest.raises(crypto.AgeError, match="not a recognised age or SSH private key"):
            _decrypt_to_bytes(tmp_path, "pt.bin", ciphertext, garbage, backend="pyrage")

    def test_malformed_age_secret_key_line_raises_age_error(self, tmp_path, keypairs):
        recipient = keypairs[0][1]
        ciphertext = _encrypt_to_file(tmp_path, "ct.age", PLAINTEXT, [recipient], backend="pyrage")

        bad_identity = tmp_path / "bad_identity.txt"
        bad_identity.write_text(f"{crypto._AGE_IDENTITY_PREFIX}NOTVALIDBECH32\n")
        with pytest.raises(crypto.AgeError, match="Invalid age identity"):
            _decrypt_to_bytes(tmp_path, "pt.bin", ciphertext, bad_identity, backend="pyrage")

    def test_writer_failure_during_encrypt_io_wraps_as_age_error(self, keypairs):
        """A non-parsing failure inside pyrage.encrypt_io itself (e.g. the
        destination write fails, disk full) — distinct from every test above,
        which all fail before encrypt_io is ever called."""
        recipient = keypairs[0][1]

        class FailingWriter(io.RawIOBase):
            def writable(self):
                return True

            def write(self, data):
                raise OSError("disk full (simulated)")

        # Large enough to force a real write() call past age's internal
        # buffering (empirically ~64KiB STREAM chunks) rather than returning
        # before the writer is ever touched.
        big_plaintext = PLAINTEXT * 20
        with pytest.raises(crypto.AgeError, match="AGE encryption failed"):
            crypto.encrypt_stream(
                io.BytesIO(big_plaintext), FailingWriter(), [recipient], backend="pyrage"
            )
