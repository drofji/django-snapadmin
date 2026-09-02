"""
snapadmin/tenancy.py

Row-level multi-tenancy (#FUT1). The verdict (see the project's own decision
record) is a nullable ``tenant_id`` column plus a manager/queryset filter —
not schema-per-tenant — because SnapAdmin is an ORM-level layer bolted onto a
project it does not own, and a filter is the one mechanism every surface it
generates (the admin, REST, GraphQL, Elasticsearch routing, exports, imports,
the offline cache) can apply identically.

**Opt-in, per model.** A model declares ``tenant_scoped = True`` and adds a
tenant column (:func:`tenant_field`) — retrofitting isolation onto every
model in an existing project is not something this library may decide for
it. A model that never sets ``tenant_scoped`` is entirely unaffected: every
function here is a no-op for it.

**Default-deny.** For a model that *did* opt in, an unfiltered queryset must
be unreachable, not merely discouraged: no tenant bound in the current
context means an empty result, never every row. The one deliberate exception
is :func:`use_all_tenants`, an explicit, audited escape hatch for background
code whose job is inherently cross-tenant (the retention purge, the
Elasticsearch reindex) — application code must never reach for it to work
around a scoping failure.

**The honest limitation.** This is *logical* isolation: one query path that
forgets to go through :func:`scope_queryset` (or the manager/queryset methods
built on it) leaks. It is not physical separation the way a separate schema
or database would be — see ``SECURITY.md`` for where that limitation is
stated for an integrator, not just here for a reader of this module.

**A ``NULL`` tenant column is unassigned data, not shared data.** It matches
no tenant's filter (ordinary SQL equality semantics — ``tenant_id = 'a'``
never matches a ``NULL`` row), so it needs no special case anywhere in this
module; it is simply invisible to every tenant until something assigns it.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from django.db import models as django_models
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from snapadmin.conf import get_setting
from snapadmin.logging_config import get_logger
from snapadmin.registry import get_model_meta

logger = get_logger(__name__)

#: Distinguishes "nothing has bound a tenant context at all" from a context
#: explicitly bound to ``None`` (a resolver that ran and found no tenant for
#: this caller). Both fail closed identically in :func:`scope_queryset`; the
#: distinction exists for :func:`tenant_context_bound`, which diagnostics and
#: tests use to tell "nobody asked" apart from "asked, got nothing".
_UNSET = object()

#: Sentinel bound by :func:`use_all_tenants` — the one explicit, audited
#: bypass of tenant scoping. Never set implicitly by any resolver.
ALL_TENANTS = object()

_current_tenant: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "snapadmin_current_tenant", default=_UNSET
)


def get_current_tenant() -> Any:
    """The tenant value bound to this request or task.

    Returns :data:`ALL_TENANTS` inside a :func:`use_all_tenants` block, the
    bound value inside a :func:`use_tenant` block, or ``None`` when nothing
    is bound — including a context explicitly bound to ``None``. Use
    :func:`tenant_context_bound` to tell those last two apart.
    """
    value = _current_tenant.get()
    return None if value is _UNSET else value


def tenant_context_bound() -> bool:
    """Whether a tenant context has been entered at all, bound-to-``None`` included."""
    return _current_tenant.get() is not _UNSET


@contextmanager
def use_tenant(value: Any) -> Iterator[None]:
    """Bind the current tenant for the duration of the ``with`` block.

    :class:`SnapTenantMiddleware` calls this once per request. Background
    code that replays a submitter's tenant outside any request — an export
    or import job running on a Celery worker — calls it around the work it
    does on that submitter's behalf, using the tenant captured on the job at
    submission time.
    """
    token = _current_tenant.set(value)
    try:
        yield
    finally:
        _current_tenant.reset(token)


def bind_tenant(value: Any) -> contextvars.Token:
    """Manually bind the current tenant, returning a token for :func:`unbind_tenant`.

    Prefer :func:`use_tenant`'s ``with`` block wherever "bind" and "unbind"
    happen in the same Python scope. This pair exists for a caller whose
    framework only ever gives a "before" and an "after" hook as two
    *separate* methods, with no scope spanning both — DRF's ``initial()`` /
    ``finalize_response()`` (see :class:`~snapadmin.api.authentication.
    SnapAPIAuthMixin`) is exactly that shape, and it needs this rebind
    because DRF's own authentication (token auth in particular) resolves
    ``request.user`` lazily, inside ``initial()`` — after
    :class:`SnapTenantMiddleware` already ran and bound whatever
    ``request.user`` was at the *Django* middleware layer (correct for
    session auth, always anonymous for token auth at that point). Every
    caller of this function **must** call :func:`unbind_tenant` with the
    returned token — an unmatched bind leaks one request's tenant into
    whatever runs next on the same thread/task.
    """
    return _current_tenant.set(value)


def unbind_tenant(token: contextvars.Token) -> None:
    """Undo one :func:`bind_tenant` call. See its docstring."""
    _current_tenant.reset(token)


@contextmanager
def use_all_tenants() -> Iterator[None]:
    """The one explicit, audited escape hatch from tenant scoping.

    Every tenant-scoped queryset built inside this block sees every tenant's
    rows. Reserved for background code whose job is inherently cross-tenant:
    the retention purge (a row's age decides whether it is purged, not its
    tenant) and the Elasticsearch reindex (the index must stay complete
    across every tenant, or :func:`snapadmin.reindexing.verify_index`'s
    count comparison would mismatch by construction). Application code must
    never reach for this to work around a scoping failure — that is exactly
    the default-deny guarantee this module exists to hold.
    """
    token = _current_tenant.set(ALL_TENANTS)
    try:
        yield
    finally:
        _current_tenant.reset(token)


def is_tenant_scoped(model: type[django_models.Model]) -> bool:
    """Whether ``model`` opted into row-level tenant isolation."""
    return bool(get_model_meta(model, "tenant_scoped", False))


def tenant_field_name(model: type[django_models.Model]) -> str:
    """The column ``model`` stores its tenant value in.

    ``model.tenant_field`` (or a decorator/registry override of the same
    name) when set, otherwise ``"tenant_id"``.
    """
    return get_model_meta(model, "tenant_field", None) or "tenant_id"


def scope_queryset(model: type[django_models.Model], queryset: Any) -> Any:
    """Apply tenant scoping to ``queryset`` for a tenant-scoped ``model``.

    A no-op — ``queryset`` returned unchanged — for a model that never set
    ``tenant_scoped = True``. For one that did:

    * inside :func:`use_all_tenants`, ``queryset`` is returned unfiltered;
    * with a tenant bound (:func:`use_tenant`), ``queryset.filter(**{field:
      tenant})``;
    * with **no** tenant bound — including a resolver that explicitly
      resolved to ``None`` — ``queryset.none()``, never every row. This is
      the default-deny guarantee: a caller for whom no tenant could be
      determined sees nothing, not everything.

    Works for anything exposing ``.filter()``/``.none()`` with the same
    contract as a Django ``QuerySet`` — including
    :class:`snapadmin.models.EsQuerySet`, whose ``.filter()`` only ever
    matches flat ``field=value`` equality, which is exactly what a tenant
    filter is.
    """
    if not is_tenant_scoped(model):
        return queryset
    current = _current_tenant.get()
    if current is ALL_TENANTS:
        return queryset
    if current is _UNSET or current is None:
        return queryset.none()
    return queryset.filter(**{tenant_field_name(model): current})


def tenant_field(**kwargs: Any) -> django_models.CharField:
    """A pre-configured tenant-id column for a tenant-scoped model.

    Usage::

        class Order(SnapModel):
            tenant_id = tenant_field()
            tenant_scoped = True

    Nullable by default so the migration this column adds never breaks an
    existing row — see the module docstring for why a ``NULL`` value needs
    no special handling in :func:`scope_queryset`. Every keyword here is
    just a default: pass ``max_length=``, or ``to=``/``on_delete=`` (via
    ``models.ForeignKey`` kwargs are not accepted here — declare a real
    ``ForeignKey`` by hand instead when the tenant is itself a project model)
    to fit a project's own tenant identifier shape.
    """
    opts: dict[str, Any] = {
        "max_length": 64,
        "null": True,
        "blank": True,
        "db_index": True,
        "verbose_name": _("Tenant"),
        "help_text": _("Row-level tenant isolation key — see SnapModel.tenant_scoped."),
    }
    opts.update(kwargs)
    return django_models.CharField(**opts)


def resolve_tenant_for_request(request: Any) -> Any:
    """The current tenant for an HTTP request, via ``SNAPADMIN_TENANT_RESOLVER``.

    The setting is a dotted path to ``resolver(request) -> tenant value |
    None``. Unset (the default) resolves every request to ``None`` — every
    tenant-scoped model is then unreachable through any request-driven
    surface, the correct fail-closed posture until a project configures a
    resolver of its own (typically reading a claim off ``request.user``, a
    subdomain, or a header validated upstream).
    """
    path = get_setting("SNAPADMIN_TENANT_RESOLVER", None)
    if not path:
        return None
    resolver = import_string(path) if isinstance(path, str) else path
    return resolver(request)


def resolve_tenant_for_user(user: Any) -> Any:
    """A tenant for background code acting on a user's behalf, no request in hand.

    Threads a job's submitter through to the worker that actually runs it
    (an async export or import): the tenant is resolved once, from the
    submitter, when the job is created, and stamped onto the job row so the
    worker can replay it via :func:`use_tenant` regardless of which process
    or how much later it runs. The setting is a dotted path to
    ``resolver(user) -> tenant value | None``; unset resolves to ``None`` —
    the same fail-closed posture as an unconfigured
    ``SNAPADMIN_TENANT_RESOLVER``, so a tenant-scoped model's export/import
    jobs simply cannot be created until a project configures this.
    """
    path = get_setting("SNAPADMIN_TENANT_USER_RESOLVER", None)
    if not path:
        return None
    resolver = import_string(path) if isinstance(path, str) else path
    return resolver(user)


class SnapTenantMiddleware:
    """Bind the current tenant (see :func:`use_tenant`) for one request's lifetime.

    Resolves via :func:`resolve_tenant_for_request` on every request and
    always clears the binding when the response leaves this middleware —
    including on an exception — so a context var never outlives the request
    that set it and leaks one caller's tenant into whatever runs next on the
    same worker thread/task.

    Add it to ``MIDDLEWARE``, anywhere after authentication has populated
    ``request.user`` if a resolver reads that (Django's
    ``AuthenticationMiddleware`` normally sits well above where a project
    adds its own middleware, so this is rarely a real ordering concern).
    """

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: Any) -> Any:
        tenant = resolve_tenant_for_request(request)
        with use_tenant(tenant):
            return self.get_response(request)


class SnapTenantRebindMixin:
    """DRF mixin: rebind the current tenant once DRF's own authentication has
    actually resolved ``request.user``/``request.auth`` (#FUT1b).

    :class:`SnapTenantMiddleware` already binds a tenant from *Django*'s own
    middleware-layer ``request.user`` — correct for session auth
    (``AuthenticationMiddleware`` resolves the session before any view runs)
    but stale for token auth: DRF's authentication is lazy, only actually run
    when ``initial()`` below calls ``perform_authentication()``. Mix this
    into any DRF view or viewset that reads tenant-scoped rows and may be
    reached by a token-authenticated caller — every such view in this
    package does (:class:`~snapadmin.api.authentication.SnapAPIAuthMixin`
    composes it for the token-scoped viewsets; ``snapadmin.api.offline``'s
    ``OfflineModelDataView`` uses it directly, since it deliberately keeps
    DRF's project-wide default authentication rather than SnapAdmin's own
    per-request-resolved classes).

    ``initial()`` and ``finalize_response()`` bracket exactly one request —
    the same guarantee :class:`SnapTenantMiddleware` gives with a single
    ``with`` block, just split across DRF's two separate hook points, since
    nothing else spans both.
    """

    def initial(self, request: Any, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)
        self._snapadmin_tenant_token = bind_tenant(resolve_tenant_for_request(request))

    def finalize_response(self, request: Any, response: Any, *args: Any, **kwargs: Any) -> Any:
        response = super().finalize_response(request, response, *args, **kwargs)
        token = getattr(self, "_snapadmin_tenant_token", None)
        if token is not None:
            unbind_tenant(token)
        return response
