# Security Policy

This document covers how to report a vulnerability in **django-snapadmin**, which versions receive
fixes, the security features the package ships, and how to deploy it safely. For the licences of the
code SnapAdmin depends on or bundles, see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

> **Beta software.** SnapAdmin is in the `0.1.0bN` beta series and has **not** had an independent
> security audit. Review it before using it on sensitive or internet-facing deployments, and pin an
> exact version in production.

## Supported versions

Security fixes are made against the **latest published release** on PyPI. Older pre-releases do not
receive backported patches — upgrade to the newest version to get security fixes.

| Version | Supported |
|---------|-----------|
| Latest release on PyPI (currently `0.1.0b4`) | ✅ |
| Any older alpha/beta | ❌ (upgrade) |

## Reporting a vulnerability

**Please do not open a public GitHub issue for security problems.** Report privately:

1. **Preferred — GitHub private vulnerability reporting:**
   <https://github.com/drofji/django-snapadmin/security/advisories/new>
   (Repository → **Security** → **Report a vulnerability**.)
2. **Alternative — email:** drofji@icloud.com with the subject `SECURITY: django-snapadmin`.

Please include, as far as you can:

- affected version(s) and environment (Python / Django versions, relevant `SNAPADMIN_*` settings);
- a description of the issue and its impact;
- a minimal proof-of-concept or reproduction steps;
- any suggested fix or mitigation.

**What to expect:** we aim to acknowledge a report within a few days, agree on a severity and a fix
timeline, and credit you in the release notes unless you prefer to stay anonymous. Please give a
reasonable window for a fix before any public disclosure (coordinated disclosure).

## Security model & built-in protections

SnapAdmin generates admin + API surfaces from your models, so most controls are **configuration-driven**.
Key protections:

### Authentication & authorization
- **REST API auth is pluggable** via `SNAPADMIN_API_AUTHENTICATION_CLASSES` (dotted paths, like DRF's
  own setting) — SnapAdmin token auth by default; add session and/or JWT.
- **Permissions are enforced everywhere.** REST and GraphQL both require the caller to hold the model's
  Django `view` / change permissions; there is no anonymous data access by default.
- **GraphQL** requires authentication and per-model permissions when
  `SNAPADMIN_GRAPHQL_REQUIRE_AUTH = True` (default). The check applies to **every relation the query
  traverses**, not just the top-level query field: reading `A { relatedB { … } }` requires the
  `view` permission (and, for token auth, the `allowed_models` scope) on **both** `A` and `B`. A
  related object the caller may not view resolves as a `Permission denied.` error rather than
  leaking its data. The GraphiQL playground follows `DEBUG` unless overridden with
  `SNAPADMIN_GRAPHIQL_ENABLED` — keep it off in production.
- **The dynamic model API only ever resolves `SnapModel` subclasses.** `/api/models/<app>/<model>/`
  404s for any Django model that isn't declared as a `SnapModel` (e.g. `auth.User`), regardless of the
  caller's Django permissions — the generic API surface can never be used to read or write a model that
  wasn't intentionally opted in via `SnapModel`.

### API tokens
- Tokens are **hashed with SHA-256 at rest** — the raw key is shown **once** at creation and never
  stored. Stored tokens expose only a non-secret 8-char `token_prefix` for identification.
- Optional **expiry** (`expiration_date`) and **per-token model scoping** (`allowed_models`). The token
  scope is **AND-ed with the owning user's Django permissions** — a token can never grant more than the
  user has. An empty `allowed_models` delegates entirely to the user's permissions (it is *not* a
  wildcard bypass).

### Injection / XSS
- **Wysiwyg (rich-text) values are sanitized** with `nh3` before being marked safe and rendered in the
  admin changelist, preventing stored XSS from field-write → admin-session escalation. Trusted fields
  can opt back into verbatim HTML with `safe_html=True`; a custom policy can be supplied via
  `SNAPADMIN_HTML_SANITIZER` (a dotted path to a `Callable[[str], str]`).
- Data access goes through the Django ORM / DRF serializers — no hand-built SQL from user input.
- **`POST /api/exports/` `filters` are restricted to the target model's own fields.** The dict is
  applied as `queryset.filter(**filters)`, so an unvalidated key could otherwise traverse a
  relationship (`fk__field`, a reverse relation, a many-to-many lookup) to reach fields on a
  related model the caller's `view` permission never covered, turning the export into a
  boolean/prefix exfiltration oracle. `ExportJobCreateSerializer` now validates every key against
  an allowlist of the model's own concrete fields, each restricted to a small, type-appropriate
  set of lookups (e.g. `exact`/`in`/`icontains` for text, `exact`/`in`/`gte`/`lte` for numbers and
  dates) — an unknown field, a relation-traversing key, or a disallowed lookup is rejected with a
  `400` before the queryset is ever built.

### Open redirect
- **SSO provider URLs are never resolved to an external origin from a same-site-looking value.**
  `get_sso_providers()` drops any `SNAPADMIN_SSO_PROVIDERS` entry whose `url` is protocol-relative
  (`//host/path`) — such a value looks site-relative but `request.build_absolute_uri()` resolves it
  to a different host, which would otherwise become an open-redirect login button if the setting is
  ever built from a templated source (env var, admin-editable setting, generated value) rather than a
  hardcoded literal. `SSOProviderView` applies the same check independently as defense in depth. An
  optional `SNAPADMIN_SSO_ALLOWED_HOSTS` allowlist further restricts *absolute* provider URLs to known
  hosts when set; it is opt-in and off by default, since pointing a provider at a genuinely external
  identity provider (e.g. `https://login.microsoftonline.com/...`) is the normal, expected case.
  `manage.py check` warns (`snapadmin.W005`) on a misconfigured provider before it ships.

### Information disclosure
- The **system dashboard is staff-gated by default** (it surfaces hostname, processor, OS, database
  name, service health and `ALLOWED_HOSTS`). Anonymous callers are redirected to login and non-staff
  get `403`. Opt into a public status page only deliberately with `SNAPADMIN_DASHBOARD_PUBLIC = True`.
- **`api_exclude_fields`** hides sensitive columns from every API surface (REST, GraphQL, schema
  introspection) while the admin keeps showing them.
- **`api_write_fields`** guards against mass assignment: when set, only the listed fields accept a
  client-supplied value on REST create/update — every other field is forced read-only through the
  API (it may still be returned in responses). Left unset, every non-excluded field stays writable,
  matching pre-existing behaviour; the `snapadmin.W004` system check flags any model that hasn't made
  the choice explicitly, so the exposure is a deliberate decision rather than an oversight.
- **`api_read_only` / `api_http_method_names`** remove write verbs entirely for a model, not just at
  the field level. `api_read_only = True` serves a model read-only over the dynamic REST API
  (list/retrieve/count/export) and answers `405` to POST/PUT/PATCH/DELETE — the whole create/update/
  delete surface is gone, so an import-only or reference table can never be written or a blank row
  inserted through the API. `api_http_method_names` is an explicit verb allowlist for finer control.
  Both default to full CRUD; the `snapadmin.W007` check nudges a field-read-only model
  (`api_write_fields = []`) toward `api_read_only` so it returns a clean `405` instead of a
  blank-row insert.

### Data protection & auditability
- **PII masking** — `SNAPADMIN_MASKED_FIELDS` masks configured fields in the admin, the REST API and
  GraphQL for users without PII-view permission; masked fields are also dropped from the change form
  for those users. A masked field is masked identically whether it is read over REST or GraphQL, and
  the same masking now covers every other output path a masked field could otherwise leak through: the
  async export (`POST /api/exports/`, masked unless the requesting user holds PII access; a masked
  field is also rejected as an export `filters` key, since a match/no-match on `job.total_rows` is
  itself an oracle), the audit trail's `changes` diff (masked in the admin display and in
  `snapadmin_audit_export` unless `--reveal-pii` is passed), and the auto-generated REST
  filter/ordering/search parameters — a masked field is silently excluded from `?field=`,
  `?ordering=field` and `?search=` for a caller without PII access, so match/no-match, sort order or
  search hits can't be used as an oracle to recover the value a masked response body never reveals raw.
- **Immutable audit trail** (`SNAPADMIN_AUDIT_LOG_ENABLED`) records every admin create/update/delete;
  retention via `SNAPADMIN_AUDIT_RETENTION_DAYS` and `snapadmin_audit_export` for SIEM ingestion.
- **Backups** — 3-2-1 database backups with local/network/FTP(S)/SFTP targets; transport credentials
  come from `SNAPADMIN_BACKUP_*` settings/env, never hard-coded.
- **Read-replica routing** (`SNAPADMIN_ANALYTICS_DB_ALIAS`) keeps read-only list/retrieve off the
  primary; writes always stay on `default`.

### Attack-surface reduction & extension guards
- Each surface can be **switched off**: `SNAPADMIN_REST_API_ENABLED`, `SNAPADMIN_GRAPHQL_ENABLED`,
  `SNAPADMIN_SWAGGER_ENABLED` (disabling removes the routes entirely). The user-management API
  (`SNAPADMIN_USER_API_ENABLED`) is **off by default**.
- The **bulk ES reindex endpoint** is off by default (`SNAPADMIN_REINDEX_API_ENABLED`) and
  `IsAdminUser`-gated when enabled.
- **Deletion guards** — `SnapModel.api_can_delete(request)` and the `SNAPADMIN_API_DELETE_GUARD` dotted
  path can veto deletes through the dynamic API (returns `403`), layered on top of Django permissions.

## Production hardening checklist

- `DEBUG = False`; set a strong `SECRET_KEY` and a correct `ALLOWED_HOSTS`.
- Serve everything over **HTTPS** — API tokens and session cookies are bearer credentials.
- Keep the dashboard gated (leave `SNAPADMIN_DASHBOARD_PUBLIC` unset/`False`).
- Keep `SNAPADMIN_GRAPHIQL_ENABLED` off in production and `SNAPADMIN_GRAPHQL_REQUIRE_AUTH = True`.
- Scope API tokens with `allowed_models` and set an `expiration_date`; rotate leaked tokens (delete +
  reissue — the raw key cannot be recovered).
- Leave the user-management API and ES-reindex endpoints disabled unless needed; gate any you enable.
- Put backup/SFTP/SMTP credentials in environment variables, not in committed settings.
- Restrict who has `is_staff` / model permissions — SnapAdmin honours standard Django auth.

## Supply chain

The **base install carries only permissive licences (MIT / BSD / Apache-2.0)** and is safe for
commercial and proprietary use. Anything copyleft or commercially-restricted is an **opt-in extra**,
imported lazily so the base package never ships it — e.g. the CKEditor 5 rich-text editor
(GPL/commercial) lives behind `django-snapadmin[wysiwyg]`. See
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for the full breakdown of what is used, what is
optional, and under which licence.

---

*This policy is provided in good faith and is not legal advice. For commercial deployments, have your
own security and legal teams review the package and its dependencies.*
