"""
snapadmin/masking.py

PII data masking.

Obfuscates sensitive fields (emails, phone numbers, IDs, …) in both the admin
and the auto-generated REST API. Which fields are sensitive is declared once, in
settings::

    SNAPADMIN_MASKED_FIELDS = {
        "users.UserModel": ["email", "phone_number"],
        "customers.Profile": ["passport_number", "billing_address"],
    }

*How* a field is obfuscated is the built-in, type-driven masker by default. A
second, optional setting overrides that per field — a regex rewrite, a constant
redaction, and/or the permission that unlocks this one field::

    SNAPADMIN_MASKING_RULES = {
        "customers.Profile": {
            # keep the last 4 digits, star the rest
            "passport_number": {"pattern": r"\\d(?=\\d{4})", "replacement": "*"},
            # never show it, to anyone below the field permission
            "billing_address": {"replacement": "[redacted]",
                                "permission": "customers.view_profile_address"},
        },
    }

A field named in ``SNAPADMIN_MASKING_RULES`` is masked whether or not it also
appears in ``SNAPADMIN_MASKED_FIELDS`` — the rule *is* the declaration. Listing a
field in the old setting alone keeps the exact behaviour it always had, so no
existing configuration changes meaning.

Who sees raw data is a permission decision, evaluated per request:

* superusers                            → always raw
* holders of ``snapadmin.view_raw_pii`` → raw
* holders of a rule's ``permission``    → raw, for that one field
* everyone else                         → masked

The same rules drive the admin (list view + change form + the audit-log diff),
the API serializer, GraphQL and background exports, so an external frontend
consuming the API receives already-masked data.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings

from snapadmin.logging_config import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from django.contrib.auth.base_user import AbstractBaseUser
    from django.contrib.auth.models import AnonymousUser

    UserLike = AbstractBaseUser | AnonymousUser | None

logger = get_logger(__name__)

#: The custom permission that unlocks raw PII. Declared on APIToken.Meta so it is
#: created for the ``snapadmin`` app; assign it to trusted staff groups.
PII_PERMISSION = "snapadmin.view_raw_pii"

#: Longest value a configured regex is allowed to run against. Catastrophic
#: backtracking needs a long subject to blow up, and no PII field is legitimately
#: this big, so anything larger is masked by the built-in masker instead. Paired
#: with the nested-quantifier rejection in :func:`_compiled`, this keeps a
#: mistyped pattern from turning a page render into a hang.
MAX_REGEX_INPUT = 4096

#: Compiled patterns, keyed by pattern text: ``None`` marks one that was rejected
#: or failed to compile, so a bad rule is reported once and then costs nothing.
#: Bounded by the number of distinct patterns in settings.
_PATTERN_CACHE: dict[str, re.Pattern[str] | None] = {}


def _model_entry(setting: str, app_label: str, model_name: str):
    """Look up ``app_label.model_name`` in a model-keyed setting, or ``None``.

    Keys are matched case-insensitively on both halves, so ``"demo.Customer"``
    and ``"demo.customer"`` resolve identically.
    """
    raw = getattr(settings, setting, None) or {}
    wanted = f"{app_label}.{model_name}".lower()
    for key, value in raw.items():
        if str(key).lower() == wanted:
            return value
    return None


def get_masking_rules(app_label: str, model_name: str) -> dict[str, dict]:
    """Return the ``{field: rule}`` map configured for ``app_label.model_name``.

    Reads ``SNAPADMIN_MASKING_RULES``. Each rule is a dict with any of:

    * ``pattern`` — a regex applied to the value with :func:`re.sub`;
    * ``replacement`` — the substitution text (default ``"*"``); with no
      ``pattern`` it replaces the whole value, which is how a constant
      redaction like ``"[redacted]"`` is written;
    * ``permission`` — a permission string that unlocks the raw value for this
      field alone (see :func:`user_can_view_pii`).

    A rule that is not a dict is ignored — a malformed entry falls back to the
    built-in masker rather than silently revealing anything.
    """
    rules = _model_entry("SNAPADMIN_MASKING_RULES", app_label, model_name) or {}
    return {str(field): rule for field, rule in rules.items() if isinstance(rule, dict)}


def get_masked_fields(app_label: str, model_name: str) -> list[str]:
    """Return the masked field names configured for ``app_label.model_name``.

    The union of ``SNAPADMIN_MASKED_FIELDS`` (in its declared order) and any
    field carrying a rule in ``SNAPADMIN_MASKING_RULES`` — declaring a rule is
    itself a declaration that the field is sensitive. Keys in both settings are
    matched case-insensitively on both the app label and the model name, so
    ``"demo.Customer"`` and ``"demo.customer"`` resolve identically.
    """
    fields = list(_model_entry("SNAPADMIN_MASKED_FIELDS", app_label, model_name) or [])
    for field in get_masking_rules(app_label, model_name):
        if field not in fields:
            fields.append(field)
    return fields


def _rule_for(field: str | None, app_label: str | None, model_name: str | None) -> dict | None:
    """Resolve one field's rule from either an explicit model or a dotted name.

    ``field`` is a plain field name when ``app_label``/``model_name`` are given,
    or the fully qualified ``"app_label.ModelName.field"`` when they are not.
    """
    if not field:
        return None
    name = str(field)
    if not (app_label and model_name):
        parts = name.split(".")
        if len(parts) != 3:
            return None
        app_label, model_name, name = parts
    return get_masking_rules(app_label, model_name).get(name)


def user_can_view_pii(
    user: UserLike,
    field: str | None = None,
    *,
    app_label: str | None = None,
    model_name: str | None = None,
) -> bool:
    """Whether ``user`` is allowed to see unmasked PII.

    True for authenticated, active superusers and holders of
    ``snapadmin.view_raw_pii``; anonymous / inactive users always get masked.

    Called with a ``field`` the answer becomes per-field: a rule declaring a
    ``permission`` in ``SNAPADMIN_MASKING_RULES`` also unlocks *that* field for
    whoever holds it, without granting the blanket ``view_raw_pii``. Name the
    field either as ``field="email", app_label="demo", model_name="Customer"``
    or as the single dotted ``field="demo.Customer.email"``. The argument is
    additive — omitted, the check behaves exactly as it always has.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if not user.is_active:
        return False
    if user.is_superuser:
        return True
    if user.has_perm(PII_PERMISSION):
        return True
    permission = (_rule_for(field, app_label, model_name) or {}).get("permission")
    return bool(permission) and bool(user.has_perm(str(permission)))


def _has_nested_quantifier(pattern: str) -> bool:
    """Heuristic: does ``pattern`` quantify a group that is itself quantified?

    ``(a+)+``, ``(a*)*`` and friends are the classic catastrophic-backtracking
    shape — the one that turns a 30-character input into minutes of CPU. Static
    detection is necessarily approximate (``re`` has no timeout to fall back
    on), and it errs towards rejection: a false positive costs a configured
    rule its regex and falls back to the built-in masker, never raw data. A
    literal ``{`` reads as a quantifier here, so escape it as ``\\{``.
    """
    stack: list[bool] = []
    quantified = False  # does the group currently being scanned hold a quantifier?
    in_class = False
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "(":
            stack.append(quantified)
            quantified = False
        elif char == ")":
            inner = quantified
            outer = stack.pop() if stack else False
            if pattern[index + 1: index + 2] in ("*", "+", "{"):
                if inner:
                    return True
                quantified = True
            else:
                # Not quantified itself, but a quantifier inside it still counts
                # towards whichever group encloses this one.
                quantified = outer or inner
        elif char in ("*", "+", "{"):
            quantified = True
        index += 1
    return False


def _compiled(pattern: str) -> re.Pattern[str] | None:
    """Compile ``pattern`` once, or return ``None`` if it is unusable.

    Unusable means it does not compile, or it carries a nested quantifier that
    could backtrack catastrophically against production data. Either way the
    reason is logged once (patterns come from settings, never from a request,
    so logging one is not a data leak) and the caller masks with the built-in
    masker instead.
    """
    if pattern in _PATTERN_CACHE:
        return _PATTERN_CACHE[pattern]
    compiled: re.Pattern[str] | None = None
    if _has_nested_quantifier(pattern):
        logger.warning(
            "snapadmin.masking.pattern_rejected",
            pattern=pattern,
            reason="nested quantifier risks catastrophic backtracking",
        )
    else:
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            logger.warning("snapadmin.masking.pattern_invalid", pattern=pattern, error=str(exc))
    _PATTERN_CACHE[pattern] = compiled
    return compiled


def _mask_string(s: str) -> str:
    if not s:
        return s
    if "@" in s:
        local, _, domain = s.partition("@")
        masked_local = (local[0] + "***") if local else "***"
        return f"{masked_local}@{domain}" if domain else f"{masked_local}@"
    if len(s) < 6:
        return "*" * len(s)
    head = tail = 2
    return s[:head] + "*" * (len(s) - head - tail) + s[-tail:]


def mask_value(value):
    """Obfuscate a single value, revealing just enough to stay recognisable.

    * ``None`` → returned unchanged.
    * ``str`` → emails become first char of the local part + ``***`` +
      ``@domain`` (e.g. ``a***@example.com``); strings under 6 chars are
      fully starred; longer strings keep a 2-char head/tail (e.g.
      ``+3********78``).
    * ``bool``, ``int``, ``float``, ``Decimal`` → the fixed sentinel
      ``"***"``, never coerced to a digit-revealing star pattern (``bool``
      is checked before ``int``, since it is a subclass of it).
    * ``list`` / ``dict`` → a new collection of the same shape, with
      ``mask_value`` applied recursively to elements / values (dict keys are
      left untouched).
    * Anything else → falls back to the string treatment above, applied to
      ``str(value)``.

    This is the default policy. A field with an entry in
    ``SNAPADMIN_MASKING_RULES`` goes through :func:`apply_masking_rule` instead.
    """
    if value is None:
        return value
    if isinstance(value, bool):
        return "***"
    if isinstance(value, (int, float, Decimal)):
        return "***"
    if isinstance(value, str):
        return _mask_string(value)
    if isinstance(value, list):
        return [mask_value(item) for item in value]
    if isinstance(value, dict):
        return {key: mask_value(item) for key, item in value.items()}
    return _mask_string(str(value))


def apply_masking_rule(value, rule: dict | None):
    """Obfuscate ``value`` with one configured rule from ``SNAPADMIN_MASKING_RULES``.

    * no rule, or a rule that is not a dict → the built-in :func:`mask_value`;
    * ``replacement`` alone → that constant, whatever the value was;
    * ``pattern`` (+ optional ``replacement``, default ``"*"``) →
      ``re.sub(pattern, replacement, str(value))``;
    * ``list`` / ``dict`` → the rule applied to each element / value.

    ``None`` is never masked (there is nothing to reveal), matching
    :func:`mask_value`. Every failure path — an unusable pattern, a replacement
    referencing a group the pattern does not have, a value too long to run a
    regex against safely — falls back to :func:`mask_value`, so a broken rule
    degrades to the default masking rather than to raw data.
    """
    if value is None:
        return None
    if not isinstance(rule, dict):
        return mask_value(value)
    if isinstance(value, list):
        return [apply_masking_rule(item, rule) for item in value]
    if isinstance(value, dict):
        return {key: apply_masking_rule(item, rule) for key, item in value.items()}

    pattern = rule.get("pattern")
    replacement = str(rule.get("replacement", "*"))
    if not pattern:
        return replacement if "replacement" in rule else mask_value(value)

    regex = _compiled(str(pattern))
    if regex is None:
        return mask_value(value)
    text = value if isinstance(value, str) else str(value)
    if len(text) > MAX_REGEX_INPUT:
        logger.warning(
            "snapadmin.masking.value_too_long_for_regex",
            pattern=str(pattern),
            length=len(text),
            limit=MAX_REGEX_INPUT,
        )
        return mask_value(value)
    try:
        return regex.sub(replacement, text)
    except re.error as exc:
        logger.warning(
            "snapadmin.masking.replacement_invalid",
            pattern=str(pattern),
            error=str(exc),
        )
        return mask_value(value)


def mask_field(
    app_label: str,
    model_name: str,
    field: str,
    value,
    user: UserLike = None,
):
    """Obfuscate ``value`` for one field, honouring its configured rule.

    The single choke point every masking surface goes through — the admin, the
    REST serializer, GraphQL, background exports and the audit trail — so a rule
    cannot apply on one of them and not another.

    It answers *how* to mask, not *whether* to: callers decide that with
    :func:`get_masked_fields`. Passing ``user`` additionally lets a rule's own
    ``permission`` hand that user the raw value for this field; omitted, the
    value is always masked.
    """
    if user is not None and user_can_view_pii(user, field, app_label=app_label, model_name=model_name):
        return value
    return apply_masking_rule(value, get_masking_rules(app_label, model_name).get(str(field)))


def mask_changes(
    app_label: str,
    model_name: str,
    changes: dict | None,
    user: UserLike = None,
) -> dict | None:
    """Mask configured PII fields within an audit-trail ``changes`` diff.

    ``changes`` has the shape written by :func:`snapadmin.audit.record_audit` —
    ``{field_name: {"old": ..., "new": ...}, ...}``. Only keys naming a masked
    field for this model are touched (both sides of the diff, each through
    :func:`mask_field`); everything else, including a falsy ``changes``, is
    returned unchanged. Pass ``user`` to honour a rule's per-field
    ``permission``; without one every masked field is masked.

    The single choke point used by the audit-log admin display, the per-object
    diff view and the ``snapadmin_audit_export`` command, so those surfaces can
    never drift out of sync on what counts as masked.
    """
    if not changes:
        return changes
    masked_names = set(get_masked_fields(app_label, model_name))
    if not masked_names:
        return changes
    return {
        field: (
            {side: mask_field(app_label, model_name, field, value, user) for side, value in diff.items()}
            if field in masked_names and isinstance(diff, dict)
            else diff
        )
        for field, diff in changes.items()
    }
