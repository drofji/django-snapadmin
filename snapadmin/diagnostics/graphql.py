"""
Diagnostics collector for the GraphQL surface.

Health probe: when ``SNAPADMIN_GRAPHQL_ENABLED`` is on, it builds the schema and
runs a trivial ``{ __typename }`` query in-process (no HTTP, no resolvers, no DB)
to confirm the schema is buildable and executable. When the feature is off it
returns a single ``{"enabled": False}`` line and is **never** a health failure —
so a project that turned GraphQL off is never falsely alerted.
"""

from __future__ import annotations

from django.conf import settings

from snapadmin.diagnostics.registry import register


@register("graphql", title="GraphQL", icon="🕸", order=36, health_probe=True)
def collect(*, verbose: bool) -> dict:
    """Collect the GraphQL section."""
    if not getattr(settings, "SNAPADMIN_GRAPHQL_ENABLED", True):
        return {"enabled": False}

    data: dict = {"enabled": True}
    try:
        from snapadmin.api.graphql import schema

        result = schema.execute("{ __typename }")
        if result.errors:
            data["ok"] = False
            data["error"] = "; ".join(str(error) for error in result.errors)
        else:
            data["ok"] = True
    except Exception as exc:
        # Enabled but the schema can't be built/executed (e.g. graphene missing in a
        # future optional [graphql] extra, or a schema error) — a real failure.
        data["ok"] = False
        data["error"] = str(exc)
    return data
