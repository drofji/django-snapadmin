"""SnapAdmin — declarative Django admin + REST/GraphQL API package.

The most common public names are re-exported here for convenience, so
``from snapadmin import SnapModel, SnapCharField`` works alongside the original
deep paths (``from snapadmin.models import SnapModel``), which keep working
unchanged. The re-exports are **lazy** (PEP 562 ``__getattr__``): importing
``snapadmin`` — or a console-script subpackage like ``snapadmin.quickstart`` that
runs before any Django settings exist — does not import the Django-backed
``models``/``fields`` modules until one of these names is actually accessed.
"""

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    #: Resolved from the installed distribution's metadata so it always matches
    #: the packaged version (``pyproject.toml``) without a second source of truth.
    __version__ = _pkg_version("django-snapadmin")
except PackageNotFoundError:  # running from a source checkout, not pip-installed
    __version__ = "0.0.0.dev0"

# name -> defining module. Kept as data so the imports stay lazy (see __getattr__).
_LAZY_EXPORTS: dict[str, str] = {
    # Core model API + enums/exceptions (snapadmin.models)
    "SnapModel": "snapadmin.models",
    "EsStorageMode": "snapadmin.models",
    "APIToken": "snapadmin.models",
    "SnapEsUnavailable": "snapadmin.models",
    "SnapPurgeError": "snapadmin.models",
    # Field types (snapadmin.fields)
    "SnapField": "snapadmin.fields",
    "SnapCharField": "snapadmin.fields",
    "SnapTextField": "snapadmin.fields",
    "SnapEmailField": "snapadmin.fields",
    "SnapSlugField": "snapadmin.fields",
    "SnapURLField": "snapadmin.fields",
    "SnapUUIDField": "snapadmin.fields",
    "SnapIntegerField": "snapadmin.fields",
    "SnapPositiveIntegerField": "snapadmin.fields",
    "SnapPositiveSmallIntegerField": "snapadmin.fields",
    "SnapPositiveBigIntegerField": "snapadmin.fields",
    "SnapSmallIntegerField": "snapadmin.fields",
    "SnapBigIntegerField": "snapadmin.fields",
    "SnapFloatField": "snapadmin.fields",
    "SnapDecimalField": "snapadmin.fields",
    "SnapDateField": "snapadmin.fields",
    "SnapDateTimeField": "snapadmin.fields",
    "SnapTimeField": "snapadmin.fields",
    "SnapDurationField": "snapadmin.fields",
    "SnapFileField": "snapadmin.fields",
    "SnapImageField": "snapadmin.fields",
    "SnapBooleanField": "snapadmin.fields",
    "SnapJSONField": "snapadmin.fields",
    "SnapGenericIPAddressField": "snapadmin.fields",
    "SnapForeignKey": "snapadmin.fields",
    "SnapOneToOneField": "snapadmin.fields",
    "SnapManyToManyField": "snapadmin.fields",
    "SnapRichTextField": "snapadmin.fields",
    "SnapPhoneField": "snapadmin.fields",
    "SnapColorField": "snapadmin.fields",
    "SnapFunctionField": "snapadmin.fields",
    "SnapStatusBadgeField": "snapadmin.fields",
    "SnapStatusBadgeFieldChoice": "snapadmin.fields",
    # Validators (snapadmin.validators)
    "SnapPhoneValidator": "snapadmin.validators",
    "SnapColorValidator": "snapadmin.validators",
    "SnapFileValidator": "snapadmin.validators",
}

__all__ = ["__version__", *sorted(_LAZY_EXPORTS)]


def __getattr__(name: str):
    """Lazily resolve a blessed re-export the first time it is accessed."""
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module_path), name)


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
