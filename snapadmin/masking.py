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

Who sees raw data is a permission decision, evaluated per request:

* superusers                          → always raw
* holders of ``snapadmin.view_raw_pii`` → raw
* everyone else                       → masked

The same rule drives the admin (list view + change form) and the API serializer,
so an external frontend consuming the API receives already-masked data.
"""

from decimal import Decimal

from django.conf import settings

#: The custom permission that unlocks raw PII. Declared on APIToken.Meta so it is
#: created for the ``snapadmin`` app; assign it to trusted staff groups.
PII_PERMISSION = "snapadmin.view_raw_pii"


def get_masked_fields(app_label: str, model_name: str) -> list[str]:
    """Return the masked field names configured for ``app_label.model_name``.

    Keys in ``SNAPADMIN_MASKED_FIELDS`` are matched case-insensitively on both
    the app label and the model name, so ``"demo.Customer"`` and
    ``"demo.customer"`` resolve identically.
    """
    raw = getattr(settings, "SNAPADMIN_MASKED_FIELDS", None) or {}
    wanted = f"{app_label}.{model_name}".lower()
    for key, fields in raw.items():
        if str(key).lower() == wanted:
            return list(fields or [])
    return []


def user_can_view_pii(user) -> bool:
    """Whether ``user`` is allowed to see unmasked PII.

    True only for authenticated, active superusers or holders of
    ``snapadmin.view_raw_pii``. Anonymous / inactive users always get masked.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if not user.is_active:
        return False
    if user.is_superuser:
        return True
    return user.has_perm(PII_PERMISSION)


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


def mask_changes(app_label: str, model_name: str, changes: dict | None) -> dict | None:
    """Mask configured PII fields within an audit-trail ``changes`` diff.

    ``changes`` has the shape written by :func:`snapadmin.audit.record_audit` —
    ``{field_name: {"old": ..., "new": ...}, ...}``. Only keys naming a field
    listed in ``SNAPADMIN_MASKED_FIELDS`` for this model are masked (both
    sides of the diff); everything else, including a falsy ``changes``, is
    returned unchanged. The single choke point used by both the audit-log
    admin display and the ``snapadmin_audit_export`` command, so the two
    surfaces can never drift out of sync on what counts as masked.
    """
    if not changes:
        return changes
    masked_names = set(get_masked_fields(app_label, model_name))
    if not masked_names:
        return changes
    return {
        field: (
            {side: mask_value(v) for side, v in diff.items()}
            if field in masked_names and isinstance(diff, dict)
            else diff
        )
        for field, diff in changes.items()
    }
