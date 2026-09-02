"""
demo/core/tenancy.py

Illustrative tenant resolvers for the demo project (#FUT1) — wired via
``SNAPADMIN_TENANT_RESOLVER`` / ``SNAPADMIN_TENANT_USER_RESOLVER`` in
``demo/core/settings.py``. See ``snapadmin.tenancy`` for the mechanism this
plugs into.

The demo has no dedicated "organisation" model, so it uses the requesting
user's email domain as the tenant — a genuinely common real-world pattern
(every ``@acme.example`` user belongs to the "acme.example" tenant) and one
that needs no extra demo data to exercise. A real project's resolver more
often reads a claim off an SSO token, a subdomain, or a membership table;
swap the body, keep the signature.
"""

from __future__ import annotations

from typing import Any


def _tenant_from_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].lower()


def resolve_demo_tenant(request: Any) -> str | None:
    """``SNAPADMIN_TENANT_RESOLVER`` — an explicit ``X-Snapadmin-Demo-Tenant``
    header wins (so a single curl/API-token caller can address any tenant
    without a matching demo user for it); otherwise the authenticated
    caller's email domain. ``None`` for an anonymous request — every
    tenant-scoped surface then fails closed, exactly as intended.
    """
    header = request.META.get("HTTP_X_SNAPADMIN_DEMO_TENANT")
    if header:
        return header
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    return _tenant_from_email(getattr(user, "email", None))


def resolve_demo_tenant_for_user(user: Any) -> str | None:
    """``SNAPADMIN_TENANT_USER_RESOLVER`` — replays a job submitter's tenant
    for an async export/import worker, which has no request in hand. Mirrors
    :func:`resolve_demo_tenant`'s email-domain fallback (no header exists in
    a background context to check first).
    """
    if user is None:
        return None
    return _tenant_from_email(getattr(user, "email", None))
