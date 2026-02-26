"""
tests/test_validators.py

Unit tests for snapadmin/validators.py

Covers:
- FileExtensionEnum and FileEncodingEnum values
- SnapFileValidator: allowed extensions
- SnapFileValidator: max file size
- SnapFileValidator: allowed encodings
- SnapFileValidator: combined constraints
- SnapFileValidator: __eq__ and __hash__ (migration stability)
- SnapFileValidator: no constraints passes all files
"""

import io

import pytest
from django.core.exceptions import ValidationError

from snapadmin.validators import (
    FileEncodingEnum,
    FileExtensionEnum,
    SnapFileValidator,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_file(name: str, content: bytes = b"hello"):
    """Return a minimal in-memory file-like object."""
    f = io.BytesIO(content)
    f.name = name
    f.size = len(content)
    return f


# ─────────────────────────────────────────────────────────────────────────────
# Enum sanity checks
# ─────────────────────────────────────────────────────────────────────────────

class TestEnums:
    def test_extension_enum_values(self):
        assert FileExtensionEnum.PDF.value == "pdf"
        assert FileExtensionEnum.JSON.value == "json"
        assert FileExtensionEnum.CSV.value == "csv"

    def test_encoding_enum_values(self):
        assert FileEncodingEnum.UTF8.value == "utf-8"
        assert FileEncodingEnum.ASCII.value == "ascii"


# ─────────────────────────────────────────────────────────────────────────────
# Extension validation
# ─────────────────────────────────────────────────────────────────────────────

class TestExtensionValidation:
    def _validator(self, *exts):
        return SnapFileValidator(allowed_extensions=list(exts))

    def test_allowed_extension_passes(self):
        v = self._validator(FileExtensionEnum.JSON)
        v(_make_file("config.json"))  # must not raise

    def test_forbidden_extension_raises(self):
        v = self._validator(FileExtensionEnum.JSON)
        with pytest.raises(ValidationError, match="not allowed"):
            v(_make_file("virus.exe"))

    def test_extension_check_is_case_insensitive(self):
        v = self._validator(FileExtensionEnum.PDF)
        v(_make_file("document.PDF"))  # uppercase – must not raise

    def test_multiple_allowed_extensions(self):
        v = self._validator(FileExtensionEnum.PDF, FileExtensionEnum.JSON, FileExtensionEnum.CSV)
        for fname in ("a.pdf", "b.json", "c.csv"):
            v(_make_file(fname))  # all must pass

    def test_string_extension_accepted(self):
        v = SnapFileValidator(allowed_extensions=["txt", "md"])
        v(_make_file("readme.txt"))

    def test_no_extension_restriction_passes_everything(self):
        v = SnapFileValidator()
        v(_make_file("anything.xyz"))


# ─────────────────────────────────────────────────────────────────────────────
# Size validation
# ─────────────────────────────────────────────────────────────────────────────

class TestSizeValidation:
    def test_file_within_limit_passes(self):
        v = SnapFileValidator(max_size_bytes=1024)
        v(_make_file("small.txt", content=b"x" * 100))

    def test_file_exactly_at_limit_passes(self):
        v = SnapFileValidator(max_size_bytes=100)
        v(_make_file("exact.txt", content=b"x" * 100))

    def test_file_over_limit_raises(self):
        v = SnapFileValidator(max_size_bytes=10)
        with pytest.raises(ValidationError, match="size"):
            v(_make_file("big.txt", content=b"x" * 100))

    def test_no_size_limit_passes_large_file(self):
        v = SnapFileValidator()
        v(_make_file("huge.bin", content=b"x" * 10_000_000))


# ─────────────────────────────────────────────────────────────────────────────
# Encoding validation
# ─────────────────────────────────────────────────────────────────────────────

class TestEncodingValidation:
    def test_utf8_content_passes(self):
        v = SnapFileValidator(allowed_encodings=[FileEncodingEnum.UTF8])
        content = "Hello World – a UTF-8 string.".encode("utf-8")
        v(_make_file("text.txt", content=content))

    def test_ascii_content_passes_utf8_check(self):
        """Pure ASCII is valid UTF-8."""
        v = SnapFileValidator(allowed_encodings=[FileEncodingEnum.UTF8])
        v(_make_file("ascii.txt", content=b"plain ascii"))

    def test_invalid_encoding_raises(self):
        v = SnapFileValidator(allowed_encodings=[FileEncodingEnum.ASCII])
        # Bytes that are valid UTF-8 but not pure ASCII
        non_ascii = "Ümlauts".encode("utf-8")
        with pytest.raises(ValidationError):
            v(_make_file("unicode.txt", content=non_ascii))

    def test_multiple_encodings_any_match_passes(self):
        v = SnapFileValidator(allowed_encodings=[FileEncodingEnum.ASCII, FileEncodingEnum.UTF8])
        v(_make_file("text.txt", content=b"pure ascii"))


# ─────────────────────────────────────────────────────────────────────────────
# Combined constraints
# ─────────────────────────────────────────────────────────────────────────────

class TestCombinedConstraints:
    def test_passes_all_constraints(self):
        v = SnapFileValidator(
            allowed_extensions=[FileExtensionEnum.JSON],
            allowed_encodings=[FileEncodingEnum.UTF8],
            max_size_bytes=1024,
        )
        content = b'{"key": "value"}'
        v(_make_file("data.json", content=content))

    def test_fails_extension_even_if_size_ok(self):
        v = SnapFileValidator(
            allowed_extensions=[FileExtensionEnum.JSON],
            max_size_bytes=1024,
        )
        with pytest.raises(ValidationError, match="not allowed"):
            v(_make_file("data.xml", content=b"<root/>"))

    def test_fails_size_even_if_extension_ok(self):
        v = SnapFileValidator(
            allowed_extensions=[FileExtensionEnum.TXT],
            max_size_bytes=5,
        )
        with pytest.raises(ValidationError, match="size"):
            v(_make_file("big.txt", content=b"way too much content here"))


# ─────────────────────────────────────────────────────────────────────────────
# __eq__ and __hash__ – migration stability
# ─────────────────────────────────────────────────────────────────────────────

class TestValidatorEquality:
    """Two validators with the same config must be equal so Django
    migration framework doesn't create duplicate migrations."""

    def test_equal_validators_are_equal(self):
        v1 = SnapFileValidator(
            allowed_extensions=[FileExtensionEnum.JSON],
            allowed_encodings=[FileEncodingEnum.UTF8],
            max_size_bytes=512,
        )
        v2 = SnapFileValidator(
            allowed_extensions=[FileExtensionEnum.JSON],
            allowed_encodings=[FileEncodingEnum.UTF8],
            max_size_bytes=512,
        )
        assert v1 == v2

    def test_different_extensions_not_equal(self):
        v1 = SnapFileValidator(allowed_extensions=[FileExtensionEnum.JSON])
        v2 = SnapFileValidator(allowed_extensions=[FileExtensionEnum.PDF])
        assert v1 != v2

    def test_different_size_not_equal(self):
        v1 = SnapFileValidator(max_size_bytes=100)
        v2 = SnapFileValidator(max_size_bytes=200)
        assert v1 != v2

    def test_none_vs_value_not_equal(self):
        v1 = SnapFileValidator(max_size_bytes=None)
        v2 = SnapFileValidator(max_size_bytes=100)
        assert v1 != v2

    def test_hash_equal_for_same_config(self):
        v1 = SnapFileValidator(allowed_extensions=[FileExtensionEnum.CSV], max_size_bytes=1024)
        v2 = SnapFileValidator(allowed_extensions=[FileExtensionEnum.CSV], max_size_bytes=1024)
        assert hash(v1) == hash(v2)

    def test_can_be_used_in_set(self):
        v1 = SnapFileValidator(max_size_bytes=512)
        v2 = SnapFileValidator(max_size_bytes=512)
        v3 = SnapFileValidator(max_size_bytes=1024)
        s = {v1, v2, v3}
        assert len(s) == 2  # v1 and v2 are equal

    def test_not_equal_to_different_type(self):
        v = SnapFileValidator()
        assert v != "not_a_validator"
        assert v != 42
        assert v != None  # noqa: E711
