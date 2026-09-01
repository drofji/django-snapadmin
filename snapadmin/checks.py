"""
snapadmin/checks.py

Startup configuration checks (onboarding / drop-in DX).

SnapAdmin is settings-driven, so a typo in a setting used to fail silently or
deep inside a request. These Django system checks (run on ``manage.py check``,
``runserver``, ``migrate``, and in CI) surface misconfiguration early with an
actionable hint. Everything here is advisory: a warning never blocks boot, and
each check is a no-op when its feature is unconfigured.
"""

import re
from datetime import timedelta
from urllib.parse import urlparse

from django.apps import apps
from django.conf import settings
from django.core.checks import Error, Info, Warning

from snapadmin import conf
from snapadmin.conf import get_setting
from snapadmin.registry import get_model_meta, is_registered


def _resolve_model(dotted: str):
    """``"app.Model"`` → model class, or ``None`` if unresolvable."""
    try:
        app_label, model_name = str(dotted).split(".", 1)
    except ValueError:
        return None
    try:
        return apps.get_model(app_label, model_name)
    except LookupError:
        return None


def check_analytics_db_alias(app_configs, **kwargs):
    alias = get_setting("SNAPADMIN_ANALYTICS_DB_ALIAS", "") or ""
    if alias and alias not in settings.DATABASES:
        return [Warning(
            f"SNAPADMIN_ANALYTICS_DB_ALIAS = {alias!r} is not a configured DATABASES alias.",
            hint="Read-replica routing will be ignored (queries stay on 'default'). "
                 "Add the alias to DATABASES or clear the setting.",
            id="snapadmin.W001",
        )]
    return []


def check_masked_fields(app_configs, **kwargs):
    errors = []
    masked = get_setting("SNAPADMIN_MASKED_FIELDS", None) or {}
    for key, fields in masked.items():
        model = _resolve_model(key)
        if model is None:
            errors.append(Error(
                f"SNAPADMIN_MASKED_FIELDS key {key!r} does not resolve to an installed model.",
                hint="Use 'app_label.ModelName', e.g. 'demo.Customer'.",
                id="snapadmin.E001",
            ))
            continue
        model_fields = {f.name for f in model._meta.get_fields()}
        for field in fields or []:
            if field not in model_fields:
                errors.append(Error(
                    f"SNAPADMIN_MASKED_FIELDS[{key!r}] lists unknown field {field!r}.",
                    hint=f"{key} has no field '{field}'. Check the spelling.",
                    id="snapadmin.E002",
                ))
    return errors


def check_masking_rules(app_configs, **kwargs):
    """Validate ``SNAPADMIN_MASKING_RULES`` — a typo here silently unmasks.

    Unlike most misconfiguration, a rule pointing at a model or field that does
    not exist fails *open*: nothing is masked and nothing says so. These are
    errors, not warnings, for the same reason ``SNAPADMIN_MASKED_FIELDS`` gets
    E001/E002.
    """
    from snapadmin.masking import _has_nested_quantifier

    errors = []
    rules = get_setting("SNAPADMIN_MASKING_RULES", None) or {}
    for key, fields in rules.items():
        model = _resolve_model(key)
        if model is None:
            errors.append(Error(
                f"SNAPADMIN_MASKING_RULES key {key!r} does not resolve to an installed model.",
                hint="Use 'app_label.ModelName', e.g. 'demo.Customer'. Nothing is masked "
                     "for a key that does not resolve.",
                id="snapadmin.E003",
            ))
            continue
        model_fields = {f.name for f in model._meta.get_fields()}
        for field, rule in (fields or {}).items():
            if field not in model_fields:
                errors.append(Error(
                    f"SNAPADMIN_MASKING_RULES[{key!r}] names unknown field {field!r}.",
                    hint=f"{key} has no field '{field}'. Check the spelling.",
                    id="snapadmin.E004",
                ))
                continue
            if not isinstance(rule, dict):
                errors.append(Error(
                    f"SNAPADMIN_MASKING_RULES[{key!r}][{field!r}] is not a dict.",
                    hint="A rule is a dict with any of 'pattern', 'replacement' and "
                         "'permission'. This one is ignored, and the field falls back to "
                         "the built-in masker.",
                    id="snapadmin.E005",
                ))
                continue
            pattern = rule.get("pattern")
            if not pattern:
                continue
            if _has_nested_quantifier(str(pattern)):
                errors.append(Error(
                    f"SNAPADMIN_MASKING_RULES[{key!r}][{field!r}] pattern {pattern!r} "
                    f"quantifies a group that is itself quantified.",
                    hint="That shape can backtrack catastrophically, so it is refused at "
                         "runtime and the field falls back to the built-in masker. Rewrite "
                         "it without the nested quantifier (escape a literal '{' as '\\{').",
                    id="snapadmin.E005",
                ))
                continue
            try:
                re.compile(str(pattern))
            except re.error as exc:
                errors.append(Error(
                    f"SNAPADMIN_MASKING_RULES[{key!r}][{field!r}] pattern {pattern!r} "
                    f"is not a valid regex: {exc}.",
                    hint="The field falls back to the built-in masker until this compiles.",
                    id="snapadmin.E005",
                ))
    return errors


def check_nested_apps(app_configs, **kwargs):
    warnings = []
    installed = {c.label for c in apps.get_app_configs()}
    nested = get_setting("SNAPADMIN_NESTED_APPS", None) or {}
    for source, target in nested.items():
        if target not in installed:
            warnings.append(Warning(
                f"SNAPADMIN_NESTED_APPS maps {source!r} → {target!r}, but no app "
                f"labelled {target!r} is installed.",
                hint="The models will stay under their own group until the target app exists.",
                id="snapadmin.W002",
            ))
    return warnings


def check_sso_providers(app_configs, **kwargs):
    warnings = []
    providers = get_setting("SNAPADMIN_SSO_PROVIDERS", None) or {}
    allowed_hosts = {
        host.lower() for host in (get_setting("SNAPADMIN_SSO_ALLOWED_HOSTS", None) or [])
    }
    for key, meta in providers.items():
        if not isinstance(meta, dict) or not (meta.get("url") or "").strip():
            warnings.append(Warning(
                f"SNAPADMIN_SSO_PROVIDERS[{key!r}] has no usable 'url' and will not render.",
                hint="Each provider needs a dict with a non-empty 'url', e.g. "
                     "{'label': '…', 'url': '/accounts/azure/login/'}.",
                id="snapadmin.W003",
            ))
            continue
        url = meta["url"].strip()
        netloc = urlparse(url).netloc
        if url.startswith("//"):
            warnings.append(Warning(
                f"SNAPADMIN_SSO_PROVIDERS[{key!r}]['url'] = {url!r} is protocol-relative.",
                hint="A protocol-relative URL (starting with '//') resolves to an external "
                     "origin and will not render. Use a site-relative path ('/accounts/…') "
                     "or a full absolute URL ('https://…').",
                id="snapadmin.W005",
            ))
        elif allowed_hosts and netloc and netloc.lower() not in allowed_hosts:
            warnings.append(Warning(
                f"SNAPADMIN_SSO_PROVIDERS[{key!r}]['url'] host {netloc!r} is not in "
                f"SNAPADMIN_SSO_ALLOWED_HOSTS and will not render.",
                hint="Add the host to SNAPADMIN_SSO_ALLOWED_HOSTS or point the provider "
                     "at an allowed identity provider.",
                id="snapadmin.W005",
            ))
    return warnings


def check_nesting_active_site(app_configs, **kwargs):
    """Warn when nesting settings are configured but another AdminSite is in play.

    ``install_nested_apps()`` (``snapadmin/apps.py``) only patches
    ``django.contrib.admin.site`` — the default ``AdminSite`` singleton. Reliably
    telling *which* ``AdminSite`` actually serves ``/admin/`` isn't possible from
    ``AppConfig.ready()`` (URLconf isn't guaranteed loaded yet, and app ready()
    order isn't guaranteed either), so this check runs later instead: by the time
    ``manage.py check`` runs, every ``AdminSite`` a project has instantiated and
    registered models on is discoverable via ``django.contrib.admin.sites.all_sites``.
    If one of those isn't the default site, nesting settings applied to the
    default site may never reach the index the user actually sees.
    """
    from snapadmin.nesting import nesting_configured

    if not nesting_configured():
        return []

    from django.contrib.admin.sites import all_sites, site as default_site

    other_sites = sorted(
        getattr(s, "name", repr(s))
        for s in all_sites
        if s is not default_site and getattr(s, "_registry", None)
    )
    if not other_sites:
        return []
    return [Warning(
        "SNAPADMIN_NESTED_APPS / SNAPADMIN_HIDDEN_APPS / SNAPADMIN_APP_LABELS are "
        "configured, but at least one AdminSite other than the default "
        f"django.contrib.admin.site also has models registered on it: {', '.join(other_sites)}.",
        hint="SnapAdmin only patches the default site's get_app_list. If that other "
             "site is the one serving /admin/, these settings are silently ignored there. "
             "Register your models on django.contrib.admin.site instead, or apply "
             "snapadmin.nesting.apply_nested_apps to your custom site's get_app_list yourself.",
        id="snapadmin.W006",
    )]


#: Model labels listed inline in a grouped warning before it truncates.
MODEL_LIST_CAP = 20


def _format_labels(labels: list[str]) -> str:
    """Render model labels as one compact, comma-separated clause.

    These checks are per-model but their advice is not: repeating an identical
    multi-line block (message *and* hint) once per model buried the actual signal
    under a wall of text on every ``manage.py`` run — 11 demo models produced 11
    copies. One warning listing the models reads in a glance and stays greppable.
    Very large projects would still overflow a terminal, so the list truncates.
    """
    if len(labels) <= MODEL_LIST_CAP:
        return ", ".join(labels)
    shown = ", ".join(labels[:MODEL_LIST_CAP])
    return f"{shown} (+{len(labels) - MODEL_LIST_CAP} more)"


def _api_writable_models():
    """SnapModels whose auto-generated API still accepts writes.

    A model served read-only (``api_read_only``) or with an explicit
    ``api_http_method_names`` allowlist that excludes every write verb has no
    mass-assignment surface, so the write-field checks skip it.
    """
    write_verbs = {"post", "put", "patch"}
    for model in apps.get_models():
        if not is_registered(model):
            continue
        if get_model_meta(model, "api_read_only", False):
            continue
        methods = get_model_meta(model, "api_http_method_names", None)
        if methods is not None and not write_verbs.intersection(m.lower() for m in methods):
            continue
        yield model


def check_api_write_fields(app_configs, **kwargs):
    if not get_setting("SNAPADMIN_REST_API_ENABLED", True):
        return []
    unguarded = sorted(
        model._meta.label
        for model in _api_writable_models()
        if get_model_meta(model, "api_write_fields", None) is None
    )
    if not unguarded:
        return []
    return [Warning(
        f"{len(unguarded)} model(s) have no api_write_fields set — on these, every field "
        "not listed in api_exclude_fields is writable through the auto-generated API "
        f"(create/update): {_format_labels(unguarded)}.",
        hint="Set api_write_fields = [...] on the model to restrict which "
             "fields accept client-supplied values (a mass-assignment guard). "
             "Leave unset only for models where every field is safe to write.",
        id="snapadmin.W004",
    )]


def check_api_read_only(app_configs, **kwargs):
    """Warn: a model whose fields are all read-only but whose writes still reach the API.

    ``api_write_fields = []`` makes every field read-only, so a REST create inserts a
    blank row (all defaults) and an update is a silent no-op — a confusing surface.
    Such a model almost always wants ``api_read_only = True`` (a clean 405 on writes)
    instead. Quiet once the model sets ``api_read_only`` or an explicit
    ``api_http_method_names`` policy, so the tradeoff is a deliberate choice.
    """
    if not get_setting("SNAPADMIN_REST_API_ENABLED", True):
        return []
    inert = sorted(
        model._meta.label
        for model in apps.get_models()
        if is_registered(model)
        and get_model_meta(model, "api_write_fields", None) == []
        and not get_model_meta(model, "api_read_only", False)
        and get_model_meta(model, "api_http_method_names", None) is None
    )
    if not inert:
        return []
    return [Warning(
        f"{len(inert)} model(s) set api_write_fields = [] (no field is writable) but "
        "still expose create/update/delete through the API — a REST create inserts a "
        "blank row and an update is a silent no-op, rather than a clean 405: "
        f"{_format_labels(inert)}.",
        hint="Set api_read_only = True to serve these models read-only "
             "(list/retrieve/count/export) and answer 405 to POST/PUT/PATCH/DELETE, "
             "or set api_http_method_names to an explicit allowlist.",
        id="snapadmin.W007",
    )]


def check_unfold_theme(app_configs, **kwargs):
    """Info: surface the stock-admin fallback so it is never silent.

    ``django-unfold`` is an optional theme (``pip install django-snapadmin[theme]``).
    SnapAdmin resolves its admin base class lazily — it uses Unfold's themed
    ``ModelAdmin``/widgets when the package is installed *and* ``'unfold'`` is in
    ``INSTALLED_APPS``, and otherwise renders on Django's built-in admin. When that
    fallback is active this emits one informational message (never an error, never
    blocks boot) so an operator who expected the themed UI can see why it isn't there.
    """
    from snapadmin.admin import UNFOLD_INSTALLED

    if UNFOLD_INSTALLED:
        return []
    return [Info(
        "SnapAdmin is running on Django's built-in admin theme — the optional "
        "django-unfold theme is not active.",
        hint="This is fully supported. For the themed UI, install the theme extra "
             "(pip install django-snapadmin[theme]) and add 'unfold', "
             "'unfold.contrib.filters', 'unfold.contrib.forms' and "
             "'unfold.contrib.inlines' to INSTALLED_APPS before 'django.contrib.admin'.",
        id="snapadmin.I001",
    )]


def check_backup_age_recipients(app_configs, **kwargs):
    """Warn about a recipient string that cannot possibly be a valid age/SSH key.

    Deliberately advisory, not an error: a typo'd recipient among several
    still leaves the others working, and backups must not fail to *run* over
    a config mistake. Deliberately does not import pyrage or shell out to
    `age` (see `crypto.looks_like_recipient`) — a project may intend to use
    only one of the two backends, and this check must not force either.
    """
    from snapadmin.crypto import looks_like_recipient

    recipients = get_setting("SNAPADMIN_BACKUP_AGE_RECIPIENTS", None) or []
    bad = [value for value in recipients if not looks_like_recipient(str(value))]
    if not bad:
        return []
    return [Warning(
        f"SNAPADMIN_BACKUP_AGE_RECIPIENTS contains {len(bad)} entr{'y' if len(bad) == 1 else 'ies'} "
        f"that do not look like an age or SSH public key: {_format_labels([repr(v) for v in bad])}.",
        hint="Each entry must be an age public key ('age1…') or an SSH public key "
             "('ssh-ed25519 …' / 'ssh-rsa …'). A malformed entry will fail encryption "
             "the next time a backup runs rather than being silently skipped.",
        id="snapadmin.W008",
    )]


def check_backup_env_requires_encryption(app_configs, **kwargs):
    """Error: ``env`` in ``SNAPADMIN_BACKUP_INCLUDE`` with no AGE recipients configured.

    A ``.env`` file holds ``SECRET_KEY``, DB passwords, S3 keys — anything else
    a backup ships is expendable if lost or read by the wrong person; this is
    not. Fail closed at startup rather than the first time a scheduled backup
    happens to include ``env`` and ships a plaintext secrets file to whatever
    destination is configured. :func:`snapadmin.backup.build_backup_bundle`
    repeats this exact guard at runtime (:class:`~snapadmin.backup.BackupError`)
    for the case where recipients were configured, then cleared, without a
    restart — this check alone cannot catch that.
    """
    include = get_setting("SNAPADMIN_BACKUP_INCLUDE", ["db"]) or []
    if "env" not in include:
        return []
    recipients = get_setting("SNAPADMIN_BACKUP_AGE_RECIPIENTS", None) or []
    if recipients:
        return []
    return [Error(
        "SNAPADMIN_BACKUP_INCLUDE includes 'env' but SNAPADMIN_BACKUP_AGE_RECIPIENTS "
        "is empty — a backup would ship your .env file's secrets (SECRET_KEY, DB "
        "password, …) to the backup destination in plain text.",
        hint="Set SNAPADMIN_BACKUP_AGE_RECIPIENTS to at least one age or SSH public "
             "key, or remove 'env' from SNAPADMIN_BACKUP_INCLUDE.",
        id="snapadmin.E007",
    )]


#: Days simulated forward when timing a crontab's period — far enough for any
#: schedule saner than "once a year" (the worst realistic beat entry), bounded
#: so a pathological one degrades to "cannot determine" rather than a slow
#: system check on every ``manage.py`` invocation.
_CRONTAB_SIMULATION_DAYS = 400


def _crontab_period_hours(schedule) -> float | None:
    """Time a ``celery.schedules.crontab``'s period by direct simulation over
    its declared field sets (``minute``/``hour``/``day_of_week``/
    ``day_of_month``/``month_of_year`` — plain ``set[int]`` attributes).

    Deliberately does **not** call the schedule's own ``remaining_estimate()``
    / ``is_due()`` — both anchor their result against the real wall-clock
    time the call happens to run at (verified by reading
    ``celery.schedules.crontab.remaining_delta``'s source: it calls
    ``self.now()`` internally), which makes them unusable for "how far apart
    are two consecutive fires", a question that must not depend on when it is
    asked. Walking from a fixed, arbitrary reference date instead — 2024-01-01
    is a Monday, matching this function's own day-of-week arithmetic — makes
    the result a pure function of the schedule.
    """
    from datetime import date, datetime as dt

    # No try/except here: the caller (_schedule_period_hours) only reaches
    # this function after isinstance(schedule, crontab), and every real
    # crontab instance sets all five fields in __init__ — there is no path
    # through which one could be missing.
    month_of_year = schedule.month_of_year
    day_of_month = schedule.day_of_month
    day_of_week = schedule.day_of_week
    hour = schedule.hour
    minute = schedule.minute

    occurrences: list[dt] = []
    day = date(2024, 1, 1)
    for _ in range(_CRONTAB_SIMULATION_DAYS):
        # Celery's own convention (crontab.remaining_delta): Sunday is 0.
        if (
            day.month in month_of_year
            and day.day in day_of_month
            and (day.isoweekday() % 7) in day_of_week
        ):
            for h in sorted(hour):
                for m in sorted(minute):
                    occurrences.append(dt(day.year, day.month, day.day, h, m))
                    if len(occurrences) == 2:
                        return (occurrences[1] - occurrences[0]).total_seconds() / 3600
        day += timedelta(days=1)
    return None


def _schedule_period_hours(schedule) -> float | None:
    """Best-effort: how often a ``CELERY_BEAT_SCHEDULE`` entry's ``schedule``
    value fires, in hours. ``None`` when it cannot be determined (an unknown
    schedule type, or a pathological one whose period could not be timed
    within :data:`_CRONTAB_SIMULATION_DAYS`) — the caller must treat that as
    "nothing to warn about", never as a 0-hour period, since a false W010 on
    an unrecognised schedule shape would be worse than staying silent.
    """
    if isinstance(schedule, timedelta):
        return schedule.total_seconds() / 3600
    try:
        from celery.schedules import crontab
    except ImportError:  # pragma: no cover - celery is the [celery] extra
        return None
    if not isinstance(schedule, crontab):
        return None
    return _crontab_period_hours(schedule)


def check_backup_schedule_cadence(app_configs, **kwargs):
    """Warn when Celery Beat checks ``snapadmin.run_db_backups`` less often
    than the shortest configured ``SNAPADMIN_BACKUP_*_EVERY_HOURS`` among the
    active destinations.

    A destination's own interval only ever gets checked when Beat wakes the
    task up — see ``snapadmin/backup.py``'s module docstring and
    ``_is_due()``'s grace margin. A daily destination behind a weekly beat
    entry silently loses six days out of every seven; this check exists so
    that combination is caught at ``manage.py check`` instead of discovered
    the day someone needs a backup that was never taken.
    """
    from snapadmin.backup import _active_destinations, get_backup_config

    config = get_backup_config()
    if not config.enabled:
        return []

    beat_schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", None) or {}
    entry = next(
        (info for info in beat_schedule.values() if info.get("task") == "snapadmin.run_db_backups"),
        None,
    )
    if entry is None:
        return []

    beat_hours = _schedule_period_hours(entry.get("schedule"))
    if beat_hours is None:
        return []

    intervals = {
        "local": config.local_every_hours,
        "network": config.network_every_hours,
        "remote": config.remote_every_hours,
        "sftp": config.sftp_every_hours,
    }
    shortest = min(intervals[dest] for dest in _active_destinations(config))
    if beat_hours <= shortest:
        return []
    return [Warning(
        f"The 'snapadmin.run_db_backups' Celery Beat entry runs about every "
        f"{beat_hours:.1f}h, less often than the shortest configured backup "
        f"interval ({shortest}h).",
        hint="A destination's own *_EVERY_HOURS check only runs when Beat wakes "
             "the task up, so this combination silently drops days. Schedule "
             "'snapadmin.run_db_backups' at least as often as your shortest "
             "SNAPADMIN_BACKUP_*_EVERY_HOURS setting, or raise that setting to "
             "match the Beat cadence.",
        id="snapadmin.W010",
    )]


def check_backup_s3_configuration(app_configs, **kwargs):
    """Warn about an S3 destination that is configured but incompletely.

    Deliberately advisory, like every other check here: a half-configured S3
    destination should not stop an otherwise-working project from starting,
    just as a malformed AGE recipient (``snapadmin.W008``) does not. No-op
    when ``SNAPADMIN_BACKUP_S3_BUCKET`` is unset — the destination is simply
    off.
    """
    from snapadmin.backup import s3_ambient_credentials_likely

    bucket = get_setting("SNAPADMIN_BACKUP_S3_BUCKET", "") or ""
    if not bucket:
        return []

    warnings = []
    endpoint_url = get_setting("SNAPADMIN_BACKUP_S3_ENDPOINT_URL", "") or ""
    if endpoint_url and not endpoint_url.startswith(("http://", "https://")):
        warnings.append(Warning(
            f"SNAPADMIN_BACKUP_S3_ENDPOINT_URL = {endpoint_url!r} does not look like a URL.",
            hint="Set it to a full endpoint URL, e.g. "
                 "'https://s3.eu-central-1.wasabisys.com', or leave it unset to use "
                 "AWS's own default endpoint.",
            id="snapadmin.W011",
        ))

    access_key = get_setting("SNAPADMIN_BACKUP_S3_ACCESS_KEY_ID", "") or ""
    secret_key = get_setting("SNAPADMIN_BACKUP_S3_SECRET_ACCESS_KEY", "") or ""
    if not access_key and not secret_key and not s3_ambient_credentials_likely():
        warnings.append(Warning(
            "SNAPADMIN_BACKUP_S3_BUCKET is set, but no SNAPADMIN_BACKUP_S3_ACCESS_KEY_ID / "
            "_SECRET_ACCESS_KEY is configured and no ambient AWS credential source "
            "(environment variables, a shared credentials file, an ECS/IRSA role) was "
            "detected — S3 backups will silently never upload.",
            hint="Set SNAPADMIN_BACKUP_S3_ACCESS_KEY_ID/_SECRET_ACCESS_KEY, or ignore this "
                 "warning on a host that authenticates purely through an EC2 instance "
                 "profile — that case cannot be detected without a network call, so this "
                 "check does not attempt it.",
            id="snapadmin.W011",
        ))
    return warnings


def check_snapadmin_profile(app_configs, **kwargs):
    """Error: ``SNAPADMIN_PROFILE`` set to a name the package does not know.

    :func:`snapadmin.conf.get_setting` also refuses this at the first setting
    it resolves after boot (fail closed, not a silent fall-through) — but by
    then the failure is a raised exception deep in whatever imported first.
    This check exists so the same misconfiguration is caught cleanly by
    ``manage.py check`` too, with the valid choices named up front.
    """
    profile = getattr(settings, "SNAPADMIN_PROFILE", None)
    if profile is None or profile in conf.PROFILES:
        return []
    return [Error(
        f"SNAPADMIN_PROFILE = {profile!r} is not a recognised profile.",
        hint=f"Choose one of {', '.join(conf.PROFILES)}, or unset it to keep "
             "today's defaults (equivalent to 'full').",
        id="snapadmin.E006",
    )]


def check_snapadmin_profile_contradiction(app_configs, **kwargs):
    """Warn when an explicit setting silently overrides what the active profile implies.

    Explicit always wins over a profile (documented, deliberate) — but a
    project that opted into ``SNAPADMIN_PROFILE = "admin"`` specifically to
    turn REST/GraphQL off, then left an old ``SNAPADMIN_REST_API_ENABLED =
    True`` from before it adopted profiles, ends up with REST on anyway and
    no signal that the profile's choice was overridden. This is advisory,
    not an error: overriding a profile on purpose is a normal, supported thing
    to do.
    """
    profile = getattr(settings, "SNAPADMIN_PROFILE", None)
    if profile is None or profile not in conf.PROFILES:
        return []
    warnings = []
    for name, preset_value in sorted(conf._PRESETS[profile].items()):
        if hasattr(settings, name) and getattr(settings, name) != preset_value:
            warnings.append(Warning(
                f"{name} = {getattr(settings, name)!r} is set explicitly, overriding "
                f"what SNAPADMIN_PROFILE = {profile!r} would otherwise set it to "
                f"({preset_value!r}).",
                hint="The explicit setting wins — this is allowed and sometimes "
                     "intentional. If it is not, remove the explicit setting to let "
                     "the profile take effect.",
                id="snapadmin.W009",
            ))
    return warnings


def check_retention_purge_scheduled(app_configs, **kwargs):
    """Warn: retention is configured somewhere, but nothing schedules the purge.

    ``SnapModel.purge_expired`` / ``SnapadminAuditLog.purge_expired`` /
    ``purge_expired_export_jobs`` only ever run when something calls them —
    the ``snapadmin.purge_expired_data`` Celery Beat entry, or an operator
    invoking ``manage.py snapadmin_purge_expired_data`` from an external cron.
    Retention is on the books the moment any of the three settings below is
    configured (the audit log always is, by its own 365-day default), so a
    project that never wires up either trigger has a table it believes is
    bounded quietly growing forever — the exact state every #RET2 report was
    actually in. This can only see ``CELERY_BEAT_SCHEDULE`` as Django settings
    define it; a cron entry calling the management command directly is
    invisible here and does not need to trip this warning.
    """
    from snapadmin.models import SnapadminAuditLog

    configured = (
        SnapadminAuditLog.data_retention_days() > 0
        or bool(get_setting("SNAPADMIN_EXPORT_RETENTION_DAYS", None))
        or any(
            (get_model_meta(model, "data_retention_days", None) or 0) > 0
            for model in apps.get_models()
            if is_registered(model)
        )
    )
    if not configured:
        return []

    beat_schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", None) or {}
    scheduled_tasks = {
        entry.get("task") for entry in beat_schedule.values() if isinstance(entry, dict)
    }
    if "snapadmin.purge_expired_data" in scheduled_tasks:
        return []

    return [Warning(
        "Retention is configured (a model's data_retention_days, "
        "SNAPADMIN_AUDIT_RETENTION_DAYS or SNAPADMIN_EXPORT_RETENTION_DAYS), "
        "but no CELERY_BEAT_SCHEDULE entry runs snapadmin.purge_expired_data — "
        "the tables it names will keep growing until something calls it.",
        hint="Add a CELERY_BEAT_SCHEDULE entry for the 'snapadmin.purge_expired_data' "
             "task (see docs/index.html#gdpr), or run "
             "'manage.py snapadmin_purge_expired_data' from an external cron instead.",
        id="snapadmin.W012",
    )]


def check_snap_action_read_only_conflict(app_configs, **kwargs):
    """Error: a ``@snap_action`` declares methods the model's own CRUD policy already blocks.

    ``dispatch_action()`` measures every action's HTTP methods against the
    exact same ``api_read_only``/``api_http_method_names`` policy a regular
    verb is measured against (#RFC1h) — an action whose methods are not a
    subset of that resolved set always answers ``403`` and can never actually
    run. That is dead, misleading configuration: the action exists in code
    and is discoverable via ``GET /api/models/schema/``, yet nothing can ever
    reach it. Caught at boot instead of at first request.
    """
    if not get_setting("SNAPADMIN_REST_API_ENABLED", True):
        return []
    try:
        from snapadmin.api.views import _SAFE_HTTP_METHOD_NAMES, iter_snap_actions
    except ImportError:
        # The REST API is enabled but its dependencies are missing — urls.py
        # already raises a pointed ImproperlyConfigured for that; this check
        # has nothing further to add.
        return []

    errors = []
    for model in apps.get_models():
        if not is_registered(model):
            continue
        explicit = get_model_meta(model, "api_http_method_names", None)
        if explicit is not None:
            allowed = {str(name).lower() for name in explicit} | {"head", "options"}
        elif get_model_meta(model, "api_read_only", False):
            allowed = set(_SAFE_HTTP_METHOD_NAMES)
        else:
            continue  # full CRUD — no action's methods can conflict
        for spec in iter_snap_actions(model):
            blocked = sorted(spec.methods - allowed)
            if not blocked:
                continue
            errors.append(Error(
                f"{model._meta.label}'s @snap_action {spec.name!r} declares method(s) "
                f"{blocked} that the model's own api_read_only/api_http_method_names "
                f"policy already blocks (only {sorted(allowed)} allowed) — this action "
                "can never be reached and always answers 403.",
                hint="Widen the model's api_http_method_names, drop the conflicting "
                     "method(s) from the action, or turn off api_read_only if the "
                     "model is meant to accept this action.",
                id="snapadmin.E008",
            ))
    return errors


#: Sentinel for "never declared subject_path at all" — distinct from an
#: explicit ``None`` ("this model carries nothing subject-scoped"), the same
#: trick ``get_model_meta``'s own ``default`` argument is built on.
_SUBJECT_PATH_UNDECLARED = object()


def check_subject_paths(app_configs, **kwargs):
    """GDPR subject-access declaration (#FUT4a/#FUT4b) — loud, not silent, omission.

    Every registered SnapAdmin model must declare ``subject_path`` — a forward
    ORM lookup path to the field carrying the value that identifies a GDPR data
    subject, or ``None`` if the model carries nothing subject-scoped. A model
    that never sets it at all is indistinguishable, from the outside, from one
    whose author considered the question and answered "nothing here" — exactly
    the silence the ``manage.py snapadmin_subject_request`` export/deletion
    command cannot safely assume its way around, since it is a legally-binding
    export. ``snapadmin.E011`` is that omission.

    ``snapadmin.E012`` groups every way a *declared* path can still be wrong —
    mirroring the house style ``check_masking_rules`` already sets (E003 for an
    unresolvable key, E004/E005 for distinct problems within one setting):

    * ``is_data_subject=True`` with no ``subject_identifier``, or with
      ``subject_path != subject_identifier`` — a subject model must reach
      *itself* by exactly its own identifying field, zero hops, never a
      different path (almost always a copy-paste mistake, and silently
      accepting one would defeat the whole point of requiring both).
    * a path over the 3-relation-hop cap — a deliberate ceiling, not a
      measured ceiling: past it is a schema shape worth a person looking at,
      not a silent multi-hop join running inside a legally-binding export.
    * a path that does not resolve via this model's own **forward**
      relations (``ForeignKey``/``OneToOneField`` only — never a reverse
      accessor or a many-to-many, since the path lives on the model that
      *has* the data, not the model being reached) to a real terminal field.
    * an ``ES_ONLY`` model declaring a multi-hop path — confirmed against
      ``EsQuerySet.filter()`` itself, which only ever matches flat
      ``field=value`` (nothing splits on ``__`` there), so a multi-hop path on
      an ``ES_ONLY`` model would silently match nothing rather than erroring,
      the one shape that must be caught here instead of discovered against a
      live index during an actual export run.

    The honest limit this whole mechanism sits on: it only ever walks
    ``apps.get_models()`` filtered by :func:`snapadmin.registry.is_registered`
    — a Django model nobody ever wrapped in ``SnapModel``/``@snap_model`` is
    invisible here before the question is even asked. State that next to the
    subject-request command's own honesty limits, not just here.
    """
    from django.core.exceptions import FieldDoesNotExist
    from django.db import models as django_models

    from snapadmin.models import EsStorageMode

    errors = []
    for model in apps.get_models():
        if not is_registered(model):
            continue

        label = model._meta.label
        path = get_model_meta(model, "subject_path", _SUBJECT_PATH_UNDECLARED)

        if path is _SUBJECT_PATH_UNDECLARED:
            errors.append(Error(
                f"{label} is a registered SnapAdmin model but never declares "
                "subject_path (or None) — a GDPR subject-access export/deletion "
                "cannot know whether this model carries personal data reachable "
                "from a subject.",
                hint="Set subject_path to a forward ORM lookup path reaching the "
                     "subject's identifying field (e.g. 'customer__email'), or "
                     f"subject_path = None if {label} carries nothing subject-scoped.",
                id="snapadmin.E011",
            ))
            continue
        if path is None:
            continue

        is_subject = bool(get_model_meta(model, "is_data_subject", False))
        identifier = get_model_meta(model, "subject_identifier", None)

        if is_subject:
            if not identifier:
                errors.append(Error(
                    f"{label} sets is_data_subject=True but declares no "
                    "subject_identifier.",
                    hint="Set subject_identifier to the field name on this model "
                         "holding the raw identifier value, e.g. 'email'.",
                    id="snapadmin.E012",
                ))
                continue
            if path != identifier:
                errors.append(Error(
                    f"{label} is a subject model (is_data_subject=True) whose "
                    f"subject_path ({path!r}) does not equal its own "
                    f"subject_identifier ({identifier!r}).",
                    hint="A subject model must reach itself by exactly its own "
                         f"identifying field: set subject_path = {identifier!r}.",
                    id="snapadmin.E012",
                ))
                continue

        if not isinstance(path, str) or not path:
            errors.append(Error(
                f"{label}.subject_path = {path!r} is not a non-empty string.",
                hint="subject_path must be a '__'-joined ORM lookup path string, "
                     "or None.",
                id="snapadmin.E012",
            ))
            continue

        segments = path.split("__")
        hops = segments[:-1]
        if len(hops) > 3:
            errors.append(Error(
                f"{label}.subject_path = {path!r} is {len(hops)} relation hops "
                "deep — over the 3-hop cap.",
                hint="Shorten the path, or reconsider the design — a path this "
                     "deep is worth a person looking at, not a silent multi-hop "
                     "join inside a legally-binding export.",
                id="snapadmin.E012",
            ))
            continue

        current = model
        resolvable = True
        for segment in hops:
            try:
                field = current._meta.get_field(segment)
            except FieldDoesNotExist:
                resolvable = False
                break
            if not isinstance(field, (django_models.ForeignKey, django_models.OneToOneField)):
                resolvable = False
                break
            current = field.related_model
        if resolvable:
            try:
                current._meta.get_field(segments[-1])
            except FieldDoesNotExist:
                resolvable = False
        if not resolvable:
            errors.append(Error(
                f"{label}.subject_path = {path!r} does not resolve to a real "
                "field via this model's own forward relations.",
                hint="subject_path must be a '__'-joined chain of this model's "
                     "own forward ForeignKey/OneToOneField names, ending in a "
                     "real field name — never a reverse accessor or a "
                     "many-to-many.",
                id="snapadmin.E012",
            ))
            continue

        if hops and get_model_meta(model, "es_storage_mode", None) == EsStorageMode.ES_ONLY:
            errors.append(Error(
                f"{label} is ES_ONLY and subject_path = {path!r} has relation "
                "hops — EsQuerySet.filter() only matches flat field=value, so a "
                "multi-hop path silently matches nothing at export/deletion time.",
                hint="An ES_ONLY model may only declare a zero-hop subject_path "
                     "— a field literally present on the ES document.",
                id="snapadmin.E012",
            ))

    return errors


#: Above this, SNAPADMIN_FETCH_BY_MAX_VALUES no longer meaningfully bounds the
#: request-size DoS the cap exists to close (see fetch_by in
#: snapadmin.api.views) — a value this high is functionally "no cap" while
#: still looking configured and safe.
FETCH_BY_MAX_VALUES_SANE_CEILING = 100_000


def check_fetch_by_max_values(app_configs, **kwargs):
    """Warn: SNAPADMIN_FETCH_BY_MAX_VALUES set so high it defeats its own purpose.

    The cap exists so an explicit ``values`` list on the ``fetch-by`` route
    can never become an unbounded-query denial-of-service vector. Raising it
    far past any plausible legitimate batch size quietly reopens that door
    while the setting still reads as "configured".
    """
    raw = get_setting("SNAPADMIN_FETCH_BY_MAX_VALUES", 10000)
    try:
        max_values = int(raw)
    except (TypeError, ValueError):
        return []
    if max_values <= FETCH_BY_MAX_VALUES_SANE_CEILING:
        return []
    return [Warning(
        f"SNAPADMIN_FETCH_BY_MAX_VALUES = {max_values} is unusually high — it no longer "
        "meaningfully bounds the fetch-by route's request size.",
        hint=f"Values above {FETCH_BY_MAX_VALUES_SANE_CEILING} defeat the purpose of the cap "
             "(an unbounded 'values' list is a denial-of-service vector). Lower it, or confirm "
             "this is genuinely intentional for a trusted, bulk-synchronisation caller.",
        id="snapadmin.W013",
    )]


ALL_CHECKS = [
    check_analytics_db_alias,
    check_masked_fields,
    check_masking_rules,
    check_nested_apps,
    check_nesting_active_site,
    check_sso_providers,
    check_api_write_fields,
    check_api_read_only,
    check_backup_age_recipients,
    check_backup_env_requires_encryption,
    check_backup_s3_configuration,
    check_backup_schedule_cadence,
    check_retention_purge_scheduled,
    check_snap_action_read_only_conflict,
    check_subject_paths,
    check_fetch_by_max_values,
    check_unfold_theme,
    check_snapadmin_profile,
    check_snapadmin_profile_contradiction,
]


def register_checks():
    """Register every SnapAdmin check (idempotent — safe to call from ready())."""
    from django.core.checks import register
    for check in ALL_CHECKS:
        register(check)
