"""
snapadmin/nesting.py

Nest SnapAdmin sections into existing Django app groups (issues #4 / #16).

By default every app is its own top-level block on the admin index. On a busy
install that clutters the sidebar. These settings let you fold auto-generated
sections into existing apps, hide groups, or rename them — without writing a
custom ``AdminSite``::

    # Move the models of these source apps under an existing app's group:
    SNAPADMIN_NESTED_APPS = {"snapadmin": "auth"}

    # Drop these app groups from the index entirely:
    SNAPADMIN_HIDDEN_APPS = ["silk"]

    # Rename a group's heading:
    SNAPADMIN_APP_LABELS = {"auth": "Administration"}

The regrouping is applied by wrapping ``admin.site.get_app_list`` at startup
(see :class:`snapadmin.apps.SnapAdminConfig`), and only when at least one of the
settings above is non-empty — stock single-app installs are untouched.
"""

from django.conf import settings


def get_nested_apps() -> dict:
    """``{source_app_label: target_app_label}`` — models moved into the target."""
    return dict(getattr(settings, "SNAPADMIN_NESTED_APPS", None) or {})


def get_hidden_apps() -> set:
    """App labels to omit from the admin index."""
    return set(getattr(settings, "SNAPADMIN_HIDDEN_APPS", None) or [])


def get_app_label_overrides() -> dict:
    """``{app_label: "Display Name"}`` — rename a group's heading."""
    return dict(getattr(settings, "SNAPADMIN_APP_LABELS", None) or {})


def nesting_configured() -> bool:
    """True when any nesting/hide/rename setting is active."""
    return bool(get_nested_apps() or get_hidden_apps() or get_app_label_overrides())


def apply_nested_apps(app_list: list) -> list:
    """Regroup an admin ``app_list`` per the nesting settings.

    ``app_list`` is Django's index structure: a list of
    ``{"app_label", "name", "models", …}`` dicts. Returns a new list with:

    * models of each ``SNAPADMIN_NESTED_APPS`` source folded into the target
      app's model list (source removed once emptied); a move whose target is
      not present in the list is left untouched;
    * ``SNAPADMIN_HIDDEN_APPS`` groups removed;
    * ``SNAPADMIN_APP_LABELS`` headings renamed.

    Order is preserved: targets keep their original position.
    """
    nested = get_nested_apps()
    hidden = get_hidden_apps()
    renames = get_app_label_overrides()

    by_label = {app["app_label"]: app for app in app_list}
    removed_labels = set()

    # Fold source apps into their targets.
    for source, target in nested.items():
        src = by_label.get(source)
        dst = by_label.get(target)
        if src is None or dst is None or source == target:
            # Nothing to move, or target not visible — leave the source alone.
            continue
        dst.setdefault("models", []).extend(src.get("models", []))
        removed_labels.add(source)

    result = []
    for app in app_list:
        label = app["app_label"]
        if label in removed_labels or label in hidden:
            continue
        if label in renames:
            app = {**app, "name": renames[label]}
        result.append(app)
    return result
