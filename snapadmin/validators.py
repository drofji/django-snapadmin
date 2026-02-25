import os
import typing

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.utils.deconstruct import deconstructible
from enum import Enum


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

# TODO Fix - если делаю миграции то почему-то все поля файловые возможно из-за валидатора постоянно вызывают миграции дублированные
@deconstructible
class SnapFileValidator:
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
            except Exception:
                raise ValidationError(_("Could not verify file encoding."))
