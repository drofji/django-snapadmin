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
accessor every reader goes through: registry entry first, class attribute
second. Both declaration styles therefore read identically, and a ``SnapModel``
subclass can still override a single key through the decorator without losing
the rest of its class-level configuration.

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
    replacing a direct ``getattr(model, name, default)``. It looks in two
    places, in order:

    1. the model's **registry entry** — what
       :func:`snapadmin.models.snap_model` stored for a decorated model;
    2. the model's **class attribute** — what a
       :class:`~snapadmin.models.SnapModel` subclass declares inline.

    So both declaration styles read the same, an undecorated ``SnapModel``
    subclass keeps behaving exactly as before, and decorating a ``SnapModel``
    subclass overrides only the keys actually passed — every other setting
    still comes from the class.

    Deliberately mirrors ``getattr``'s signature and dynamism: the values are a
    heterogeneous configuration namespace keyed by name, so ``default`` and the
    return value are as loosely typed as the attribute lookup they replace.
    """
    value = _REGISTRY.get(model, {}).get(name, _MISSING)
    if value is not _MISSING:
        return value
    return getattr(model, name, default)
