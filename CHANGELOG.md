# Changelog

All notable changes to **django-snapadmin** are recorded here. This file is a concise,
version-by-version summary; the full, prose release notes for each version live in
[the docs repository](https://github.com/drofji/django-snapadmin/tree/main/docs/releases/) 
(shipped in the source distribution) and online in the project documentation.

The project follows [PEP 440](https://peps.python.org/pep-0440/) versioning. Semantic versioning
begins once the project reaches a stable `1.0` release; until then, see `SECURITY.md`'s
API-stability policy for what counts as public API and how breaking changes are handled during
the `0.x` beta series.

## Unreleased

### Breaking
- `snapadmin.E027` fails `manage.py check` when `SnapModel`'s `EsManager` silently replaces a
  mixin's own `objects` manager, or a `tenant_scoped` model's `objects` is not an `EsManager` —
  both cross-scope data leaks. Declare a manager inheriting from both on the model.
- The `APIToken` admin is registered only while REST or GraphQL is on;
  `SNAPADMIN_TOKEN_ADMIN_ENABLED = True` keeps it for projects using token auth in their own views.
- The generated changelist shows the primary key for integer keys only (`admin_list_display_pk`
  overrides), and references a key not named `id` by its real name.
- A REST viewset action missing from the permission map is refused instead of falling back to
  `view`; map a project's own with `SNAPADMIN_API_ACTION_PERMISSIONS`.
- An `ES_ONLY` retention purge that fails raises `SnapPurgeError` instead of returning `0`.
- `EsQuerySet.delete()` on an `ES_ONLY` model raises `SnapEsUnavailable` when Elasticsearch cannot
  delete, instead of counting the documents as deleted (a GDPR erasure no longer reports success).
- The SFTP backup location in the run summary and `db_backup_stored` is the directory the server
  resolved, so a relative `SNAPADMIN_BACKUP_SFTP_DIR` now reports the full path from the login
  directory.

### Added
- `snapadmin_restore --database <alias>` for restore drills: `db` only, into another alias, with a
  per-table row count; an alias pointing at `default`'s database is refused.
- `SNAPADMIN_PURGE_EXTERNAL = True` lets an external cron satisfy `snapadmin.W012`.
- Snap fields accept a positional `verbose_name`, like Django fields.
- `PATCH /api/tokens/<id>/` renames a token (name only; other fields are a `400`).
- The sdist carries the test suite (and the demo, docs and `pytest.ini` it needs); the wheel does not.
- `snapadmin-new --admin-only` generates an admin-only project (no REST API / GraphQL).
- System checks `snapadmin.W023` (backups configured but disabled), `W024` (`env` part with no env
  file) and `W025` (masked fields behind a hand-written admin that does not mask).
- `SnapModel.purge_expired()` returns `SnapPurgeResult`, an `int` with `skipped_protected`.

### Changed
- An e-mail with a one- or two-character local part masks to `***@domain`; a masking-rule pattern
  that matches nothing falls back to the built-in masker instead of returning the raw value.
- `snapadmin.W022`'s hint covers SFTP accounts whose login directory is the absolute path, and a
  directory created on the fly is logged as `sftp_backup_dir_created`.

### Fixed
- A due row held by a `PROTECT`/`RESTRICT` foreign key no longer aborts the model's retention purge,
  and no longer loses its `data_retention_files` while the row stays.
- `PIIMaskingSerializerMixin` / `FieldPermissionSerializerMixin` mask and gate on a hand-built
  `ModelSerializer` too (they read `Meta.model`); before, they returned every field raw there.
- Deleting an ES search result of a `DUAL` model deletes the database rows; it used to delete
  nothing while reporting every row deleted.
- Restore and snapshot refuse unknown parts; the digest/health-alert tasks always return a `status`.
- `delete_pks_from_es()` treats a `failures` list in the Elasticsearch answer as a failed delete.
- A masked changelist column keeps the field's `verbose_name`.
- Generated `ModelAdmin` classes report the model's module as `__module__`.

### Security
- `formatted_id` escapes a non-integer primary key instead of rendering it as markup (stored XSS).
- Alert webhooks accept only `http`/`https` URLs.
- `snapadmin_restore` escapes the database name in the SQL it sends to `psql`.

### Deprecated
- `SnapModel.admin_sections` — never read; setting it raises `snapadmin.W026`.

## 0.1.0b9 — 2026-09-15

Field-level encryption gains its cipher and field types; the test suite gains random ordering and a
real-services CI job; the documentation gains an honest account of both, including what is not
covered. Read `Breaking` first — `snapadmin.E026` can stop a deployment whose Elasticsearch search
has silently never worked.

### Breaking
- `snapadmin.E026` fails `manage.py check` on a model that mirrors to Elasticsearch with neither
  `es_mapping` nor `es_auto_mapping = True`. Such a model indexed its primary key and nothing else
  while reporting success everywhere, so its search has never worked — but the check is new, and it
  stops startup. Two one-line fixes are in the hint; `'snapadmin.E026'` in `SILENCED_SYSTEM_CHECKS`
  is the documented way out.
- The `[elasticsearch]` extra is capped at `>=8,<9` and no longer resolves a 9.x client. If one is
  already installed, `pip install "elasticsearch<9"`. A 9.x client never worked against these code
  paths anyway.
- `snapadmin.E025` fails `manage.py check` on a misresolved `EXTRA_SETTINGS_ADMIN_APP` — reachable
  only with the `[extra-settings]` extra installed and admin autodiscovery deferred; the default
  `AdminConfig` already aborted startup earlier than any check.
- A `django.core.exceptions.ValidationError` on an API write now answers `400` instead of `500`, in
  the body shape a client already parses for a serializer error. A project with its own DRF
  `EXCEPTION_HANDLER` keeps first refusal and is unaffected.
- Deleting rows of an Elasticsearch-mirrored model no longer uses Django's fast-delete path: keeping
  the mirror honest on a bulk `QuerySet.delete()` needs a `post_delete` receiver, which costs one
  `es.delete()` per row. For large deletes use `SnapModel.delete_pks_from_es(pks)`, optionally
  inside `snapadmin.models.suppress_es_delete_receiver()`. `DB_ONLY` models are unaffected.

Everything else this cycle is additive and inert until configured.

### Added
- `data_retention_date_field` gives a model a per-row deletion date instead of one model-wide age:
  name a `Date`/`DateTimeField` and each row expires on the date it carries. It combines with
  `data_retention_days` — a row past its own date goes whatever the window says, a row whose date
  is still ahead of it is kept even when it is older than the window, and a `NULL` date falls back
  to the window (never purged when no window is set). Set it alone for a table where every row
  carries its own deadline: the purge task, the management command and `snapadmin.W012` all count
  that as configured retention. `ES_ONLY` models get the same rule as a query.
- `api_full_clean` runs a model's own `full_clean()` on the API write path, so a
  `Model.clean()` cross-field rule holds for API clients as it already did in the admin.
  Off by default; `SNAPADMIN_API_FULL_CLEAN` turns it on project-wide and a model's own
  attribute still wins. Validation is scoped to writable fields, uniqueness is left to
  DRF's own validators, and a `PATCH` is checked against the merged row.
- Field-level encryption now has a cipher: AES-256-GCM behind the new optional `[encryption]`
  extra (`cryptography`), storing each value as a self-describing, rotation-ready
  `snap1.<key id>.<nonce>.<ciphertext>` envelope bound to its own `app_label.model.field`. A
  dropped key, a tampered payload or a ciphertext moved between columns fails loudly instead of
  returning a wrong value; no key material or plaintext reaches an error message or a log.
- Eight encrypted field types — `SnapEncryptedCharField`, `…TextField`, `…EmailField`,
  `…JSONField`, `…IntegerField`, `…DecimalField`, `…DateField`, `…DateTimeField` — storing
  ciphertext in a text column while the form, the validation and the Python value stay those of
  the plain field. `None` stays SQL `NULL`; re-saving, `bulk_update()`, `QuerySet.update()` and a
  fixture reload never encrypt a value twice; `dumpdata` emits ciphertext, not plaintext.
- Encrypted fields refuse every lookup they cannot answer honestly with a `FieldError` naming the
  field — the alternative being a query that silently matches nothing. `blind_index=True` adds an
  HMAC sibling column that restores `__exact` and `__in` (and `unique=True`), matching under every
  key in the keyset so a rotation needs no rebuild. It makes equality observable by design; see the
  release notes before turning it on for a low-entropy column.
- Encrypted values no longer reach the surfaces that would re-emit them: excluded from
  Elasticsearch documents, redacted in the audit trail and the admin history, and masked by default
  in exports, REST, GraphQL, the changelist and imports through the existing PII permission model.
  Declarations the column cannot honour (`searchable`/`unique` without a blind index, `Meta.ordering`
  on ciphertext, a field named in `es_mapping`) are startup errors `snapadmin.E020`–`E023`, with
  `W019`/`W020` for a dead index and an orphaned blind-index column.
- The demo now encrypts `CustomerProfile.tax_id` with a blind index, so the feature is visible in a
  project you can actually run.
- New `manage.py snapadmin_encrypt_fields`: `--adopt` encrypts rows that predate the switch to an
  encrypted column, `--rotate` moves rows off an old key so it can be dropped, `--reindex` rebuilds
  blind-index columns after a `bulk_update()`/`QuerySet.update()`. Reports only unless `--apply`,
  resumable by primary key, and a row it cannot convert is counted and skipped rather than aborting
  the run.
- `SNAPADMIN_BACKUP_ALIGN_TO_SCHEDULE` (default `False`) measures each backup destination's due
  window from its *planned* slot rather than its last actual run. Off, every run books the next one
  a full interval after it finished, so a daily backup creeps forward by however long each run takes
  and over a month walks out of the quiet hours it was scheduled for. On, the schedule is pinned to
  the clock time of the first run, and slots missed while the process was down collapse into a single
  catch-up run instead of a burst. The existing drift-tolerant behaviour stays the default, the state
  file needs no surgery to turn it on, and turning it back off is equally safe.
- New system check `snapadmin.E025`: `EXTRA_SETTINGS_ADMIN_APP` (the `[extra-settings]` extra) set
  to an app *label* when django-extra-settings matches it against `INSTALLED_APPS` verbatim. The
  upstream error quotes the label back at you, so a package-nested app (`"myapps.shop"`, label
  `shop`) looks like a missing app instead of a wrong identifier; the check names the
  `INSTALLED_APPS` entry to write instead. It is reached wherever admin autodiscovery is deferred
  (`SimpleAdminConfig`, a custom `AdminSite`, no `django.contrib.admin`) — with Django's default
  `AdminConfig` the upstream error still aborts `django.setup()` before any check runs.
- New system check `snapadmin.W021`: a backup destination that leaves the host (`network`, `remote`,
  `sftp`, `s3`) is active while `SNAPADMIN_BACKUP_AGE_RECIPIENTS` is empty, so the database dump
  itself travels in plain gzip. `snapadmin.E007` only ever covered the `.env` part of the bundle.
  The warning names the offending destinations and the setting that fixes them; `local` is excluded
  because the dump never leaves the machine. A warning rather than an error on purpose — the
  transport or the destination may already encrypt, and neither is visible from `settings.py`.
- New `SNAPADMIN_BACKUP_SFTP_KNOWN_HOSTS` names the host-key file for the `sftp` destination
  outright. Unset, paramiko reads `~/.ssh/known_hosts` expanded against the `HOME` of whoever runs
  the process and ignores its absence — so in a container, where `docker exec` without a `USER` line
  is root, a `known_hosts` baked into the image at `/home/<svc>/.ssh/` is never read and the
  rejection blames a missing host key instead of the lookup path. Set, it replaces that lookup
  rather than adding to it (as OpenSSH's `UserKnownHostsFile` does), and an unreadable file fails
  loudly naming the setting. Backup and restore resolve it the same way; unset keeps the old
  behaviour exactly.

### Changed
- `snapadmin_info --section features` reports the number of fields actually encrypted alongside the
  keyset source and fingerprint — a configured key with nothing encrypted is a real state, and the
  adoption audit has to tell the two apart.
- The field-encryption documentation is split into the field types (`#field-encryption`) and the
  keyset (`#encryption-keys`); `SECURITY.md` gains the field layer's threat model.
- New check `snapadmin.E026`: a model indexed in Elasticsearch (`DUAL`, `ES_ONLY`, or
  `es_index_enabled = True`) with neither `es_mapping` nor `es_auto_mapping = True` now fails
  `manage.py check`. Such a model indexed its primary key and nothing else while the index
  creation, every save and `es_reindex_all()` all reported success — only the search came back
  empty, with nothing in a log to explain it. `searchable=True` never built the mapping, though
  the documentation said it did; that wording and the mapping-less `DUAL` examples are corrected.
  `es_auto_mapping` stays off by default on purpose: flipping it would start shipping every
  concrete column of every mirrored model to a second datastore on upgrade. Existing projects in
  this state will now fail at startup with two one-line fixes in the hint, or can silence
  `snapadmin.E026` if an id-only index is deliberate.
- The README's `Quality & compatibility` section now describes the testing method rather than only
  its size: the layers in use with named example files, the working rules behind them (test-first,
  a regression test per fixed bug, assertions that state a contract, a guard suite that fails on an
  assertion no outcome could falsify), random-order runs on every invocation, and the CI job that
  runs the whole suite against a real PostgreSQL 16 plus a marker-gated suite against a live
  Elasticsearch 8.13.0 — because a mocked client cannot reject the malformed query a cluster
  answers `400` to. A new subsection lists what is deliberately **not** in place yet: mutation
  testing, property-based/fuzz testing, browser E2E, and lint/type/security static analysis in CI.
  Branch coverage is reported as measured (99%) rather than gated; the enforced 100% is **line**
  coverage. Every count is a floor from a real collection run — 4,900+ tests across 152 files,
  replacing stale 4,600+ figures.
- The documentation site gains a `Testing & Quality Engineering` section (sidebar link included,
  cross-linked from Ecosystem Compatibility) explaining how each quality check actually works — what
  a mutant is and what a surviving one means, why branch coverage is reported rather than enforced,
  how random ordering surfaces an order-dependent test and how to reproduce one from its seed, how
  the AST contract backstop works, and what a contributor does when each check fails. `llms.txt`
  (both copies) links it and states the methodology set, the enforced **line**-coverage gate and the
  absent layers as a Key fact.
- New `tests/test_testing_docs_truth.py` keeps all of that honest: every test file named in the docs
  must exist, every quoted count must be a floor a real collection run still meets, no four-figure
  test claim anywhere may exceed what is collected, every check the docs say runs must be wired up,
  and — the direction that actually rots — every check they call absent must still be absent, so
  adding Ruff or mutation testing to CI fails the build until the pages are updated with it.

### Fixed
- A flaky assertion in the encrypted-field leak check: it searched the stored envelope for the
  plaintext `42`, and the envelope is random base64 on every write, so a two-character needle matched
  by chance in about 1.1% of runs. The encryption was never at fault. The sample value is now ten
  digits, and three new guards keep every leak-check needle long enough to be evidence, pointed at a
  genuinely encrypted column, and actually present in the plaintext it claims to detect.
- The README claimed the shipped package carries no `# pragma: no cover`; it carries eighteen,
  across twelve modules. Both documents now state the number and the four groups they fall into,
  name the five defensive guards as the group worth revisiting, and the two pragmas that carried no
  reason now have one. A guard test freezes the list as a ceiling rather than banning what was
  already there.
- Five import-job strings shipped untranslated in all nine translated locales. `Fail`,
  `Report Resume Byte Offset`, `Byte length of the report file confirmed as written.`,
  `Import Job` and `Import Jobs` were left flagged `#, fuzzy` after a catalog regeneration,
  and `msgfmt` drops a fuzzy entry — so the admin rendered them in English everywhere while
  the `.po` files carried a plausible-looking wrong translation (`Import Job` held the
  translation of `Export Job` in every locale). All forty-five entries are now reviewed and
  compiled, and a test fails the suite on any future fuzzy entry.
- A model-level validation rule that rejects an API write now answers `400` naming the
  field instead of escaping as an HTML `500`. Anything raising a Django `ValidationError`
  on a write path — `Model.clean()`, `full_clean()`, a `save()` guard — is translated on
  every SnapAdmin endpoint with no configuration; a project's own `EXCEPTION_HANDLER`
  still sees the exception first. `snapadmin.api.exceptions.snap_exception_handler` is the
  same translation as a drop-in handler for a project's own views.
- `collectstatic` no longer fails on a manifest static-files backend. The vendored Chart.js bundle
  ended with a `sourceMappingURL` comment pointing at a `.map` file the package does not ship, so
  `ManifestStaticFilesStorage` — including whitenoise's `CompressedManifestStaticFilesStorage`, the
  usual production choice — aborted post-processing with a missing-file error and took the image
  build or deploy down with it. Neither `WHITENOISE_MANIFEST_STRICT = False` nor a
  `manifest_strict = False` subclass worked around it, because the file really was absent. The
  comment is gone from the shipped bundle.
- The upgrade guides no longer claim the REST and GraphQL surfaces are on by default. The b7→b8
  guide told the reader in section 1 that `SNAPADMIN_REST_API_ENABLED` and
  `SNAPADMIN_GRAPHQL_ENABLED` "both still default to `True`" and in section 2 that they now default
  to `False`; two older guides shipped `# default True` next to the same settings. Anyone following
  section 1 installed the extras, expected their API to survive the upgrade, and lost it on the next
  boot. All three are corrected, and the guides now spell out that restoring a surface takes both
  the extra and the setting.
- `editable=False` no longer generates a migration. It is the one Snap field kwarg that is also a
  Django one — passed through on purpose, so the restriction holds in a hand-written `ModelForm` or
  DRF serializer and not only in the generated admin — but `Field.deconstruct()` reported it, so
  putting it on 24 fields produced 24 `AlterField` operations that `sqlmigrate` renders as `(no-op)`
  and a red `makemigrations --check` in CI, against the `SnapField` docstring's explicit promise.
  `deconstruct()` now drops it on both sides of every comparison, so a project that already
  generated such a migration needs no action: nothing further is detected. `snap_field()` had the
  same leak and is fixed the same way, while an `editable=` passed to the wrapped Django field's own
  constructor is still reported, because there it really is the Django kwarg.
- `snapadmin.W015` no longer warns about a model whose admin you wrote yourself. It decided a model
  would render an empty change form from the SnapAdmin registration alone, never asking whether
  SnapAdmin built the admin actually serving it — so a model registered with `@admin.register` and a
  hand-written `ModelAdmin` (its own `fields`, its own `readonly_fields`) was warned about even
  though its form is complete. It now consults the live admin registry, across every `AdminSite`
  rather than only the default one, and skips a model whose registered `ModelAdmin` SnapAdmin did
  not generate. A model registered nowhere still warns, so no genuine case is lost.
- The `[elasticsearch]` extra is capped to the 8.x client (`>=8,<9`); it was unbounded, so a fresh
  install pulled the 9.x client against the 8.x server the scaffold and demo compose files start —
  which answers `BadRequestError(400)` to every call, leaving no indices, no search results and a
  degraded `/api/health/`. `demo/requirements.txt` is capped to match, and a test now reads the pin
  and both compose images from their own files so they cannot drift apart again.

- A backup run no longer ends on a `PermissionError` traceback when it cannot write its own state
  file. `_load_state()` had always treated an unreadable state file as "no state"; the save side had
  no such guard, so a state directory the process cannot write — a mis-owned Docker volume, in the
  report — replaced a cleanly logged storage failure with an unrelated crash out of
  `pathlib.Path.write_text`. The write failure is now logged as `backup_state_save_failed` and the
  run's real outcome stands.
- **A bulk `QuerySet.delete()` no longer leaves the Elasticsearch document behind.** `SnapModel.delete()`
  always cleared the mirror, but a bulk delete is one SQL `DELETE` that never calls it — and neither is
  a row removed by an `on_delete=CASCADE` sweep — so the index kept returning rows that no longer
  existed. A `post_delete` receiver is now connected at startup for exactly the registered models that
  mirror to ES (`es_storage_mode` other than `DB_ONLY`, or `es_index_enabled`); a `DB_ONLY` model gets
  no receiver and is unaffected. On a mirrored model this costs one `es.delete()` per deleted row and
  opts the model out of Django's fast-delete path. For a large delete use the bulk path instead:
  `SnapModel.delete_pks_from_es(pks)` is now public (the private `_delete_pks_from_es` still works) and
  clears any number of documents with one `delete_by_query` — run the delete inside the new
  `snapadmin.models.suppress_es_delete_receiver()` context manager so the per-row receiver does not
  repeat the work, as the retention purge and `snapadmin.etl.stale_sync()` now do. An ES outage logs `es_delete_document_failed` and never breaks the
  database delete.
- `manage.py check` no longer dies with `KeyError: 's3'` when an S3 backup destination is
  configured. The `snapadmin.W010` cadence check kept its own copy of the destination-to-interval
  table and never learned about `s3`, so a project with `SNAPADMIN_BACKUP_S3_BUCKET` set and a Beat
  entry for `snapadmin.run_db_backups` lost `check`, `migrate` and `runserver` to a traceback that
  named no setting. The intervals now come from the same table the backup run itself uses, and
  `s3`'s interval is finally counted when judging whether Beat runs often enough.
- A refused SFTP upload now names the path it was writing to. `SNAPADMIN_BACKUP_SFTP_DIR` is
  relative to the SSH login directory, which was documented nowhere; on an account restricted to a
  subtree the directory change appears to succeed and the upload is what fails, with paramiko's bare
  `Failure` and no path — so it reads as a credentials problem rather than a wrong directory. The
  failure now carries the full intended path and the login-relative rule, and an absolute value is
  `snapadmin.W022` at `manage.py check` (the default `/` is not flagged). A relative directory also
  built a malformed location string, `sftp://host:22backups/dump.gz`, with the separator eaten by
  the `rstrip('/')` that exists for the default — the path is now built once and used for both the
  reported location and the failure message.
- The b7→b8 upgrade guide now covers `show_in_form` and the arity of `SnapModel.get_admin_fields()`.
  It never mentioned that `show_in_form` decides what appears on the generated change form and
  defaults to `False`, so a finished migration produced a normal changelist and an empty form with
  nothing to explain it; the guide now names the flag, the project-wide
  `SNAPADMIN_SHOW_IN_FORM_DEFAULT` default, and `snapadmin.W015` as the way to find affected models
  before anyone opens the admin. It also states that `get_admin_fields()` returns five values —
  unpacking four fails admin autodiscovery outright.


## 0.1.0b8 — 2026-09-06

### Breaking
- **`SNAPADMIN_PROFILE = "full"` / `"api"` now mount REST, GraphQL and Swagger.** Originally, both
  presets were empty and fell through to the freshly-flipped `False` defaults below, so a project
  setting either was running admin-only. Restoring the documented meaning widens the HTTP surface on
  upgrade with no settings edit — and `api_write_fields` is unset by default, so previously
  unexposed models become writable. Audit write allowlists and masking first, or pin
  `SNAPADMIN_REST_API_ENABLED = False` / `SNAPADMIN_GRAPHQL_ENABLED = False` (explicit always beats
  a profile), or drop `SNAPADMIN_PROFILE` altogether.

- The shipped `admin.js`'s select2 auto-init is now **opt-in**: only a `<select>` carrying a
  `snapadmin-select2` class (or `data-snapadmin-select2` attribute) gets initialised, not every
  `<select>` on the page. The old broad selector reached the changelist's own action dropdown and
  silently broke bulk actions on a theme that binds it through Alpine. Add the class to a field's
  widget to opt it back in.

- `SNAPADMIN_CONNECTIVITY_ENABLED` now defaults to `False` (previously always on): the admin-wide
  health poll, save-blocking guard and sidebar sync badge no longer load unless explicitly enabled
  *and* at least one registered model has `offline_mode = True`. A deployment with
  `SNAPADMIN_REST_API_ENABLED = False` used to poll a 404ing `/api/health/` forever and block every
  Save button — set `SNAPADMIN_CONNECTIVITY_ENABLED = True` to restore the previous behaviour.

- `SNAPADMIN_REST_API_ENABLED` / `SNAPADMIN_GRAPHQL_ENABLED` now default to `False` (previously
  `True`) — a project including `snapadmin.urls` no longer gets a writable REST/GraphQL surface
  for every registered model without asking for one. Pin either to `True` to restore the previous
  behaviour. See the migration guide.

- `djangorestframework`, `drf-spectacular`, `django-filter` and `graphene-django` are no longer
  core dependencies — they moved behind two new extras, `[api]` and `[graphql]`. A bare
  `pip install django-snapadmin` now pulls only Django, structlog and nh3; both features above
  default to `False`, so most installs only need the matching extra once a surface is turned on:
  `pip install django-snapadmin[api,graphql]` (or `[all]`, a no-op upgrade for an install that
  already has everything). New check `snapadmin.E010` catches a feature left on with its extra
  missing. See the migration guide.

- Retro-note (this heading is new): `SnapModel.get_admin_fields()`'s return arity silently grew
  from four values to five in an earlier pre-1.0 release with no changelog entry — now pinned so
  it cannot shift silently again. `django-admin-rangefilter` stopped being a dependency in
  `0.1.0b6`, already documented there under `Removed` — see that entry rather than duplicating it.

- `snapadmin.backup.run_backup()` / `run_due_backups()`, and the `purge_expired_data` /
  `send_error_digest` / `run_es_reindex` / `send_health_alert` Celery tasks, now **raise** instead
  of returning a dict when every unit of work failed (previously reported as a normal, if
  unhelpful, return value). Code calling these directly and branching on the return value for a
  total-failure case must now catch the exception instead (`BackupError`, `SnapPurgeError`,
  `AlertDeliveryError`, `ReindexError`) — see the task-outcome convention under Fixed, below.

- `snapadmin.purge_expired_data` / `snapadmin_purge_expired_data` now also purge the audit log
  (`SnapadminAuditLog`) automatically, against `SNAPADMIN_AUDIT_RETENTION_DAYS` — a setting that
  already documented a 365-day default but, until now, was only ever read by
  `snapadmin_audit_export --purge`. A project that already schedules `purge_expired_data` via Celery
  Beat and has audit rows older than 365 days will see them deleted on the first run after
  upgrading. Set `SNAPADMIN_AUDIT_RETENTION_DAYS = 0` to keep every audit row indefinitely, as
  before.

- **Every registered model must now declare `subject_path`.** New check `snapadmin.E011` fails
  `manage.py check` for **any** registered `SnapModel`/`@snap_model` model that never declares
  `subject_path` at all — `subject_path = None` is a valid, explicit answer ("this model carries
  nothing reachable from a GDPR data subject"), but silence is not. This is unconditional and not
  behind a feature flag: it is the declaration the new `snapadmin_subject_request` export/deletion
  command depends on to know what it can safely reach. Every existing registered model in every
  project needs one line added (`subject_path = None`, or a real path for a model that does carry
  personal data) before `manage.py check` passes again after upgrading. See the migration guide.

### Added
- Key management for field-level encryption: `SNAPADMIN_ENCRYPTION` configures one ordered keyset,
  resolved from a `KEY_PROVIDER` (KMS/Vault), a mounted `KEY_FILE`, the `SNAPADMIN_ENCRYPTION_KEYS`
  environment variable or the settings module — most secure first, first hit wins, never merged.
  `manage.py snapadmin_encryption_key [--rotate]` generates a key and prints it once. Key material
  is never written to a log, a `repr` or an exception; only a key id and a fingerprint. Reusing
  Django's `SECRET_KEY` is refused (`snapadmin.E017`), an encrypted field without a keyset stops
  startup (`snapadmin.E018`), and `snapadmin_info --section features` reports the key source and
  keyset fingerprint. Nothing is read or imported until a model declares an encrypted field; the
  field types themselves ship next.

- Declarative database sharding and read-replica routing: `SNAPADMIN_SHARDING = {"ENABLED": True,
  ...}` configures any number of shards/replicas (an auto-sliced flat `DATABASES` list, or an
  explicit `SHARDS` mapping), and `SnapAdminRouter` routes reads/writes by `modulo`/`hash`/`range`/
  a custom function, with primary failover and replica fallback. A model opts in with `shard_key`
  (mirrors `tenant_scoped`); `snap_master_only()`/`snap_target(...)` force routing per block, sync or
  `async def`. `manage.py snap_migrate` migrates every shard's primary; `manage.py
  snapadmin_db_backup` now backs up every shard's primary too, never a replica. `STRATEGY` defaults
  to `modulo`, which needs an integer shard key — shard by a string or UUID key with
  `STRATEGY = "hash"`, or the router raises `ShardResolutionError` naming the field (never the
  value, which is often a natural key such as an email address). `HA_SETTINGS['AUTO_FAILOVER']`
  (default `False`) fails writes over to a live replica when the primary is down: enable it only
  against a replica that can genuinely be promoted, since a read-only standby rejects the write
  anyway and one that accepts it diverges from the primary. `snapadmin_info --section features`
  reports the shard and replica counts and the active strategy. Unset/disabled, this is a complete
  no-op for an existing single-database project.

- `manage.py snapadmin_age_keygen` generates an AGE keypair for
  `SNAPADMIN_BACKUP_AGE_RECIPIENTS` directly from the library, writing it to a git-ignored `.age/`
  directory — the private key is never printed, and the command adds a `.gitignore` rule (checking
  for one first, including broad patterns like a bare `.*`) rather than trusting one already exists.

- `snap_field()` now accepts every `Snap*Field` constructor kwarg — `required` and the file-upload
  trio (`allowed_extensions`/`allowed_encodings`/`max_size_bytes`) are no longer refused.

- `SNAPADMIN_PROFILE = "admin" | "api" | "full"` picks sane defaults for the handful of settings
  that actually differ by use case, instead of deciding all ~90 individually. Unset (or `"full"`)
  changes nothing — every existing install keeps its current behaviour.

- `snapadmin_info`'s `inventory` section now reports, per model, which registration door it came
  through (`SnapModel` subclass vs. `@snap_model` decorator) and which capabilities that door
  leaves inactive (Elasticsearch mirroring, retention purge, generated admin).

- `@snap_property` decorates a method into a computed, display-only admin column — the decorator
  form of `SnapFunctionField` (no database column, no migration). Works identically on a
  `SnapModel` subclass and on a `@snap_model`-decorated plain model.

- `get_model_meta()` gains a third precedence tier: a project-wide `SNAPADMIN_<NAME>` setting,
  consulted between the class attribute and the caller's built-in default. Only reachable on the
  `@snap_model` route — a `SnapModel` subclass always has a class attribute to answer from.

- A runnable integration checklist (Must work / Should be configured / Data safety /
  Optional-scale), documented at `#integration-checklist` and now printed by `snapadmin-init`
  itself — every row is ✅/❌/⚠️, never a false green for anything it can't check without a live
  project.

- Database backups can be encrypted in-stream with AGE (`SNAPADMIN_BACKUP_AGE_RECIPIENTS`) — any
  one of N configured recipients decrypts a bundle independently. Two interchangeable backends
  (`pyrage`, the new optional `[age]` extra, or the `age` CLI). Empty (the default) changes nothing.

- `SNAPADMIN_BACKUP_INCLUDE` bundles media and an encrypted `.env` alongside the database backup
  (default `["db"]`, opt-in). Every run now also writes an unencrypted `manifest.json` sidecar;
  retention (`SNAPADMIN_BACKUP_KEEP`) applies per part.

- `manage.py snapadmin_restore` restores a backup bundle — dry-run by default, `--confirm` to
  perform it. Verifies the manifest checksum before touching anything, supports `--only`/`--skip`
  part selection, fetches straight from a configured destination (`<destination>:<name>`), and
  prints exactly which identity an encrypted bundle needs.

- `manage.py snapadmin_rollback` undoes a restore: `snapadmin_restore --confirm` automatically
  snapshots the current live state before touching anything (aborting the restore if the snapshot
  itself fails), and `snapadmin_rollback [<id>]` restores it back. Its own short retention
  (`SNAPADMIN_RESTORE_SNAPSHOT_KEEP`, default 3) is separate from `SNAPADMIN_BACKUP_KEEP`.

- A fifth backup destination: any S3-compatible object store (`SNAPADMIN_BACKUP_S3_*`, the new
  optional `[s3]` extra, `boto3`) — AWS S3, MinIO, Backblaze B2, Hetzner Object Storage or Wasabi via
  `SNAPADMIN_BACKUP_S3_ENDPOINT_URL`. Supports the ambient AWS credential chain when no explicit key
  is set. A worked SFTP recipe for Hetzner Storage Box (a different, non-S3 product) is now in the
  docs. New check `snapadmin.W011` flags an incompletely configured S3 destination.

- `snapadmin_info` gains a `backups` section (destinations, last run per destination, encryption
  status, recipient fingerprints); the `features` section's backup line now also names the active
  destinations and whether a restore has ever run.

- `SnapModel.get_admin_fields()` returns a pinned `AdminFieldSets` named tuple (`form_fields`,
  `list_display`, `search_fields`, `list_filter`, `autocomplete_fields`) instead of a bare 5-tuple —
  backward-compatible by construction, since positional unpacking, indexing and `len()` all keep
  working; a future sixth member stays a breaking change, just an announced one.

- `SnapModel.get_admin_media()` — the base admin `(js, css)` asset lists as a public, typed
  classmethod, so a project overriding `register_admin()` can extend the real lists instead of
  copying a snapshot that rots at the next release.

- `APIToken.allowed_scopes` (new field, one migration) plus `token_has_scope()` scope a token to a
  project's own endpoints, not just SnapAdmin's generated model routes — SnapAdmin only stores and
  matches the free-form strings, the meaning is the project's. Empty denies every scope check
  (fail-closed), unlike `allowed_models`.

- `POST /api/tokens/<id>/rotate/` (also `APIToken.rotate()`) replaces a token's secret in place —
  same row, id, scopes and history — and returns the new raw key once; the old key stops
  authenticating immediately. Written to the audit trail.

- `POST /api/tokens/<id>/deactivate/` flips `is_active` off without deleting the row — the
  documented revocation path. A regular user manages their own tokens (list, rotate, deactivate)
  without needing to be a superuser.

- `snapadmin_reindex --verify` compares the Elasticsearch document count against the source row
  count once a model's run finishes (discounting documents ES itself rejected, and skipped
  entirely for `ES_ONLY` models, which have no independent source to compare against) and exits
  non-zero on a mismatch — a run that reports success on the strength of its own loop counter no
  longer looks identical to one that quietly came up short. `--progress-interval` (default 5s)
  throttles the per-chunk progress line so a multi-hour run in a detached container doesn't fill
  the log with one line per chunk; the line reporting a model's completion, cancellation or
  failure always prints regardless of the throttle.

- `snapadmin.limits.reserve(key, windows, concurrency)` — a cache-backed quota primitive for
  per-tenant/per-token limits across several time windows at once, a concurrency cap, and an
  explicit `cooldown()` after an upstream 429, with no opinion about what `key` means (an inbound
  API guard and an outbound client call use it identically). Counters are per-process unless
  `SNAPADMIN_LIMITS_CACHE_ALIAS` points at a shared cache; an evicted counter fails open, never
  closed. Demonstrated in the demo project's `sync_exchange_rates --rate-limit N`.

- `SnapModel.data_retention_files` — a list of `SnapFileField`/`SnapImageField` names whose storage
  objects are deleted along with an expiring row, so a GDPR purge no longer leaves an orphaned file
  (unreachable, but undeletable without a separate storage sweep) behind. Files are deleted before
  the row; a storage failure raises `SnapPurgeError` and leaves the row intact for a retry, and a
  path another live row still references is skipped rather than deleted out from under it. Unset
  (the default) changes nothing.

- `SNAPADMIN_EXPORT_RETENTION_DAYS` — opt-in (unset by default) cleanup of finished
  `SnapExportJob`/`SnapReindexJob` rows and their published files past the window, plus a sweep for
  any export file left behind with no job row at all. Runs from the same
  `snapadmin.purge_expired_data` task/command as every other retention sweep.

- New check `snapadmin.W012`: retention is configured somewhere (a model's `data_retention_days`,
  the audit log's on-by-default window, or `SNAPADMIN_EXPORT_RETENTION_DAYS`) but no
  `CELERY_BEAT_SCHEDULE` entry runs `snapadmin.purge_expired_data` to actually enforce it.

- A single table in the docs (`#retention-table`) listing every table SnapAdmin can auto-delete,
  its setting, and its recommended schedule — the audit log, error events, export/reindex jobs,
  expired API tokens and model-level `data_retention_days` were previously documented separately,
  each looking automatic on its own with no way to see what the whole picture actually covers.

- `@snap_action` turns a model method into a user-defined REST action —
  `POST /api/models/<app_label>/<Model>/<pk>/<name>/` for a `detail=True` action (the default), or
  the list-level route with `detail=False`. Bound by the model's own `api_read_only`/
  `api_http_method_names` policy (a write action can never reach a read-only model) and a Django
  permission, derived from the action's methods or given explicitly. Discoverable per model via
  `GET /api/models/schema/`. New check `snapadmin.E008` catches an action whose methods conflict
  with its own model's CRUD policy at boot instead of at first request.

- `api_field_permissions` (model-level metadata, e.g. `{"salary": {"read": "hr.view_salary",
  "write": "hr.change_salary"}}`) gates a field's very presence in a REST/GraphQL response and
  rejects a denied write with a `400` naming the field — orthogonal to PII masking, which only
  controls whether an already-present field is raw or starred. Wired into REST (serializer +
  filter/ordering/search) and GraphQL this round; the admin form and export gain the same guard in
  a follow-up.

- `manage.py snapadmin_import` — CSV/NDJSON import, the write-side counterpart to async export,
  backed by a new `SnapImportJob` (one migration) mirroring the export job's architecture: header-name
  column mapping (plus an explicit `--map` override), a configurable natural-key duplicate rule,
  `--on-conflict fail|skip|update` (default `fail` — never a silent overwrite), validation through the
  model's own `full_clean()`, and a per-row NDJSON report plus a summary line. Crash-safe: every row's
  write, the job's counters and the report's confirmed byte length commit together per chunk, so
  `--resume` can never re-create a row an earlier attempt already committed. Write-surface rules
  (`api_write_fields`/`api_exclude_fields`/`api_read_only`/masking) are enforced up front, not as a
  follow-up.

- **GDPR subject-access requests** — `manage.py snapadmin_subject_request export|delete --model
  app.Model --identifier VALUE --user USERNAME` exports or deletes everything reachable from one data
  subject, across every registered model's own `subject_path` declaration (a forward `__`-joined ORM
  path, ≤3 relation hops, to the subject-identifying field, or `None`). `subject_path` is required on
  every registered model, no implicit default — new checks `snapadmin.E011` (never declared) and
  `snapadmin.E012` (declared but malformed: `is_data_subject=True` with no/mismatched
  `subject_identifier`, over the hop cap, unresolvable via forward relations, or a multi-hop path on
  an `ES_ONLY` model). `--user` must hold `snapadmin.view_raw_pii` — export is unmasked by design and
  reuses the existing async-export machinery; `--recipient` AGE-encrypts the finished bundle.
  Deletion is dry-run by default; both modes pre-flight through a Django deletion `Collector`, so a
  protected relation (`on_delete=PROTECT`) refuses the whole run up front instead of deleting in
  dependency order. `@snap_model()` also accepts `subject_path`/`is_data_subject`/
  `subject_identifier` as new keyword arguments.

- `POST /api/models/<app>/<Model>/fetch-by/` fetches a large explicit key set in one call —
  `{"field": "sku", "values": [...]}` streamed as NDJSON, the counterpart to `export`'s filtered
  streaming. `field` must be `unique=True` or `db_index=True` (`400` otherwise, naming the
  constraint); `values` is capped at `SNAPADMIN_FETCH_BY_MAX_VALUES` (default `10000`, `400` never a
  truncation over it — new check `snapadmin.W013` flags a ceiling raised so high it defeats the
  cap). Same permissions and masking as `export`; reachable via `POST` even on an `api_read_only`
  model, since it never writes. Not supported for `ES_ONLY` models (no DB column to index).

- The async surface: `asave`/`adelete`/`arefresh_from_db` on `SnapModel` (Django's own native async
  model methods since 5.2 — a test now pins that they reach `SnapModel`'s own `save()`/`delete()`
  overrides, including the Elasticsearch mirror and wysiwyg sanitize-on-write) and
  `aget`/`afirst`/`alast` on `EsManager`/`EsQuerySet`. Out of scope: async DRF ViewSets, an async
  Elasticsearch client, bulk async operations.

- Row-level multi-tenancy (`snapadmin.tenancy`): a model opts in with `tenant_scoped = True` plus a
  tenant column (`tenant_field()`), and every generated surface — admin, REST, GraphQL,
  Elasticsearch routing, async export/import jobs, the offline cache — then requires a bound tenant
  (`use_tenant()`, or the new `SnapTenantMiddleware` per request via the new
  `SNAPADMIN_TENANT_RESOLVER`/`SNAPADMIN_TENANT_USER_RESOLVER` settings) to see or write any row:
  default-deny, never "every row" with none bound. `use_all_tenants()` is the one explicit, audited
  bypass, reserved for the retention purge and the Elasticsearch reindex. New check
  `snapadmin.E009` flags a `tenant_scoped = True` declaration that cannot actually be enforced.
  Isolation is logical, not physical — `snapadmin.backup`'s database dumps run below the ORM and are
  not tenant-scoped at all, documented as plainly as the feature.

- `snapadmin_license_check` now reports how stale its curated licence map is (last-reviewed date,
  age in days) and warns loudly past 180 days unreviewed — also in the `--json` payload.

### Changed
- The shipped `admin.js`'s select2 initialisation is opt-in now — see Breaking, above, for the
  migration note.

- `SNAPADMIN_CONNECTIVITY_ENABLED` gates the admin-wide connectivity layer and now defaults to
  `False` — see Breaking, above.

### Fixed
- The `SNAPADMIN_REST_API_ENABLED` / `SNAPADMIN_GRAPHQL_ENABLED` default-off flip above had only
  reached `snapadmin/urls.py`. Ten other read sites still defaulted to `True`, so an install that
  set neither switch mounted no API while `manage.py check` failed with two `snapadmin.E010`
  errors demanding the `[api]`/`[graphql]` extras, `snapadmin_info --section features` reported
  both surfaces as adopted, and the dashboard linked to `/api/`. All read sites now share one
  default.

- `SNAPADMIN_PROFILE = "api"` turned REST, GraphQL and Swagger **off** instead of on: the `api` and
  `full` presets were empty and silently inverted when the built-in defaults flipped to `False` above. Every
  profile now states its values explicitly. `full` and "no profile" are no longer equivalent.

- A project generated by `snapadmin-new` could not start from its own `requirements.txt`: it lists
  the four API-stack apps in `INSTALLED_APPS` while requesting a bare `django-snapadmin`, which no
  longer ships them. The generated requirements now ask for `django-snapadmin[api,graphql]`.

- The demo project claimed in its README to exercise PII masking while shipping it switched off, and
  had no surface at all for `@snap_model` or `api_field_permissions`. All three are now really
  configured, and `snapadmin_info --section features` reports them on.

- The system dashboard linked `/api/` and `/api/graphql/` as literals, so both 404'd under
  `SNAPADMIN_URL_PREFIX` or a non-`/api/` mount point; they are reversed now, and a surface enabled
  without its URLconf included drops the link instead of raising.

- Removed fourteen dangling `issue #N` references from shipped comments and demo config (no public
  tracker exists, so each rendered as a broken link), and corrected two test docstrings that named
  modules renamed several releases ago.

- `demo/dist.env` now documents `SNAPADMIN_API_DELETE_GUARD`, the one env-driven demo setting it
  never named, and a test keeps that file in step with the demo settings module.

- `snapadmin_license_check`'s `--json` test asserted a value derived from the current date, turning
  the suite red the day after the curated licence table was reviewed. Command behaviour unchanged.

- A project's own `admin_overrides["get_readonly_fields"]` / `admin_overrides["safe_html_<field>"]`
  no longer get silently clobbered by the generator: the generated callables are merged onto the
  admin class before `admin_overrides`, never written into it, so a project's own override always
  wins regardless of write order. Previously a project's own `get_readonly_fields` could vanish,
  taking every change form on the site down with `FieldError: Unknown field(s)`, with nothing logged.

- Off `DEBUG`, the shipped media no longer downloads jQuery twice: the base admin JS now picks
  `jquery.js` / `jquery.min.js` the same way Django's own `ModelAdmin.media` does, so the two media
  lists collapse into a single entry on merge.

- The system dashboard's GitHub link pointed at the retired `drofji/snapadmin` (dead) instead of
  `drofji/django-snapadmin`.

- **A scheduled task that did nothing, or half-failed, no longer looks like a success.** All six
  Celery tasks (`run_db_backups`, `purge_expired_data`, `purge_expired_tokens`, `send_error_digest`,
  `run_es_reindex`, `send_health_alert`) now return a `status` key — `"ok"` / `"partial"` /
  `"noop"` / `"disabled"` — and a `failed` list alongside every existing key (purely additive), and
  **raise** instead of returning when every unit of work failed. One monitoring rule now covers all
  six: alert when `status != "ok"`, page when the Celery task state is `FAILURE`. Fixes the reported
  incident where a disabled backup schedule ran "successfully" for weeks with no backup ever taken,
  and a silently-failing offsite destination never surfaced anywhere but a log line.

- `run_db_backups`'s due-time check (`_is_due()`) no longer skips a day when a run completes even
  slightly later than the previous day's ideal slot — a small tolerance (2% of the destination's own
  interval) absorbs realistic scheduler jitter without materially changing when a backup actually
  runs. A new check, `snapadmin.W010`, warns when the Celery Beat entry for `run_db_backups` runs
  less often than the shortest configured `SNAPADMIN_BACKUP_*_EVERY_HOURS` — that combination
  silently drops days regardless of the tolerance above.

- `create_db_dump()` (and the AGE-encrypted path) now supports MySQL via `mysqldump`, alongside the
  existing PostgreSQL and SQLite support — the credential handled the same way as the PostgreSQL
  branch (`MYSQL_PWD` environment variable, never a command-line argument).

- The dynamic model API answers an unknown or unregistered model the same way on every action,
  including `retrieve`/`update`/`partial_update` — previously provided by DRF without an explicit
  guard, so they fell through to filtering an empty queryset instead of the consistent 404 body the
  other five actions already built for themselves. The check now runs once in `initial()`.

- `POST /api/exports/<id>/cancel/` now stamps `finished_at` alongside `status=cancelled`, matching
  completion and failure — a cancelled job can leave a real partial file on disk, and until now it
  was invisible to anything measuring a retention window on `finished_at` (including the new
  `SNAPADMIN_EXPORT_RETENTION_DAYS` purge).

### Removed
- **The deprecated command aliases and underscored console scripts are removed in this release.**
  This closes the beta-series removal window announced since `0.1.0b6` and reiterated in
  `SECURITY.md`'s API-stability policy: `db_backup`/`purge_expired_data`/`send_error_digest` (use
  `snapadmin_db_backup`/`snapadmin_purge_expired_data`/`snapadmin_send_error_digest`) and the
  underscored `snapadmin_info`/`snapadmin_license_check` console scripts (use the dashed spellings,
  or `manage.py snapadmin_info`/`manage.py snapadmin_license_check`, both unaffected) are gone —
  there is no runtime fallback. See the migration guide.

### Security
- `snap_field(field, wysiwyg=True)` now sanitizes on write, matching `SnapRichTextField` — closing
  a gap where the wrapper route stored raw HTML unsanitized.

- Backing up `env` with no `SNAPADMIN_BACKUP_AGE_RECIPIENTS` configured is refused fail-closed
  (system check `snapadmin.E007` plus a matching runtime guard) — a `.env` file's secrets are never
  written to a backup destination unencrypted.

- An unresolvable model on the dynamic API now denies every HTTP verb instead of falling back to
  full CRUD (`_resolve_http_method_names()`), and the 404 guard runs after authentication and
  permission checks — asserted by a dedicated test — so an anonymous probe cannot use a 404-vs-401
  difference to enumerate registered models without credentials.

## 0.1.0b7 — 2026-08-25

Two ways to declare a model instead of one, plus a full-project scaffolder and a batch of
operational features. No breaking changes, no required migration — every addition is opt-in.

### Breaking
- None.

### Added
- **`@snap_model`** opts a plain `django.db.models.Model` into SnapAdmin without subclassing —
  registers it and records the same settings a `SnapModel` subclass declares as class attributes.
  No field, no attribute, no migration. Deliberately does **not** attach `SnapModel`'s runtime
  machinery (Elasticsearch, retention purge, generated admin); the seven gates that used to
  hand-roll an `issubclass(model, SnapModel)` check now ask the registry instead.
- **`snapadmin.registry` is public API** — `is_registered()`, `meta_for()`, `register()`, and the
  new `get_model_meta()` every SnapAdmin surface now reads a model-level setting through.
- **`snap_field()`** sets SnapAdmin's field-level attributes (`searchable`, `filterable`,
  `wysiwyg`, …) directly on any Django field instance — the same interop path as `@snap_model`,
  one field at a time, for a third-party field package or a field that can't be rewritten.
- **`snapadmin-new`** scaffolds a project you keep — `manage.py`, settings, a worked `SnapModel`
  example, SQLite, `migrate` and `runserver` work immediately. `--full` adds Docker/Postgres/
  Redis/Elasticsearch.
- **`snapadmin-demo` stamps the tree it extracts** (`.snapadmin-demo.json`), so a re-run actually
  refreshes it — deleting files the new release dropped, keeping anything you added yourself.
- **Alert channels** — Slack, Discord, Teams, Telegram and JSON webhooks beside email, via
  `SNAPADMIN_ALERT_WEBHOOKS`. No new dependency; webhook URLs are treated as credentials.
- **XLSX exports** behind the optional `[xlsx]` extra — typed cells, formula-injection guarded;
  not resumable, unlike CSV/JSON.
- **The Unfold theme's own chrome is now translated** in all ten shipped locales — `django-unfold`
  ships no catalogs of its own.
- **A readable audit-log diff and a per-object timeline** — field-level before/after table instead
  of raw JSON, plus `/admin/.../timeline/<app>/<model>/<id>/` showing every change to one record.
- **`SNAPADMIN_MASKING_RULES`** — per-field masking pattern/replacement and a permission that
  unlocks one field without the blanket `view_raw_pii`.

### Fixed
- **`SnapStatusBadgeField` accepts its source field and choices positionally**, not just as
  keywords — the missing-argument error used to point at the wrong thing.
- **One failing `snapadmin_info` section no longer takes down the whole report** — isolated per
  collector, with credentials redacted from the error text.
- **`snapadmin.tasks` no longer requires Celery to import** — calling a task now runs it
  synchronously without Celery installed; queueing it raises naming the `[celery]` extra.

### Changed
- **The README is a landing page, not a manual** — problem statement, 60-second quickstart, an
  enterprise Q&A section; reference material moved behind collapsible sections.
- **Rich-text HTML is sanitized on write, not only on render** — covers every ORM write path.
  Lossy by design; `safe_html=True` or `auto_sanitize=False` opt out. `QuerySet.update()` is not
  covered.
- **`snapadmin_info` reports demo-tree drift** against the installed package version.
- **Audit-log diffs preserve JSON-native types** — numbers, booleans and `null` no longer
  collapse to strings.

### Deprecated
- **The removal window for every currently-deprecated alias is now fixed at `1.0`** — no
  behaviour change, just a date where "a future release" used to be.

### Security
- **The audit-log change form no longer renders the unmasked diff** to a viewer without PII
  access — the raw field is now excluded from the form outright.
- **Wysiwyg sanitization now fails closed if `nh3` cannot be imported**, instead of silently
  skipping sanitization. `nh3` is still a required dependency today; this is defense in depth
  ahead of a future release that makes it optional.

## 0.1.0b6 — 2026-08-13

A first-run polish release, from installing 0.1.0b5 into a fresh project and walking the demo.
No migration; no import path, setting or command name removed.

### Breaking
- `django-admin-rangefilter` is no longer a dependency — breaking only for code that imported it
  directly or listed it in `INSTALLED_APPS`; see `Removed` below.

### Added
- **`llms.txt`** — a machine-readable map of the documentation for AI coding assistants, in the
  [llmstxt.org](https://llmstxt.org/) format. Published at
  <https://drofji.github.io/django-snapadmin/llms.txt> and shipped in the source distribution.
- **A quickstart and module map in the `snapadmin` package docstring** — the three-step example,
  what every module and management command does, the `SNAPADMIN_*` setting families and the optional
  extras, available offline from any install via `help(snapadmin)`.
- **`snapadmin-info` and `snapadmin-license-check` as console scripts** — all four spellings
  (`snapadmin-info`, `snapadmin_info`, `python manage.py snapadmin_info`, likewise for the licence
  check) now work; the shim finds your `manage.py` and forwards arguments and exit code.
- **A copy-pasteable container health check** — `HEALTHCHECK` in the demo image and compose service,
  plus a docs section with the exact values for Docker, Compose, Coolify/Dokploy/Caprover and
  Kubernetes.
- **Remote S3-compatible storage in the demo** from one variable (`SNAPADMIN_STORAGE_BACKEND=s3`) —
  AWS S3, Hetzner Object Storage, MinIO and Backblaze B2, with signed URLs and no-overwrite defaults.
- **A `checks` section in `snapadmin_info`** — per-severity system-check counts, with `--health-check`
  failing on any error.
- **A written API-stability and compatibility policy** in `SECURITY.md` — what counts as public API,
  what does not, and the semver/deprecation rules that take effect at `1.0`.
- **A docstring on every name in the public contract**, with usage examples on the ones you type, and
  a test that fails when a public name is added without one.
- **README positioning against Unfold, Jazzmin and Grappelli** — those restyle the admin you write;
  SnapAdmin generates it (and the API, GraphQL and search) from the same field declarations, using
  Unfold as its optional theme.
- **An honest scale note in the README** — no benchmark numbers are quoted; it points at the demo's
  `seed_large` and `benchmark_list_view` commands so you measure on your own data.

### Changed
- **Every management command is `snapadmin_*`-prefixed.** `db_backup`, `purge_expired_data` and
  `send_error_digest` become `snapadmin_db_backup`, `snapadmin_purge_expired_data` and
  `snapadmin_send_error_digest` — generic names can silently collide with a command of your own.
  The old names keep working as deprecated aliases (rename notice on stderr, stdout untouched).
  Celery task names are unchanged.
- **`snapadmin_info` output is readable at a glance** — system checks no longer print above the
  report, uniform records render as an aligned table (model inventory: 55 lines → 13 for 11 models),
  and runs of booleans collapse to one `✓ on` / `✗ off` pair.
- **`snapadmin.W004`/`W007` emit one grouped warning** naming the affected models, instead of one
  near-identical block per model. Check ids are unchanged. W004 no longer fires for models that
  answer 405 to every write (`api_read_only`, or an `api_http_method_names` with no write verb).
- **Loading a model no longer imports the REST framework** — `SnapDynamicPagination` is built on
  first access, and `snapadmin.urls` imports DRF, drf-spectacular and graphene only inside the
  branches that need them. Groundwork: those packages are still dependencies of every install;
  moving them behind `[api]`/`[graphql]` extras is left to its own release.
- **A feature enabled without its dependencies raises an actionable error** naming both the packages
  to install and the setting to switch off, instead of an `ImportError` from inside a URLconf. A
  missing `graphene-django` with GraphQL enabled now raises instead of silently logging a warning.

### Removed
- **`colorama` is no longer a dependency** — nothing in the package imported it; the console colour
  is plain ANSI escapes.
- **`django-admin-rangefilter` is no longer a dependency** — it was installed for every user and the
  package never imported it (range filters come from `unfold.contrib.filters`, or Django's own list
  filters without the theme). Install it directly if your own admin code uses it.

### Fixed
- **`snapadmin_info --health-check` honours `SILENCED_SYSTEM_CHECKS`** — a silenced check used to
  keep it failing forever on a configuration `manage.py check` calls clean.
- **`SNAPADMIN_SWAGGER_ENABLED` follows `SNAPADMIN_REST_API_ENABLED` by default** — switching the
  REST API off left the OpenAPI views wired with nothing to document. An explicit setting still wins.
- **`--no-color` reaches the deprecated commands' rename notice**, so a piped log no longer collects
  ANSI escapes.
- **`GET /api/health/` reports `unhealthy` (503) when the database is down even if Elasticsearch is
  also unreachable.** The Elasticsearch branch could overwrite the status with the still-serving
  `degraded`, so probes kept routing to an instance that could not answer a query.
- **The dashboard chart renders in French** — a translated label containing an apostrophe broke the
  inline `<script>`, so no chart appeared.
- **The themed `User`/`Group` admin no longer depends on `INSTALLED_APPS` order**, which could
  silently skip the theming entirely.
- **The dashboard no longer crashes when a model sets `admin_enabled = False`** — one opted-out model
  took down the whole page with `NoReverseMatch`.
- **SnapAdmin's stylesheet no longer overrides the Unfold theme's own form layout** — the themed
  layer was scoped to a class current Unfold never emits, so it was dead while stock-admin layout
  rules applied everywhere. Styling now ships as one shared sheet plus exactly one theme layer.
- **The built-in `User` admin has a working password field under the theme again** — Unfold's
  templates were rendering against Django's stock forms, leaving the password row empty with no way
  to change it.

## 0.1.0b5 — 2026-07-24

Scale-hardening and operability. The production-scale Elasticsearch query layer is finished, the
auto-generated REST filters are richer and safer, `etl.stale_sync` scales past an in-memory key set,
async export sources become pluggable, and `snapadmin_info` gains a feature-adoption audit. Everything
is additive and backward-compatible; two additive migrations ship (a demo-only watermark column and
`SnapExportJob.source`).

- **Breaking:** none.
- **Added:** `SnapModel.es_count()` — exact match count of a structured ES query, past the search limit.
- **Added:** ES query methods accept `db_fallback=False` (+ `SNAPADMIN_ES_DB_FALLBACK`) to raise
  `SnapEsUnavailable` instead of silently falling back to the database.
- **Added:** `es_scan(source=False, limit=…)` streams primary keys of N-million matches; `snapadmin_reindex`
  gains `--limit` and a settable `--tune` default and fetches only ES-mapped columns.
- **Added:** REST filters gain `?field__isnull=` / `?field__in=` across text/numeric/date/FK, a swappable
  `SNAPADMIN_API_FILTER_BACKEND`, project/model-wide text-lookup defaults, and JSON comma-OR with a lazy
  native queryset + `SNAPADMIN_API_JSON_FILTER_SCAN_CAP`.
- **Added:** per-model `api_read_only` / `api_http_method_names` (write verbs answer 405; `snapadmin.W007`).
- **Added:** `etl.stale_sync` DB-side `strategy="last_seen"` and non-raising `on_exceed="skip"`.
- **Added:** pluggable async-export row sources (`SNAPADMIN_EXPORT_SOURCES` + `SnapExportJob.source`).
- **Added:** `snapadmin_info --section features` — a ✓/✗ commerce-readiness feature-adoption checklist.
- **Added:** lazy top-level re-exports (`from snapadmin import SnapModel, SnapCharField`) + a module map.
- **Fixed:** `AppConfig.ready()` no longer crashes when an optional package is importable but not in
  `INSTALLED_APPS`; text `?field__isnull=` no longer 500s; `es_reindex_all()` no longer risks OOM on MySQL.

## 0.1.0b4 — 2026-07-21

An operability, onboarding and decoupling release: four new operator/onboarding commands, a subsystem
health-alert email channel, Docker self-healing in the demo, and `django-unfold` made an optional
theme. No model, no migration; every existing import path, setting and signature is unchanged.

- **Breaking:** none.
- **Added:** `snapadmin_info` — one command reporting config, connected services and health
  (`--json`, `--section`, `--brief`/`--verbose`, `--health-check`); secrets never printed.
- **Added:** `snapadmin_license_check` — runtime licence audit with 🟢/🟡/🔴 tiers and a
  commercial-compatibility verdict (`--json`, `--critical-only`, `--compatible-with`, `--verbose`).
- **Added:** `snapadmin-demo` console script — stdlib-only bootstrapper that fetches, seeds and serves
  the demo with no existing project (wizard, save/load config, non-interactive CI flags).
- **Added:** `snapadmin-init` console script — read-only integration doctor that prints the exact
  `INSTALLED_APPS` / urls / settings / install snippets to paste, editing nothing.
- **Added:** subsystem health alerts — `snapadmin_health_alert` command and `snapadmin.send_health_alert`
  task email when a probe (database, Elasticsearch, REST API, GraphQL — each skipped when its feature is
  off) is down, with a cooldown. Recipients fall back to `SNAPADMIN_ERROR_ALERT_EMAILS`.
- **Added:** multi-version CI (`test.yml`, Python 3.10–3.13 × Django 5.2/6.0, 100% coverage gate) and a
  status badge; publish/release now gate on the matrix passing.
- **Added (demo):** a `willfarrell/autoheal` sidecar and a Celery worker healthcheck so containers that
  hang while unhealthy are restarted, not just ones that exit.
- **Changed:** `django-unfold` moved from a core dependency to a `[theme]` extra (kept in `[all]`); the
  admin falls back to Django's built-in theme when Unfold is absent (byte-identical when present). New
  `snapadmin.I001` info check surfaces the fallback.

## 0.1.0b3 — 2026-07-20

A large security and Elasticsearch release: ten security fixes, a structured Elasticsearch query
layer, and safer bulk imports. One breaking change to the auto-generated REST filters.

- **Breaking:** auto-generated REST filters now default text fields to **exact** match
  instead of substring. `?field=value` was `icontains` (a never-indexable leading-wildcard `LIKE`,
  and `?sku=123` also matched `sku=91234`); it is now an exact, index-usable match. Substring
  search moves to the explicit `?field__icontains=value`, alongside new `__startswith` and `__in`
  lookups. Set `api_filter_lookups` per model to restore the old behaviour for a given field.
  SFTP backups now verify the remote host key against `known_hosts` (pre-populate it before
  upgrading); the streaming export's `?limit=0` or negative now rejects with `400` instead of
  streaming everything.
- **Security:** GraphQL now enforces `view` permission and PII masking on **every relation a query
  traverses**, not just top-level fields, matching the REST contract.
- **Security:** new `api_write_fields` mass-assignment guard restricts which fields accept a
  client-supplied value on REST create/update; a system check (`snapadmin.W004`) flags models
  without one.
- **Security:** fixed an SSO provider open redirect, a fail-open in `SmartModelSelectorWidget`,
  `mask_value()` type handling, and loss of upload-validator config on `Snap*Field`.
- **Security:** export filters are restricted to the target model's own fields (a related-field
  path could previously reach columns the caller could not otherwise read); PII masking is now
  closed on export, the audit trail, and API filtering/ordering/search.
- **Security:** database backup path hardened, plus assorted deployment-topology fixes.
- **Added:** `es_filter()` (structured term filters in ES filter context), `es_aggregate()`
  (terms facets) and `es_scan()` (a `search_after` iterator streaming past the 10k
  `max_result_window`) — each falling back to an equivalent database query when ES is off.
- **Added:** `etl.stale_sync()` prunes rows whose natural key vanished from the latest source sync,
  refusing (via `StaleSyncAbort`) if that would delete more than `max_fraction` of the table — so a
  truncated feed cannot silently wipe it.
- **Added:** resumable, progress-tracking bulk reindex (`snapadmin_reindex` / `SnapReindexJob`), and
  the `SNAPADMIN_EXPORT_MAX_ROWS` / `SNAPADMIN_EXPORT_LIMIT_MAX` ceilings on the streaming export.
- **Added:** JSON key-path filtering for the REST API via `api_json_filters`.
- **Fixed:** translation catalogs refreshed — the admin UI is fully localised again in all 10
  locales; GDPR purge correctness (secondary-store failures, `retention_days=0`, inflated counts);
  API pagination and throttling now actually enforced; async export torn-write duplication,
  single-flight and OFFSET drift.
- **Changed:** the README is now a 252-line overview, with the reference material moved to the
  documentation site, which gains Internationalization and Environment Variables sections.

See [the full release notes](https://github.com/drofji/django-snapadmin/blob/main/docs/releases/0.1.0b3.txt) for more detail.

## 0.1.0b2 — 2026-07-13

- **Breaking:** none.
- **Security:** the generic dynamic model API (`/api/models/<app>/<model>/`) now only resolves
  `SnapModel` subclasses, mirroring the schema endpoint. Previously any registered Django model
  (e.g. `auth.User`) could be listed, retrieved, created, updated or deleted through it.
- **Fixed:** doc links in the installed `CHANGELOG.md` now use absolute GitHub URLs instead of
  relative paths that 404 outside a source checkout.

See [the full release notes](https://github.com/drofji/django-snapadmin/blob/main/docs/releases/0.1.0b2.txt) for more detail.

## 0.1.0b1 — 2026-07-08

First beta. Completes a downstream-integrator feedback pass, hardens the dashboard, and reorganises
optional dependencies so a base install is fully permissively licensed. Carries a few breaking changes.

> Upgrading from 0.1.0a11? A few changes need action (Celery task rename, dashboard gate, deps moved
> to extras) — see [the migration guide](https://github.com/drofji/django-snapadmin/blob/main/docs/migrations/0.1.0a11_to_0.1.0b1.md).

- **Breaking:** Celery tasks moved to `snapadmin/tasks.py` and renamed to the `snapadmin.*`
  namespace (from `api.tasks.*`) so `autodiscover_tasks()` finds them. Update every
  `CELERY_BEAT_SCHEDULE` entry and any imports; no back-compat aliases are kept. The dashboard is
  now staff-gated by default (see Security below); `django-admin-autocomplete-filter`, the wysiwyg
  editor and `django-extra-settings` moved behind optional extras (see Changed below).
- **Security:** the system dashboard is now staff-gated by default (it exposed hostname,
  processor, OS, database name, service health and `ALLOWED_HOSTS` to anonymous callers).
  Opt out with `SNAPADMIN_DASHBOARD_PUBLIC = True`.
- **Security:** wysiwyg field values are sanitized (via `nh3`, a new core dependency) before being
  rendered in the admin changelist; opt back into raw HTML per field with `safe_html=True`.
- **Added:** `SNAPADMIN_URL_PREFIX` relocates the entire route surface (REST, Swagger, GraphQL)
  under one extra path segment for projects that already own the mount point; route names are
  unchanged.
- **Added:** admin-only bulk ES reindex endpoint (`POST /api/es/reindex/`, gated), a deletion-veto
  hook for the dynamic model API, and synchronous `count` / streaming NDJSON `export` actions.
- **Changed:** `django-extra-settings` is now an optional extra (`django-snapadmin[extra-settings]`),
  not a forced core dependency — SnapAdmin's core never used it.
- **Changed:** the wysiwyg editor (`django-ckeditor-5`, which bundles GPL/commercial CKEditor 5) is now
  an optional `[wysiwyg]` extra, imported lazily — the base package stays permissively licensed for
  commercial use.
- **Changed:** `django-admin-autocomplete-filter` (LGPL, unused by the core) is now the optional
  `[autocomplete-filter]` extra — the base install is now **fully permissive** (MIT/BSD/Apache), no
  copyleft/commercial code by default.
- **Added:** a Python × Django compatibility matrix in the README and `Framework :: Django :: 6.0`
  / per-minor Python classifiers; the suite runs green on Django 6.0.
- **Fixed:** aggregations on SnapModels no longer return wrong grouped counts (default `-pk` ordering
  no longer leaks into `GROUP BY`); `upsert_from_source()` works on MySQL/MariaDB.
- **Fixed:** the dashboard shows the real installed version and loads no external assets
  (Chart.js + Material Icons vendored, Font Awesome dropped for an inline SVG).
- **Fixed:** `SnapPhoneField` accepts spaced international numbers (e.g. `+49 89 1234567`).
- **Fixed:** the demo seeder no longer crashes on a cp1252 Windows console.
- **Docs:** GraphQL field-naming scheme documented; per-model admin extension points
  (`admin_mixins` / `admin_overrides` / `css_admin_files` / `js_admin_files`) documented; migration
  guide install name and `/api/` collision handling corrected; a CHANGELOG now ships to pip users.

See [the full release notes](https://github.com/drofji/django-snapadmin/blob/main/docs/releases/0.1.0b1.txt) for more detail.

## 0.1.0a11 — 2026-07-05

Squashed the `snapadmin` and `demo` migrations (`0001`–`0006` each) into a single
`0001_initial.py` per app. No model or API changes.

**Breaking:** migration history reset — installs that already ran `migrate` on a prior alpha must
reset the recorded migration rows and fake-apply the new initial (drop/recreate the database also
works); see the migration guide.

## 0.1.0a10 — 2026-07-05

Housekeeping only: fixed a malformed `templates/admin/index...html` filename (the admin
dashboard override was silently ignored) and replaced a debug `print()` / swallowed exception
around GraphQL URL wiring with structured `structlog` logging.

**Breaking:** none.

## 0.1.0a9 — 2026-07-05

Enterprise backlog: immutable audit trail, asynchronous background export, large-dataset
pagination, full i18n (10 locales), WCAG 2.1 AA accessibility, an ecosystem-compatibility matrix,
configuration health checks and a migration guide.

**Breaking:** none.

## 0.1.0a8 — 2026-07-05

Config-driven enterprise features: read-replica routing, an SSO/OAuth2 login helper, PII masking
and nested-app grouping.

**Breaking:** none.

## 0.1.0a7 — 2026-07-04

SFTP offsite backups and a `[backup]` extra, automated PyPI publishing (tag → OIDC Trusted
Publishing), PyPI project URLs, and a docs split (package vs demo) with an Extending guide.

**Breaking:** none.

## 0.1.0a1 – 0.1.0a6

Initial alpha series: the declarative `SnapModel` + `Snap*` field types, auto-generated Unfold
admin, REST API with Swagger, dynamic GraphQL, Elasticsearch integration and smart `?search=`
routing, email error monitoring and 3-2-1 database backups. See the online release notes for
detail.
