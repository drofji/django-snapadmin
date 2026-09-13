# Security Policy

This document covers how to report a vulnerability in **django-snapadmin**, which versions receive
fixes, the security features the package ships, and how to deploy it safely. For the licences of the
code SnapAdmin depends on or bundles, see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

> **No independent security audit.** SnapAdmin has not had a third-party security audit. Review it
> before using it on sensitive or internet-facing deployments, and pin an exact version in
> production.

## Supported versions

Security fixes are made against the **latest published release** on PyPI. Older pre-releases do not
receive backported patches — upgrade to the newest version to get security fixes.

| Version | Supported |
|---------|-----------|
| Latest release on PyPI (currently `0.1.0b8`) | ✅ |
| Any older pre-`1.0` release | ❌ (upgrade) |

## API stability and compatibility policy

Upgrading for a security fix should never mean rewriting your code, so it has to be clear what
counts as a promise and what does not.

**The public API is:**

- **Import paths** — everything importable from `snapadmin.*` without a leading underscore, and the
  blessed top-level re-exports (`from snapadmin import SnapModel, SnapCharField, …`).
- **`SnapModel` class attributes and `Snap*Field` keyword arguments**, and their defaults.
- **The generated-admin extension surface** — `register_admin()`, `admin_overrides` (a project's own
  callable here always wins over anything the generator produces), `get_admin_fields()`'s return
  shape (the `AdminFieldSets` named tuple: `form_fields`, `list_display`, `search_fields`,
  `list_filter`, `autocomplete_fields`, in that order), `get_admin_media()` and `formatted_id`.
- **`SNAPADMIN_*` settings** — names, defaults and meaning.
- **Management command names** and their documented flags.
- **REST and GraphQL routes and their URL names**, as reversed by `reverse()`.
- **Template block hooks** documented as extension points in the shipped templates.

**The public API is not:** anything named with a leading underscore, module internals not listed
above, the exact HTML or CSS class names of admin pages, log message wording, or the contents of
migrations.

Most of this surface is pinned by `tests/test_public_contract.py`, so a breaking change fails the
suite rather than reaching PyPI quietly.

**The test suite (`tests/`) is not shipped in the wheel or sdist** — only `snapadmin/` and the docs
are; see the [Contributing](README.md#contributing) section. That is a packaging-size choice, not a
coverage gap: every security fix in `CHANGELOG.md`/the release notes has a regression test living
next to the subsystem it fixed (masking, sanitization, permission checks, the audit trail, …), and
CI runs the full suite on every push against the compatibility matrix — what actually ships is what
that suite already verified against a clone of this repository, not a second, unverified copy
trailing behind inside every install.

**Right now (the `0.x` beta series, before `1.0` ships):** breaking changes are possible but never
silent. Each one is called out in [`CHANGELOG.md`](CHANGELOG.md) and the release notes, with a
migration guide when manual steps are involved.

**From `1.0` onward** (a future release — no `1.0` has shipped yet): the project will follow
semantic versioning.

| Change | Allowed in |
|---|---|
| New settings, fields, commands, routes | any minor (`1.x.0`) |
| Bug fixes with no API change | any patch (`1.x.y`) |
| Deprecating a public name (keeps working, warns) | any minor |
| Removing or renaming a public name | a major (`2.0.0`) only |

A deprecated name keeps working for **at least one full minor release**, and says so, before it can
be removed: a `DeprecationWarning` for a Python name, a notice on stderr for a management command
(so a cron job piping stdout still surfaces it). Both name the replacement. A security fix that
cannot be made backward-compatible is the one exception, and is documented as such in the advisory.

**Removed in `0.1.0b8`**, per the announced beta-series removal window — upgrading from any
older pre-`0.1.0b8` release needs the corresponding change:

| Removed name | Use instead |
|---|---|
| `db_backup` (management command) | `snapadmin_db_backup` |
| `purge_expired_data` (management command) | `snapadmin_purge_expired_data` |
| `send_error_digest` (management command) | `snapadmin_send_error_digest` |
| `snapadmin_info` (underscored console script) | `snapadmin-info` (or `manage.py snapadmin_info`) |
| `snapadmin_license_check` (underscored console script) | `snapadmin-license-check` (or `manage.py snapadmin_license_check`) |

**Defaults flipped in `0.1.0b8`:**

| Setting | Pre-`0.1.0b8` default | `0.1.0b8` default |
|---|---|---|
| `SNAPADMIN_REST_API_ENABLED` | `True` | `False` |
| `SNAPADMIN_GRAPHQL_ENABLED` | `True` | `False` |

Both flipped because a project migrating from a plain Django admin never asked for an API at all,
yet got one — writable, unless `api_write_fields` was set per model — the moment `snapadmin.urls`
was included. Pin either setting explicitly to `True` to restore the pre-`0.1.0b8` behaviour. See
[the migration guide](docs/migrations/0.1.0b7_to_0.1.0b8.md).

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
- **The dynamic model API only ever resolves models that were deliberately opted in.**
  `/api/models/<app>/<model>/` 404s for any Django model SnapAdmin does not have in its registry
  (e.g. `auth.User`), regardless of the caller's Django permissions, so the generic API surface can
  never be used to read or write a model nobody opted in.
  **Auditing your exposure means listing the registry, not grepping for `SnapModel`.** There are two
  ways into it — subclassing `SnapModel`, and decorating a plain `django.db.models.Model` with
  `@snap_model(...)` — and both are served identically; `snapadmin.registry.is_registered(model)` is
  the single gate every surface asks. A model opted in with the decorator is easy to miss when
  reviewing by eye, because nothing in its class statement says "SnapAdmin". Run
  `python manage.py snapadmin_info --section inventory` for the authoritative list: every registered
  model, which door it came through, and what it exposes.
- **`SNAPADMIN_PROFILE = "admin"` reduces exposed surface by default.** It turns REST, GraphQL,
  Swagger and GraphiQL off — a smaller attack surface for a project that only serves the Django admin.
  The other two profiles do the opposite and should be chosen as deliberately: `"api"` and `"full"`
  turn REST, GraphQL and Swagger **on** for every registered model. Since `0.1.0b8` the built-in
  default for those switches is `False`, so leaving `SNAPADMIN_PROFILE` unset is the minimal-exposure
  posture, and setting `"full"` is an explicit decision to serve an API — not, as an earlier release
  described it, a no-op.
  An explicit `SNAPADMIN_REST_API_ENABLED` / `SNAPADMIN_GRAPHQL_ENABLED` still overrides it either way;
  `manage.py check` warns (`snapadmin.W009`) when an explicit setting silently disagrees with the
  active profile, so re-enabling a surface a profile turned off is never silent. See
  [`SNAPADMIN_PROFILE` presets](https://drofji.github.io/django-snapadmin/#profiles).
- **`@snap_action` (user-defined REST actions) can never widen a model's own write policy.** Every
  action URL is wired to every HTTP verb, but Django REST Framework rejects a verb outside the
  view's `http_method_names` — the exact same descriptor that already resolves `api_read_only` /
  `api_http_method_names` for the built-in CRUD verbs — before a handler is even selected. A
  `POST`-only action therefore cannot reach a model configured `api_read_only = True`; there is
  nothing to bypass, structurally, not via a second check that could drift out of sync. Beyond
  that, each action requires a Django permission (`view_<model>`/`change_<model>`, derived from its
  declared methods, or an explicit override) — checked with the caller's own `has_perm()` plus, for
  a token-authenticated caller, the token's `allowed_models` scope. `manage.py check` catches a
  declared conflict between an action's methods and its model's own policy at boot
  (`snapadmin.E008`) rather than at first request. See [User-Defined REST
  Actions](https://drofji.github.io/django-snapadmin/#snap-action).

### API tokens
- Tokens are **hashed with SHA-256 at rest** — the raw key is shown **once** at creation (and once
  again on rotation) and never stored. Stored tokens expose only a non-secret 8-char `token_prefix`
  for identification.
- Optional **expiry** (`expiration_date`) and **per-token model scoping** (`allowed_models`). The token
  scope is **AND-ed with the owning user's Django permissions** — a token can never grant more than the
  user has. An empty `allowed_models` delegates entirely to the user's permissions (it is *not* a
  wildcard bypass).
- **`allowed_scopes`** carries free-form, project-defined strings a custom view checks with
  `token_has_scope()` — SnapAdmin only stores and matches them, the meaning is the project's. Unlike
  `allowed_models`, an **empty** `allowed_scopes` denies every scope check (fail-closed): there is no
  Django-permission equivalent an opaque string could delegate to.
- **Rotation is the supported response to a leaked key.** `POST /api/tokens/<id>/rotate/` (or the
  `APIToken.rotate()` model method) replaces the secret **in place** — same row, id, scopes and
  history — and the old key stops authenticating immediately. The new raw key is returned once, the
  same way creation returns it, and every rotation is written to the audit trail.
- **Deactivation, not deletion, is the recommended revocation path.** `POST
  /api/tokens/<id>/deactivate/` flips `is_active` off without losing the row; `DELETE` remains
  available for administrators who want it gone outright. A regular user manages their own tokens —
  list, rotate, deactivate — without needing to be a superuser; a superuser sees and manages every
  token.

### Injection / XSS
- **Wysiwyg (rich-text) values are sanitized with `nh3` on write and on render.** The field's
  `pre_save()` cleans the value before it is stored, so every ORM write path — admin form, REST and
  GraphQL serializers, `Model.save()`, `bulk_create()` — puts sanitized HTML in the column; the
  changelist sanitizes again when rendering, which keeps rows written before this existed safe to
  display. This closes the gap where a stored payload was harmless in the admin but reached every
  other consumer (project templates using `|safe`, a frontend reading the API, exports) verbatim.
  Trusted fields opt out with `safe_html=True`, storing on write can be disabled per field with
  `auto_sanitize=False`, and a custom policy can be supplied via `SNAPADMIN_HTML_SANITIZER` (a dotted
  path to a `Callable[[str], str]`). **`QuerySet.update()` is not covered** — Django does not call
  `pre_save()` for bulk updates, so a caller writing rich text that way must sanitize it themselves.
- **Sanitization fails closed if `nh3` cannot be imported.** `nh3` is currently a required core
  dependency, so this cannot happen in a released install today; the guard exists as defense in depth
  ahead of a planned future release that moves `nh3` behind an optional extra. Both call sites — the
  field's `pre_save()` on write and the changelist display on render — go through one choke point,
  `sanitize.sanitize_html()`, which raises `ImproperlyConfigured` the moment the default nh3 sanitizer
  would run and `nh3` is unavailable, stopping the write or the render rather than letting unsanitized
  HTML through. The `SNAPADMIN_HTML_SANITIZER` escape hatch never imports `nh3` at all, so a project
  supplying its own sanitizer is unaffected either way.
- **XLSX exports never turn row data into formulas.** A spreadsheet writer infers a cell's type from
  its text, so a value beginning with `=` would be stored as a formula the spreadsheet evaluates when
  the file is opened (`=cmd|…`, `=HYPERLINK(…)`, `=WEBSERVICE(…)` — the spreadsheet counterpart of CSV
  injection, and the exported rows are attacker-supplied whenever your users can write to the model).
  `export_format="xlsx"` pins such cells to the text type, so Excel displays the characters that are
  in the database and executes nothing. Control characters the format forbids are stripped and text
  is clamped to the per-cell limit, so one hostile row cannot fail the whole export either.
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
  the choice explicitly, so the exposure is a deliberate decision rather than an oversight. W004 emits
  one grouped warning naming every unguarded model, and skips models served read-only
  (`api_read_only`, or an `api_http_method_names` allowlist without a write verb) — those have no
  mass-assignment surface.
- **`api_read_only` / `api_http_method_names`** remove write verbs entirely for a model, not just at
  the field level. `api_read_only = True` serves a model read-only over the dynamic REST API
  (list/retrieve/count/export) and answers `405` to POST/PUT/PATCH/DELETE — the whole create/update/
  delete surface is gone, so an import-only or reference table can never be written or a blank row
  inserted through the API. `api_http_method_names` is an explicit verb allowlist for finer control.
  Both default to full CRUD; the `snapadmin.W007` check nudges a field-read-only model
  (`api_write_fields = []`) toward `api_read_only` so it returns a clean `405` instead of a
  blank-row insert.
- **`api_field_permissions`** gates an individual field's presence and writability behind a Django
  permission — `{"salary": {"read": "hr.view_salary", "write": "hr.change_salary"}}` — a third guard
  alongside `api_exclude_fields` (absolute) and `api_write_fields` (its own, unchanged, silent-drop
  contract). A denied **read** is *absent* from a REST response (never `null`, never an error — the
  key itself is gone, so nothing about the field's existence leaks) or *nulled* in GraphQL (the
  schema is built once at import time, so a per-request field cannot be removed from the response
  shape the way REST can — a documented, deliberate asymmetry). A denied **write** answers an
  explicit `400` naming the field, since a silently dropped write is a data-loss bug the caller
  cannot detect. The gate is checked upstream of PII masking (does the field appear at all, before
  whether what appears is raw or starred) and also removes a permission-denied field from
  `?field=`/`?ordering=`/`?search=` — the same oracle-prevention rule masking already follows. Wired
  into REST and GraphQL; the admin form and background export are a tracked follow-up, not silently
  unguarded — until they ship, treat a field's `api_field_permissions` rule as REST/GraphQL-only.

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
- **Per-field masking rules and permissions** — `SNAPADMIN_MASKING_RULES` sets, per field, how it is
  obfuscated (a regex `pattern`+`replacement`, or a flat `replacement` redaction) and which
  `permission` unlocks *that one field*, so raw access can be granted narrowly instead of through the
  blanket `snapadmin.view_raw_pii`. Naming a field in a rule also marks it sensitive, so the rules
  cannot be configured in a way that formats a field without also masking it. Rules apply on every
  masking surface (admin changelist, REST, GraphQL, exports, audit diff) through one choke point,
  `masking.mask_field()`. Patterns come from settings but still meet production data, so each is
  compiled once and rejected when it cannot compile or carries a nested quantifier that could
  backtrack catastrophically (`(a+)+`); values over 4096 characters skip the regex. Every one of those
  paths — plus a replacement referencing a group the pattern lacks — falls back to the built-in
  masker, so a broken rule degrades to *more* masking, never to raw data.
- **Field-encryption key management** — encrypted model fields read their key
  material through one resolver, `snapadmin.encryption.keys`, configured by the single
  `SNAPADMIN_ENCRYPTION` dict. Four sources are tried most-secure-first and **never merged**, so a
  stray environment variable cannot half-override a secret store: `KEY_PROVIDER` (a dotted path to a
  callable — the KMS/Vault hook, keeping material out of settings *and* the environment), `KEY_FILE`
  or `SNAPADMIN_ENCRYPTION_KEY_FILE` (a mounted container secret), the `SNAPADMIN_ENCRYPTION_KEYS`
  environment variable, then literal `KEYS` in the settings module. The last is supported but warned
  about (`snapadmin.W016` when the mounted key file is group/world-readable, `snapadmin.W017` when key
  material sits in a settings module with `DEBUG` off) — a key in a settings module is a key in
  version control. Reusing Django's `SECRET_KEY` as encryption key material is refused outright
  (`snapadmin.E017`): `SECRET_KEY` is rotated for session and CSRF reasons, and each rotation would
  otherwise make every encrypted column permanently unreadable. Key material is never rendered — not
  into a `repr`, a `str`, a log line, an exception message or any `snapadmin_info` section; only a key
  id and a short domain-separated fingerprint, which exists so a restore into an environment holding a
  *different* keyset is diagnosable rather than looking like data corruption. The design is
  fail-closed: an encrypted field declared with no resolvable keyset stops `manage.py check`
  (`snapadmin.E018`), a malformed keyset is reported rather than skipped (`snapadmin.E019`), and
  `STRICT: False` relaxes only that startup check — the runtime still refuses to read or write an
  encrypted field without a key (`snapadmin.W018` says so), never falling back to storing plaintext.
  The keyset is ordered — the first key encrypts, every key decrypts, and each ciphertext records the
  id of the key that wrote it — so rotation is prepending one key and re-encrypting, with the old key
  removed only once no row still names it.
- **Encrypted model fields** — the `SnapEncrypted*Field` family (Char, Text, Email, JSON, Integer,
  Decimal, Date, DateTime) stores **AES-256-GCM ciphertext at rest** in a text column and hands
  application code the ordinary Python value. AEAD, so a modified ciphertext fails to decrypt rather
  than decrypting into something else; a **fresh random 96-bit nonce on every write**, never a
  counter and never derived from the value; and the envelope authenticated against its own
  `app_label.model.field`, so a value lifted out of one column and pasted into another fails to
  decrypt instead of quietly relocating a secret. The binding deliberately excludes the primary key,
  so row copies and PK-changing restores keep working — with two consequences stated rather than
  buried. **Renaming the app, model or field invalidates existing rows** until they are
  re-encrypted. And because the primary key is not bound, a ciphertext from one row of a column
  will decrypt in *another* row of the same column: an actor who can already read the stored
  ciphertext and write that column can move a value between rows. Reading the raw ciphertext means
  database access or a role with the field unmasked — at which point the plaintext is available
  anyway — so this is the accepted cost of keeping `INSERT … SELECT`, fixtures and restores
  working. Bind the primary key into your own application-level integrity check if a column needs
  to resist that. Failure is always loud: a dropped
  key id, a tampered payload, a value bound to a different column and an envelope from a newer
  SnapAdmin each raise, naming the key *id* and the column and never key material or the value.
  Nothing on the encrypt/decrypt path logs.
- **Encrypted values do not reach the surfaces that would re-emit them.** A field decrypts
  transparently, so every layer above the ORM receives an ordinary Python value; each has a tested
  default rather than a convention. Elasticsearch: excluded from the mapping and the document (a
  second datastore with a different threat model, which also keeps the value in its own inverted
  index — naming one in an explicit `es_mapping` is `snapadmin.E020`). Audit trail: records *that*
  an encrypted field changed, never the before/after values, in both the SnapAdmin trail and
  Django's own admin history message. REST, GraphQL, exports, the changelist and imports: masked by
  default through the existing PII permission model, unlocked per field with a
  `SNAPADMIN_MASKING_RULES` permission rule. `dumpdata` emits the ciphertext, not the plaintext, so
  a fixture is no more sensitive than the database it came from. Declarations the column cannot
  honour are refused at startup rather than failing quietly: `searchable=True` without a blind index
  (`snapadmin.E021`) would search ciphertext with `icontains` and report the row missing;
  `unique=True` without one (`E022`) is satisfied by every row, since each ciphertext has its own
  nonce; `Meta.ordering` on an encrypted field (`E023`) sorts by the envelope, and unlike a filter
  it cannot be refused at query time; and `filterable=True` (`E024`) would have the admin print
  every distinct plaintext into the changelist sidebar, because a sidebar filter reads the column's
  distinct values *through the ORM*, which decrypts them. The generated admin also drops encrypted
  fields from `sortable_by`, and they stay off the changelist by default.
- **The blind-index sibling is protected exactly as the column is.** `<field>_bi` is a
  deterministic function of the value, so a caller who is masked out of the field but served its
  index has an offline equality oracle over it. The sibling is therefore masked wherever the field
  is, and the REST filter generator emits no filter for either — a filter on the ciphertext would
  be a documented 500, and one on the index would work, which is worse.
- **Lookups fail loudly rather than silently matching nothing.** The database cannot compare, order
  or index a column it cannot read, so `icontains`, `gt`, `startswith`, `range`, transforms and
  `ORDER BY` raise a `FieldError` naming the field. That is a security property, not ergonomics:
  a query that succeeds and returns an empty result turns encrypted data into invisible data, which
  nothing announces and no downstream test catches. `blind_index=True` restores `__exact` / `__in`
  and `unique=True` through a `<field>_bi` sibling column holding `HMAC-SHA256(index key, NFC
  value)`, with the index key **HKDF-derived from the keyset, never the encryption key**, and the
  field's own `app.model.field` as derivation info so indexes cannot be correlated between columns.
  Lookups match under every key in the keyset, so a rotation needs no rebuild. **The leak it buys
  its usefulness with:** a blind index makes *equality* observable — two rows holding the same value
  have the same index, so anyone who can read the column can count duplicates and confirm a guess.
  Against a low-entropy column with a known format that is a dictionary attack away from the value.
  Reasonable for an email address; wrong for a national ID. Off by default.
- **Backup and restore across keysets.** A database dump carries ciphertext, so restoring it into an
  environment with a different keyset produces rows that cannot be read — the failure that looks
  most like data corruption and is not. `manage.py snapadmin_info` prints the keyset *fingerprint*
  precisely so the two environments can be compared before anyone concludes anything.
  `manage.py snapadmin_encrypt_fields` converts stored data (`--adopt` an existing plaintext column,
  `--rotate` rows onto the active key, `--reindex` blind-index columns); it writes nothing without
  `--apply`, is resumable by primary key, counts a row it cannot convert instead of aborting the
  pass, and prints no key material or value on any path, including a failure caused by a malformed
  legacy value.
- **Reading the audit trail is not a way around masking** — the audit-log admin renders each entry's
  diff as a masked field-level table and offers a per-object timeline at
  `/admin/snapadmin/snapadminauditlog/timeline/<app_label>/<model>/<object_id>/`. Both are gated on
  the audit log's own view permission (the same one that lists the rows) and mask through the same
  rules. The raw `changes` JSON is excluded from the change form outright: it was previously only
  *replaced* in `readonly_fields`, which pushed the real field back into the form where Django
  rendered it read-only and unmasked next to the masked copy (fixed in the current release).
- **Immutable audit trail** (`SNAPADMIN_AUDIT_LOG_ENABLED`) records every admin create/update/delete.
  Retention is enforced two ways against the same `SNAPADMIN_AUDIT_RETENTION_DAYS` (default **365**,
  on by default): automatically, by `snapadmin.purge_expired_data` (the audit log is not a
  `SnapModel`, so this is an explicit step in that task/command, not the generic per-model sweep),
  and manually via `snapadmin_audit_export --purge` for a SIEM-export-then-prune pass. Rows are
  append-only (`save`/`delete` raise once persisted) — the purge uses `QuerySet.delete()`, the one
  sanctioned bypass of that guard, never a code path reachable from outside retention pruning.
- **GDPR retention takes uploaded files with it** — `data_retention_files` (a list of
  `SnapFileField`/`SnapImageField` names) makes `purge_expired()` delete those files from storage
  before deleting the row, not just the row: previously a purged row could leave its file
  unreachable-but-undeletable on disk, which is the wrong outcome for a retention feature built for
  compliance. Files are deleted **before** the row (a storage failure leaves the row — and the file's
  name — intact and the purge retryable, raising `SnapPurgeError` instead of silently continuing), and
  a path still referenced by another live row outside the purge is never deleted. `dry_run=True`
  deletes nothing, including files.
- **Export/reindex job housekeeping is opt-in** (`SNAPADMIN_EXPORT_RETENTION_DAYS`, unset by default)
  — unlike the two retention sweeps above, this one deletes files (a downloaded report, an archival
  export) a project may want to keep, so it needs an explicit setting rather than an on-by-default
  window. Once set, `snapadmin.purge_expired_data` deletes finished `SnapExportJob`/`SnapReindexJob`
  rows past the window and their published files (files before rows, same ordering/failure rule as
  `data_retention_files`), plus a sweep for any export file left with no job row at all. Assumes the
  export storage location is dedicated to SnapAdmin exports — do not point it at a bucket holding
  unrelated files.
- **`snapadmin.W012`** warns at startup when retention is configured anywhere (a model's
  `data_retention_days` or `data_retention_date_field`, the audit log's on-by-default window, or
  `SNAPADMIN_EXPORT_RETENTION_DAYS`)
  but no `CELERY_BEAT_SCHEDULE` entry runs `snapadmin.purge_expired_data` — the exact state every
  retention-scattered report this batch of checks addresses turned out to be in: retention configured,
  nothing scheduled to enforce it.
- **GDPR subject-access requests** (`manage.py snapadmin_subject_request export|delete`) — export or
  delete everything reachable from one data subject, via every registered model's own `subject_path`
  declaration (`snapadmin.E011` fails `manage.py check` for a registered model that never declares it
  at all — not even `None`; `E012` catches a declared-but-malformed one: a subject model whose own path
  doesn't match its identifier, a path over 3 relation hops, one that doesn't resolve via this model's
  own *forward* relations, or a multi-hop path on an `ES_ONLY` model).
  - **Gated on `snapadmin.view_raw_pii`** — a SAR export is unmasked by design (it goes to the
    subject), which makes it a high-value artefact; `--user` must already be trusted with raw PII, and
    every run (export or deletion) is written to the audit trail against that operator.
  - **Export reuses the existing async-export machinery** (one `SnapExportJob` per matched model, the
    same masking bypass a PII-privileged requester already gets elsewhere), so there is no second
    "skip masking" code path to get wrong. `--recipient` AGE-encrypts the finished bundle in place
    (the same machinery backups use) and removes the plaintext.
  - **Deletion is dry-run by default.** Both modes run the identical pre-flight — a Django deletion
    `Collector` walk over every matched row, which also discovers cascade spillover the `subject_path`
    declarations alone would not show — so the preview matches what `--confirm` actually does. Any
    protected relation (`on_delete=PROTECT`) refuses the **whole** run up front and deletes nothing,
    rather than deleting in dependency order to route around it.
  - **The deletion audit entry cannot be swept away by a later request for the same subject** —
    `SnapadminAuditLog` is deliberately outside the general SnapAdmin registry (see the audit-trail
    entry above) and therefore carries no `subject_path` at all.
  - **Honest limits, printed on every run:** this command reaches only the SnapAdmin registry — it
    cannot see or touch a backup bundle, an Elasticsearch copy a model does not itself mirror, or any
    third-party store a project integrates outside SnapAdmin.
- **Backups** — 3-2-1 database backups with local/network/FTP(S)/SFTP/S3-compatible targets; transport
  credentials come from `SNAPADMIN_BACKUP_*` settings/env, never hard-coded. The S3 destination
  supports the ambient AWS credential chain (environment variables, a shared config file, an IAM
  role / instance profile) when `SNAPADMIN_BACKUP_S3_ACCESS_KEY_ID`/`_SECRET_ACCESS_KEY` are left
  unset — the recommended setup on AWS, since a static key there is a downgrade from a rotating
  instance-profile credential. `manage.py check` warns (`snapadmin.W011`) when a bucket is configured
  with neither an explicit key pair nor a detectable ambient signal, and again when
  `SNAPADMIN_BACKUP_S3_ENDPOINT_URL` is set to something that is not a URL — a half-configured S3
  destination silently never uploading is the same class of failure as everything else in this list.
  Plain FTP ships credentials in clear text; use FTPS (`SNAPADMIN_BACKUP_FTP_TLS`), SFTP or S3 for an
  offsite copy instead. Hetzner Storage Box is an SFTP/SCP/WebDAV service — it uses the `sftp`
  destination, never `s3`; the S3 destination is for genuinely S3-compatible services (AWS, MinIO,
  Backblaze B2, Hetzner **Object Storage**, Wasabi).
- **SFTP host-key verification, and where the file lives** — the `sftp` destination rejects a host
  whose key is not already known (`paramiko.RejectPolicy`) rather than trusting it on first use, so
  a man-in-the-middle cannot take a permanent foothold on the offsite copy by answering the first
  connection. That guarantee is unchanged; what changed is **which file** answers the question.
  Left unset, paramiko expands `~/.ssh/known_hosts` against the `HOME` of whoever runs the process
  and silently ignores the file's absence — so in a container, where a `docker exec` without a
  `USER` line runs as root with `HOME=/root`, a `known_hosts` correctly baked into the image at
  `/home/<svc>/.ssh/known_hosts` is never read, and the rejection that follows blames a missing
  host key rather than the lookup path. `SNAPADMIN_BACKUP_SFTP_KNOWN_HOSTS` names the file outright.
  It **replaces** the `~/.ssh` lookup rather than adding to it, the way OpenSSH's own
  `UserKnownHostsFile` does — pinning a path must not silently widen trust to whatever the running
  user's home directory happens to hold — and an unreadable file is a hard failure naming the
  setting, not a silent fall-through to trusting nothing (or, worse, to a weaker policy). Both the
  backup and the restore path resolve it the same way. Leaving it unset keeps the previous
  behaviour exactly.
- **Backup encryption (AGE)** — set `SNAPADMIN_BACKUP_AGE_RECIPIENTS` (one or more age/SSH public
  keys) and every dump is encrypted **in-stream** — `pg_dump`/`mysqldump`/SQLite → gzip → age → the
  `.age`-suffixed file — before a single byte reaches disk; no plaintext or plain-gzip artefact is ever written, not
  even transiently, and a mid-pipeline failure leaves nothing behind rather than a partial/corrupt one.
  With the setting empty (the default) nothing changes.
  - **What this protects against:** a compromised or merely readable backup *destination* — a rented
    FTP host, a Storage Box, anyone who can list your `local`/`network` directory. Without a matching
    private key, the file is unreadable.
  - **What this does *not* protect against:** a compromised application server. The server already
    holds the live, unencrypted database — backup encryption adds nothing there; it protects the copy
    once it leaves the machine that produced it.
  - **Any one of N recipients decrypts alone** — age's multi-recipient property, not a shared secret:
    encrypting to three keys means three people can each restore independently, with no re-encryption
    needed to add or remove a reader from *future* backups.
  - **Key rotation, stated plainly because it surprises people:** adding a recipient only affects
    backups made *after* the change — it does not retroactively grant access to older bundles.
    Removing a recipient is the same in reverse: an already-encrypted bundle stays decryptable by every
    key it was originally encrypted to, forever, since decrypting it requires no new encryption step.
    If a key is compromised, treat every backup already encrypted to it as compromised too, not just
    future ones — re-encrypting past bundles to a fresh key set is a manual step, not automatic.
  - **A private key (identity) never appears in a setting, in `snapadmin_info` output, or in a log
    line** — only the recipient (public key) list and the identity **file path** are ever recorded or
    printed; the identity is supplied only at restore time, from a file
    (`SNAPADMIN_BACKUP_AGE_IDENTITY_FILE`), never as key material in a setting.
  - **Two interchangeable backends** — the in-process `pyrage` library (the `[age]` extra) or the
    `age` command-line tool (`SNAPADMIN_BACKUP_AGE_BACKEND="binary"`, e.g. `apt install age` on
    Debian 12+/Ubuntu 22.04+). Both implement the identical, standardised file format, so a bundle
    encrypted with one restores fine with the other — or with the plain `age` CLI run by hand on a
    jump host with no Django involved at all.
- **Generating AGE keypairs (`manage.py snapadmin_age_keygen`)** — the library can mint the keypair
  itself, through the same `pyrage`/`age-keygen` backend resolution as encryption. The private key
  (identity) is written to disk exactly once and never printed, logged, or returned in the command's
  own output — only the public recipient is, since that half is safe to share. Before writing
  anything, the command checks the project's `.gitignore` for a rule that already excludes the
  `.age/` directory the keypair lands in — recognising more than a literal `.age`/`.age/` line (a
  leading-slash anchor, a `**/` prefix or `/**` suffix, and a blanket dotfile rule like `.*`) — and
  appends one with a visible message if none is found, rather than trusting one is already there.
  Every run closes with an explicit reminder to move the private key to a secure location (a
  password manager, a secrets vault, an encrypted volume) and delete the local `.age/` directory —
  it exists only as a generation convenience, never as long-term storage for key material. **Not a
  full `.gitignore` engine** — mid-pattern `**` and negation-line ordering are out of scope; a
  `!`-negation line is always treated as *not* covering `.age/` (it can also never remove coverage a
  broader rule already gave, which is the safe direction for a check whose job is "warn when in
  doubt").
- **Backup bundle contents — the `.env` fail-closed rule** — `SNAPADMIN_BACKUP_INCLUDE` (default
  `["db"]`) can extend a run to `media` and/or `env` (a project `.env`/secrets file). Including `env`
  with `SNAPADMIN_BACKUP_AGE_RECIPIENTS` empty is refused **fail closed**, in two independent places:
  a startup system check (`snapadmin.E007`, so `manage.py check`/`migrate`/`runserver` catch it before
  a single backup ever runs) and a runtime guard inside the backup path itself (so it still refuses if
  reached another way — recipients configured, then cleared, without a restart). A `.env` file's
  contents — `SECRET_KEY`, database passwords, third-party API keys — are never written to a backup
  destination unencrypted. The always-unencrypted `manifest.json` sidecar that accompanies every
  bundle (by design — it must be readable without an identity) carries no secrets: only part names,
  per-part **ciphertext** checksums, versions and the public recipient list.
- **An off-host destination with no encryption is flagged, not blocked (`snapadmin.W021`)** — the
  `.env` rule above guards the `env` part and nothing else, so a project that never bundles `.env`
  could still ship the *database dump itself* in plain gzip to an FTP host, an SFTP Storage Box, an
  S3 bucket or a mounted NFS share without a word at startup. That dump is the same data the `.env`
  file merely unlocks. `manage.py check` now warns when any destination that leaves this machine
  (`network`, `remote`, `sftp`, `s3`) is active while `SNAPADMIN_BACKUP_AGE_RECIPIENTS` is empty,
  naming the offending destinations and the setting that fixes them. `local` is excluded — it is the
  staging directory on the same host. Deliberately a **warning**, unlike the `env` case: the
  transport may already be encrypted (SFTP, FTPS, HTTPS to S3) and the destination may encrypt at
  rest (SSE-KMS, LUKS), and neither is visible from `settings.py` — failing boot over a control
  SnapAdmin cannot observe would be wrong, while silence in the common case, where nobody chose at
  all, is worse. Silence it via `SILENCED_SYSTEM_CHECKS` once you have confirmed the destination
  really does encrypt.
- **Restoring a backup** (`manage.py snapadmin_restore`) — dry-run by default; `--confirm` performs
  it. Every part's checksum is verified against the manifest before any byte reaches the live
  database/media/`.env`, so a truncated or corrupted upload is refused rather than half-restored. An
  encrypted bundle restored with no `--identity` prints the recipient count and fingerprints, never a
  bare parse error. `env` is never restored by a bare `--confirm` — it overwrites secrets, so it must
  be named explicitly in `--only`. A restore is **not live-safe**: existing database connections are
  terminated and, for PostgreSQL, the database is dropped and recreated before the dump loads — plan
  a maintenance window.
- **The pre-restore snapshot and `manage.py snapadmin_rollback`** — before a `--confirm`ed restore
  touches anything, the current live state of every part it is about to overwrite is automatically
  snapshotted (encrypted the same way a real backup would be, if recipients are configured) into
  `SNAPADMIN_RESTORE_SNAPSHOT_DIR`. **If the snapshot itself fails, the restore is aborted** — never on
  a best-effort basis, since this is the entire point of the safety net. `--no-snapshot` exists for the
  operator who knows better and prints a loud warning when used.
  - **What this protects against:** a restore that turns out to have been the wrong call — the
    snapshot lets you get back to exactly the state immediately before the restore ran.
  - **What this does *not* protect against:** losing the disk the snapshot lives on. A snapshot is
    only as good as the storage underneath it — it is **not** a substitute for the encrypted offsite
    copy a real, separately-stored backup destination provides. Snapshots also have their own short
    retention (`SNAPADMIN_RESTORE_SNAPSHOT_KEEP`, default 3) specifically so they never compete with
    the real backup policy for disk — they are a safety net with a short half-life, not a backup.
- **Read-replica routing** (`SNAPADMIN_ANALYTICS_DB_ALIAS`) keeps read-only list/retrieve off the
  primary; writes always stay on `default`.
- **Database sharding (`SNAPADMIN_SHARDING`)** — a separate, more general mechanism from the single
  read-replica alias above, for a project partitioning data across multiple physical databases.
  **DSNs belong in environment variables, never hard-coded in a settings module** —
  `SNAPADMIN_SHARDING['SHARDS'][...]['PRIMARY']`/`['REPLICAS']` are plain connection strings
  embedding credentials, exactly like `DATABASES` itself, and should be built from `os.environ`
  the same way. Health checks that drive failover/fallback (`HA_SETTINGS`) are a **TCP reachability
  probe only** — a successful socket connect proves the host is listening on the right port, not
  that the database is actually accepting queries or that the named database exists; treat a
  "failover succeeded" outcome as "the replica was reachable," not as a guarantee of replication
  currency. **Sharding is opt-in per model** (`shard_key`, mirroring `tenant_scoped`) — no model,
  including Django's own `auth`/`sessions`/`admin`/`contenttypes` tables, is ever routed across
  shards without explicitly asking. A misconfigured `SNAPADMIN_SHARDING` degrades to sharding
  staying off (logged, surfaced loudly by `manage.py check` — `snapadmin.E013`–`E016`) rather than
  crashing `django.setup()` on every deploy, **except** `allow_migrate()`, which always degrades to
  "no opinion" on a broken config specifically so it can never block an unrelated app's migration.
  `python manage.py snap_migrate` and the sharding-aware `manage.py snapadmin_db_backup` both target
  every shard's **primary only** — a replica is never migrated or dumped directly, since replication
  already propagates both at the database layer.
- **Alert webhook URLs are credentials** — a Slack/Discord/Teams incoming-webhook URL and a Telegram
  bot token let their holder post into your channel, so `SNAPADMIN_ALERT_WEBHOOKS` entries belong in
  environment variables, not in committed settings. SnapAdmin never writes one to a log line (a
  delivery failure is logged as `alert_channel_failed` with the host only —
  `https://hooks.slack.com/…`), never puts one in an alert body, and never reports one from
  `snapadmin_info`. Alert *content* is unfiltered by design: an error subject, exception class and
  path go to whatever channel you configure, so treat an alert channel as trusted as your error
  emails — and prefer a private channel for the health alert, which names failing subsystems.

### Multi-tenancy
- **Row-level tenant isolation** (`snapadmin.tenancy`) — a model opts in with `tenant_scoped = True`
  plus a tenant column (`tenant_field()`); once opted in, **every generated surface requires a bound
  tenant to see or write a single row**: the admin, REST, GraphQL, Elasticsearch routing
  (`es_search`/`es_filter`/`es_aggregate`/`es_count`/`es_scan`), async export/import jobs, and the
  offline cache. **Default-deny** is the whole guarantee: with no tenant bound, a read returns empty
  and a write is refused outright — never "every row" as the fallback. The current tenant is resolved
  per request by `SnapTenantMiddleware` + `SNAPADMIN_TENANT_RESOLVER` (a dotted path,
  `resolver(request) -> tenant | None`; unset means every request resolves to no tenant, the
  fail-closed default until a project configures one); for an async export/import job, which runs on a
  Celery worker with no request of its own, the submitter's tenant is resolved once at job-creation
  time via `SNAPADMIN_TENANT_USER_RESOLVER` and stamped onto the job row for the worker to replay.
- **A write can never assign a foreign tenant.** REST create/update, the admin form and CSV/NDJSON
  import all stamp the tenant server-side from the bound context, never from client/file input — a
  request body or import column naming a *different* tenant is refused outright (a `400` naming the
  field on REST, a rejected column on import), never silently overwritten or silently dropped.
- **`use_all_tenants()` is the one explicit, audited escape hatch**, reserved for background code
  whose job is inherently cross-tenant: the retention purge (a row's age decides whether it is
  purged, not its tenant) and the Elasticsearch reindex (the index must stay complete across every
  tenant). Application code must never reach for it to work around a scoping failure.
- **`snapadmin.E009`** fails `manage.py check` when a model declares `tenant_scoped = True` but the
  declaration cannot actually be enforced — no resolvable tenant field, or the model is registered via
  `@snap_model` rather than subclassing `SnapModel` (the scoping hook lives in `SnapModel`'s
  `EsManager`, never a plain registered model's default manager).
- **A `NULL` tenant value is unassigned data, not shared data** — it matches no tenant's filter, by
  ordinary SQL equality semantics, so it is invisible to every tenant until something assigns it,
  never a fallback any caller can reach.
- **Honest limit, stated as plainly as the feature:** isolation is **logical**, not physical. Every
  guarantee above holds because every surface reads through the same tenant-scoped manager — a raw SQL
  query, a custom management command calling `Model.objects` without binding a tenant, or a
  third-party package querying the table directly still leaks. This is not a substitute for a separate
  schema or database where that level of isolation is required.
- **Backups and restores are not tenant-scoped at all.** `snapadmin.backup` dumps the whole configured
  database (`pg_dump`/`mysqldump`/a raw SQLite file copy) below the ORM, entirely outside this
  feature's reach — a backup bundle contains every tenant's data, and restoring one is an all-tenants
  operation. Do not treat a backup, or a restore, as a tenant-scoped operation in any deployment plan.

### Attack-surface reduction & extension guards
- Each surface can be **switched off**: `SNAPADMIN_REST_API_ENABLED`, `SNAPADMIN_GRAPHQL_ENABLED`,
  `SNAPADMIN_SWAGGER_ENABLED` (disabling removes the routes entirely). Both default to `False` — a
  project opts in explicitly, rather than the pre-`0.1.0b8` behaviour of opting out. The user-management
  API (`SNAPADMIN_USER_API_ENABLED`) is likewise **off by default**.
- The **bulk ES reindex endpoint** is off by default (`SNAPADMIN_REINDEX_API_ENABLED`) and
  `IsAdminUser`-gated when enabled.
- **Deletion guards** — `SnapModel.api_can_delete(request)` and the `SNAPADMIN_API_DELETE_GUARD` dotted
  path can veto deletes through the dynamic API (returns `403`), layered on top of Django permissions.

## Production hardening checklist

- `DEBUG = False`; set a strong `SECRET_KEY` and a correct `ALLOWED_HOSTS`.
- Serve everything over **HTTPS** — API tokens and session cookies are bearer credentials.
- Keep the dashboard gated (leave `SNAPADMIN_DASHBOARD_PUBLIC` unset/`False`).
- Keep `SNAPADMIN_GRAPHIQL_ENABLED` off in production and `SNAPADMIN_GRAPHQL_REQUIRE_AUTH = True`.
- `SNAPADMIN_REST_API_ENABLED` / `SNAPADMIN_GRAPHQL_ENABLED` default to `False` — turn on only the
  surface(s) the project actually serves, rather than both by habit.
- Scope API tokens with `allowed_models` (and `allowed_scopes` for your own endpoints) and set an
  `expiration_date`; rotate a leaked token with `POST /api/tokens/<id>/rotate/` rather than deleting
  and reissuing it — the row, its scopes and its history survive, and the old key stops authenticating
  immediately.
- Leave the user-management API and ES-reindex endpoints disabled unless needed; gate any you enable.
- Put backup/SFTP/SMTP credentials **and alert webhook URLs** in environment variables, not in
  committed settings.
- **Encrypt backups** — set `SNAPADMIN_BACKUP_AGE_RECIPIENTS`. An unencrypted dump on a rented offsite
  server is your whole database in someone else's hands; encryption costs one setting.
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
