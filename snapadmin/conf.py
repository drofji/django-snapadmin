"""
snapadmin/conf.py

The single resolution point for every ``SNAPADMIN_*`` setting the package
reads (#SIMPL1g). Before this module, ~100 call sites across the codebase
each wrote their own ``getattr(settings, "SNAPADMIN_...", default)`` — a new
user had to decide all of them to get a sensible install. :func:`get_setting`
collapses that to one line, ``SNAPADMIN_PROFILE``, without changing what any
existing call site returns when the setting is left unset.

Resolution order, mirroring :func:`snapadmin.registry.get_model_meta`'s
"explicit beats declared beats default" shape:

1. an **explicit** Django setting — ``hasattr(settings, name)``, so an
   explicitly set falsy value (``False``, ``0``, ``""``) still wins over a
   profile;
2. the active **profile preset** (``SNAPADMIN_PROFILE``), if the name has an
   entry there;
3. the **built-in default** the caller passed — unchanged from before this
   module existed.

With ``SNAPADMIN_PROFILE`` unset, step 2 never runs: every name resolves via
``getattr(settings, name, default)`` exactly as every call site did before
#SIMPL1g. That is the upgrade guarantee, pinned in ``tests/test_conf.py``.

An ``SNAPADMIN_PROFILE`` value outside :data:`PROFILES` fails closed —
:class:`~django.core.exceptions.ImproperlyConfigured` at the first setting
resolved after boot, not a silent fall-through to the built-in default.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

#: Built-in default for ``SNAPADMIN_REST_API_ENABLED`` / ``SNAPADMIN_GRAPHQL_ENABLED``.
#:
#: Both flipped ``True`` -> ``False`` at 1.0 (D4/#DEF2a): including ``snapadmin.urls`` used to mount
#: a writable REST/GraphQL surface for every registered model even in a project that only ever
#: wanted an admin, so now it has to be asked for. The value lives here, once, because the flip
#: originally landed in ``snapadmin/urls.py`` alone while ten other read sites — the system checks,
#: the dashboard and three ``snapadmin_info`` collectors — kept their own ``True`` literal and went
#: on reporting an API that was no longer mounted. Every read site imports these instead of
#: spelling the default out, and ``tests/test_api_surface_defaults.py`` fails the build if one
#: starts spelling it out again.
#:
#: ``SNAPADMIN_SWAGGER_ENABLED`` and ``SNAPADMIN_GRAPHIQL_ENABLED`` deliberately get no constant of
#: their own: each follows its parent surface's *resolved* value, so its default is a variable, not
#: a literal.
REST_API_ENABLED_DEFAULT: bool = False
GRAPHQL_ENABLED_DEFAULT: bool = False

#: The three ``SNAPADMIN_PROFILE`` values this package understands.
#: ``admin`` = admin UI only, REST/GraphQL/ES off. ``api`` = REST + GraphQL
#: on, admin minimal. ``full`` = every generated surface on.
PROFILES: tuple[str, ...] = ("admin", "api", "full")

#: profile name -> {setting name: preset value}. Populated per #SIMPL1g's
#: setting x profile x value matrix; a name absent
#: from a profile's dict simply falls through to the caller's built-in
#: default, which is correct whenever the three profiles agree — and they
#: agree almost everywhere: of the 86 settings the package reads, only the
#: four below actually change per profile. Every other setting (backups,
#: masking, SSO, exports, alerts, audit, pagination, ES tuning, …) is
#: orthogonal to "which surfaces are exposed" and keeps its built-in default
#: in every profile.
#:
#: Every profile states its four values explicitly. ``api`` and ``full`` used to
#: be empty dicts, on the reasoning that the built-in defaults already turned
#: REST and GraphQL on so there was nothing to move — but that made the preset
#: table a mirror of the defaults rather than a statement of intent, and when
#: the defaults flipped to ``False`` at 1.0 (D4/#DEF2a) both profiles silently
#: inverted with them: ``SNAPADMIN_PROFILE = "api"``, whose entire purpose is
#: "REST + GraphQL on", began resolving both to ``False``. A profile now says
#: what it means regardless of what any default happens to be.
#:
#: ``SNAPADMIN_SWAGGER_ENABLED`` and ``SNAPADMIN_GRAPHIQL_ENABLED`` are intentionally
#: absent from ``api``/``full``. Neither has a value of its own there: Swagger follows
#: whatever ``SNAPADMIN_REST_API_ENABLED`` *resolved* to and GraphiQL follows ``DEBUG``,
#: which is exactly what each read site already passes as its own default, so falling
#: through **is** the documented behaviour. Pinning Swagger ``True`` here would break
#: that cascade in the one case it exists for: a project on ``SNAPADMIN_PROFILE =
#: "full"`` that then sets ``SNAPADMIN_REST_API_ENABLED = False`` explicitly would still
#: mount ``/api/docs/`` — documentation for a REST surface with no routes, and an
#: ``ImproperlyConfigured`` if the ``[api]`` extra is not installed.
#:
#: ``admin`` pins both ``False`` rather than relying on the cascade, so that turning the
#: profile on is a single unambiguous statement rather than a chain to follow.
_PRESETS: dict[str, dict[str, Any]] = {
    "admin": {
        "SNAPADMIN_REST_API_ENABLED": False,
        "SNAPADMIN_GRAPHQL_ENABLED": False,
        # Swagger documents the REST API and GraphiQL explores the GraphQL
        # endpoint — both are pointless with their surface off, so the
        # profile turns them off explicitly rather than relying on every
        # read site to cascade the same way `urls.py` already does.
        "SNAPADMIN_SWAGGER_ENABLED": False,
        "SNAPADMIN_GRAPHIQL_ENABLED": False,
    },
    "api": {
        "SNAPADMIN_REST_API_ENABLED": True,
        "SNAPADMIN_GRAPHQL_ENABLED": True,
    },
    "full": {
        "SNAPADMIN_REST_API_ENABLED": True,
        "SNAPADMIN_GRAPHQL_ENABLED": True,
    },
}


def get_setting(name: str, default: Any = None) -> Any:
    """One ``SNAPADMIN_*`` setting, wherever it is configured.

    Replaces a direct ``getattr(settings, name, default)``. See the module
    docstring for the three-step resolution order and the backward-
    compatibility guarantee when ``SNAPADMIN_PROFILE`` is unset.
    """
    if hasattr(settings, name):
        return getattr(settings, name)

    profile = getattr(settings, "SNAPADMIN_PROFILE", None)
    if profile is None:
        return default

    if profile not in PROFILES:
        raise ImproperlyConfigured(
            f"SNAPADMIN_PROFILE={profile!r} is not a recognised profile — "
            f"choose one of {', '.join(PROFILES)}."
        )

    preset = _PRESETS[profile]
    if name in preset:
        return preset[name]
    return default
