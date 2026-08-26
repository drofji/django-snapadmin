"""
snapadmin/registry.py

The single place that answers "is this model a SnapAdmin model?".

Every SnapAdmin surface is gated on that one question: which models get an admin
registration, a REST viewset, a GraphQL type, an offline cache endpoint, an
Elasticsearch mapping on ``post_migrate``, and which ones the system checks
inspect. Each gate used to ask it by testing ``issubclass(model, SnapModel)``,
which hard-wires the answer to a base class — a model could only be a SnapAdmin
model by inheriting from :class:`~snapadmin.models.SnapModel`.

This module turns the question into a lookup. ``SnapModel`` registers every
subclass as it is declared (``__init_subclass__``), the gates ask
:func:`is_registered`, and the answer no longer depends on the inheritance
chain. That seam is what lets a plain ``django.db.models.Model`` opt in with
:func:`snapadmin.models.snap_model` without reopening a single gate.

The second half of the question is *how* a model is configured. A ``SnapModel``
subclass answers with class attributes (``api_write_fields``, ``offline_mode``,
…); a decorated plain model answers with the keywords it passed to the
decorator, which land in its registry entry. :func:`get_model_meta` is the one
accessor every reader goes through, and it resolves the full **four-tier
precedence rule** (#RFC1e): explicit decorator argument > class attribute >
project-wide ``SNAPADMIN_<NAME>`` setting > the caller's built-in default. Both
declaration styles therefore read identically, and a ``SnapModel`` subclass can
still override a single key through the decorator without losing the rest of
its class-level configuration. See :func:`get_model_meta` for the settings
tier's naming convention and a caveat about when it actually fires.

The module is deliberately dependency-free (it imports neither Django nor
:mod:`snapadmin.models`) so ``snapadmin.models`` can import it at module level
without a cycle, and so registration can happen while a model class is still
half-built: ``__init_subclass__`` runs inside ``ModelBase.__new__``, before
Django has attached ``_meta``, so nothing here may touch the model beyond its
identity.

Entries are held **weakly**. A model class that goes away — a throwaway class in
a test, a model discarded by ``isolate_apps`` — drops out of the registry with
it, exactly as it drops out of Django's own app registry.
"""

from __future__ import annotations

from typing import Any
from weakref import WeakKeyDictionary

#: Model class -> its SnapAdmin metadata. Membership *is* registration; the
#: value carries per-model configuration. It is empty for a ``SnapModel``
#: subclass, which keeps its configuration as class attributes.
_REGISTRY: WeakKeyDictionary[type, dict[str, Any]] = WeakKeyDictionary()

#: Sentinel for "the caller asked for a name nothing has configured". Distinct
#: from ``None``, which several settings use as a meaningful value.
_MISSING = object()


def register(model: type, **meta: Any) -> type:
    """Register ``model`` as a SnapAdmin model, merging ``meta`` into its entry.

    Idempotent: registering the same model twice keeps one entry and updates it
    with the new keys, so several registration paths can name the same model
    without clobbering each other. Returns ``model`` unchanged, which is what
    makes a registering class decorator a one-liner.
    """
    _REGISTRY.setdefault(model, {}).update(meta)
    return model


def is_registered(model: type) -> bool:
    """Whether ``model`` is a SnapAdmin model — the gate every surface asks."""
    return model in _REGISTRY


def meta_for(model: type) -> dict[str, Any]:
    """The metadata registered for ``model``; an empty dict when it has none.

    A copy, so :func:`register` stays the only way to mutate an entry.
    """
    return dict(_REGISTRY.get(model, {}))


def get_model_meta(model: type, name: str, default: Any = None) -> Any:
    """One model-level configuration value, wherever the model declared it.

    The single accessor every SnapAdmin surface uses to read a model-level
    setting (``api_write_fields``, ``offline_mode``, ``search_fields``, …),
    replacing a direct ``getattr(model, name, default)``. It resolves the full
    precedence rule (#RFC1e), checking four places in order and returning the
    first one that actually supplied a value:

    1. the model's **registry entry** — what
       :func:`snapadmin.models.snap_model` (or :func:`snapadmin.models.snap_property`'s
       host attribute) stored for a decorated model;
    2. the model's **class attribute** — what a
       :class:`~snapadmin.models.SnapModel` subclass declares inline;
    3. a project-wide **``SNAPADMIN_<NAME>`` setting** — ``name`` upper-cased
       and prefixed, e.g. ``get_model_meta(Product, "api_read_only", False)``
       also consults ``settings.SNAPADMIN_API_READ_ONLY`` — resolved through
       :func:`snapadmin.conf.get_setting`, so a ``SNAPADMIN_PROFILE`` preset
       can supply it too;
    4. the caller's **built-in default** — the ``default`` argument, unchanged
       from before this tier existed.

    So both declaration styles read the same, an undecorated ``SnapModel``
    subclass keeps behaving exactly as before, and decorating a ``SnapModel``
    subclass overrides only the keys actually passed — every other setting
    still comes from the class.

    **The settings tier is live for a decorated plain model and dead for a
    ``SnapModel`` subclass, and that is not a bug.** ``SnapModel`` declares a
    concrete class attribute for every name this function is ever called with
    (``api_read_only = False``, ``es_index_enabled = False``, …), and ``getattr``
    finds that *inherited* value long before tier 3 is reached — a subclass
    does not have to redeclare a name for tier 2 to answer it. The
    ``SNAPADMIN_<NAME>`` tier therefore only ever fires for a plain model
    registered with :func:`snapadmin.models.snap_model`, which carries no such
    base-class defaults: it is what lets that route inherit a project-wide
    posture instead of always falling straight to this function's hard-coded
    ``default`` argument, one more way the two doors converge (#RFC1/#PAR1).

    Deliberately mirrors ``getattr``'s signature and dynamism: the values are a
    heterogeneous configuration namespace keyed by name, so ``default`` and the
    return value are as loosely typed as the attribute lookup they replace.
    Note this is a *general* mechanism, not a registry of specific supported
    settings — it works for any ``name`` a caller passes, without a per-name
    allowlist. It is unrelated to the narrower, independently-named precedence
    chains a few callers already build for themselves on top of this function
    (e.g. ``api/filters.py``'s ``api_default_text_lookups`` → the differently
    -named project setting ``SNAPADMIN_API_TEXT_LOOKUPS`` → a library
    default) — those predate this tier, keep their own setting names, and are
    unaffected by it.
    """
    value = _REGISTRY.get(model, {}).get(name, _MISSING)
    if value is not _MISSING:
        return value

    value = getattr(model, name, _MISSING)
    if value is not _MISSING:
        return value

    # Imported lazily, not at module level: this keeps the module's own
    # "imports neither Django nor snapadmin.models" invariant intact for
    # register()/is_registered(), which run inside ModelBase.__new__ while a
    # model class is still half-built. get_model_meta() is never called from
    # there — only later, once a model is fully built and Django is already
    # configured — so the import carries none of that risk.
    from snapadmin.conf import get_setting
    return get_setting(f"SNAPADMIN_{name.upper()}", default)
