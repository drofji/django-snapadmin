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

#: The three ``SNAPADMIN_PROFILE`` values this package understands.
#: ``admin`` = admin UI only, REST/GraphQL/ES off. ``api`` = REST + GraphQL
#: on, admin minimal. ``full`` = today's defaults — every preset value below
#: equals the built-in default passed at its call site, by design: setting
#: ``SNAPADMIN_PROFILE = "full"`` is a documented no-op.
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
#: ``api`` has no entries: its built-in defaults already turn REST and
#: GraphQL on, which is exactly what "api" means — the profile exists as a
#: self-documenting declaration of intent, not because any setting needs to
#: move. ``full`` likewise has no entries by construction: it *is* today's
#: defaults.
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
    "api": {},
    "full": {},
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
