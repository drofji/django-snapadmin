"""
snapadmin/masking.py

PII data masking (issue #12).

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


def mask_value(value):
    """Obfuscate a single value, revealing just enough to stay recognisable.

    * ``None`` / empty → returned unchanged.
    * Emails → first char of the local part + ``***`` + ``@domain`` (e.g.
      ``a***@example.com``).
    * Everything else → head + ``*`` middle + tail (e.g. ``+3********78``);
      strings of 1–2 chars are fully starred.
    """
    if value is None:
        return value
    s = str(value)
    if not s:
        return s
    if "@" in s:
        local, _, domain = s.partition("@")
        masked_local = (local[0] + "***") if local else "***"
        return f"{masked_local}@{domain}" if domain else f"{masked_local}@"
    if len(s) <= 2:
        return "*" * len(s)
    head = tail = 2 if len(s) >= 6 else 1
    return s[:head] + "*" * (len(s) - head - tail) + s[-tail:]
