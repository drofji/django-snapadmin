"""SnapAdmin — declarative Django admin + REST/GraphQL API package."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    #: Resolved from the installed distribution's metadata so it always matches
    #: the packaged version (``pyproject.toml``) without a second source of truth.
    __version__ = _pkg_version("django-snapadmin")
except PackageNotFoundError:  # running from a source checkout, not pip-installed
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
