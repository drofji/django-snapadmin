"""
Reusable, ``deconstructible`` field validators for SnapAdmin.

``SnapPhoneValidator`` (E.164 / common national phone formats), ``SnapColorValidator``
(``#RGB``/``#RRGGBB`` hex) and ``SnapFileValidator`` (extension + size limits). Each is
``@deconstructible`` so it round-trips through a field's ``deconstruct()`` unchanged and
never forces a spurious migration. They back the corresponding ``Snap*Field`` types but
are public for direct use on any Django field.
"""

import os
import re
import typing

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.utils.deconstruct import deconstructible
from enum import Enum


# --- Validators ---

@deconstructible
class SnapPhoneValidator:
    """Validates phone numbers in E.164 or common national formats.

    Common grouping separators — spaces, hyphens and parentheses — are stripped
    before matching, so the international spaced form (``+49 89 1234567``) and the
    national grouped form (``(089) 123-456``) both validate, not only the
    separator-free E.164 form.
    """

    #: Applied to the *separator-normalised* value. Two branches:
    #:  * ``\+?[1-9]\d{6,14}`` — international / E.164 (optional ``+``, country or
    #:    trunk digit 1-9, then 6–14 more): 7–15 digits, no leading zero.
    #:  * ``\d{7,15}`` — national numbers that keep a leading trunk zero
    #:    (e.g. German ``089…``), which E.164 drops.
    _PATTERN = re.compile(r"^(\+?[1-9]\d{6,14}|\d{7,15})$")

    def __call__(self, value: str) -> None:
        clean = re.sub(r"[\s\-()]", "", value)
        if not self._PATTERN.match(clean):
            raise ValidationError(
                _("Enter a valid phone number (e.g. +49151234567 or 089-123456)."),
                code="invalid_phone",
            )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SnapPhoneValidator)

    def __hash__(self) -> int:
        return hash(self.__class__)


@deconstructible
class SnapColorValidator:
    """Validates CSS hex color strings (#RGB or #RRGGBB)."""

    _PATTERN = re.compile(r"^#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})$")

    def __call__(self, value: str) -> None:
        if not self._PATTERN.match(value):
            raise ValidationError(
                _("Enter a valid hex color code (e.g. #FF5733 or #F53)."),
                code="invalid_color",
            )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SnapColorValidator)

    def __hash__(self) -> int:
        return hash(self.__class__)


# --- Enums ---

class FileExtensionEnum(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    TXT = "txt"
    CSV = "csv"
    JPG = "jpg"
    PNG = "png"
    JSON = "json"


class FileEncodingEnum(str, Enum):
    UTF8 = "utf-8"
    UTF16 = "utf-16"
    CP1251 = "cp1251"
    LATIN1 = "latin-1"
    ASCII = "ascii"


# --- Validators ---

@deconstructible
class SnapFileValidator:
    """
    Reusable file validator that checks extension, encoding, and file size.

    The ``@deconstructible`` decorator ensures Django's migration framework
    can serialise this validator. Equality is based on the validator's
    configuration tuple so Django does not generate spurious duplicate
    migrations when the model definition is unchanged.
    """
    def __init__(
            self,
            allowed_extensions: typing.List[typing.Union[FileExtensionEnum, str]] = None,
            allowed_encodings: typing.List[typing.Union[FileEncodingEnum, str]] = None,
            max_size_bytes: int = None,
    ):
        self.allowed_extensions = [
            ext.value.lower() if isinstance(ext, FileExtensionEnum) else ext.lower()
            for ext in allowed_extensions
        ] if allowed_extensions else None

        self.allowed_encodings = [
            enc.value if isinstance(enc, FileEncodingEnum) else enc
            for enc in allowed_encodings
        ] if allowed_encodings else None

        self.max_size_bytes = max_size_bytes

    def __call__(self, file):
        if self.allowed_extensions:
            ext = os.path.splitext(file.name)[1].lower().replace('.', '')
            if ext not in self.allowed_extensions:
                raise ValidationError(
                    _("File extension '%(ext)s' is not allowed. Allowed: %(allowed)s"),
                    params={'ext': ext, 'allowed': ", ".join(self.allowed_extensions)}
                )

        if self.max_size_bytes and file.size > self.max_size_bytes:
            raise ValidationError(
                _("File size is %(size)s bytes. Max allowed is %(max_size)s bytes."),
                params={'size': file.size, 'max_size': self.max_size_bytes}
            )

        if self.allowed_encodings:
            try:
                content = file.read(1024 * 1024)
                file.seek(0)
            except Exception:
                # Only the actual I/O risk (reading/rewinding the upload) is
                # caught here — a genuinely invalid encoding is a normal,
                # expected outcome of the loop below and must not be swallowed
                # into this generic message.
                raise ValidationError(_("Could not verify file encoding."))

            is_valid = False
            for enc in self.allowed_encodings:
                try:
                    content.decode(enc)
                    is_valid = True
                    break
                except (UnicodeDecodeError, LookupError):
                    continue

            if not is_valid:
                raise ValidationError(
                    _("Invalid encoding. Allowed: %(encodings)s"),
                    params={'encodings': ", ".join(self.allowed_encodings)}
                )

    def __eq__(self, other):
        """
        Equality check used by Django's migration framework.
        Prevents spurious duplicate migrations on each makemigrations run.
        """
        if not isinstance(other, SnapFileValidator):
            return False
        return (
            self.allowed_extensions == other.allowed_extensions
            and self.allowed_encodings == other.allowed_encodings
            and self.max_size_bytes == other.max_size_bytes
        )

    def __hash__(self):
        return hash((
            tuple(self.allowed_extensions or []),
            tuple(self.allowed_encodings or []),
            self.max_size_bytes,
        ))
