# Third-Party Notices

**django-snapadmin** itself is licensed under the **MIT License** (see [`LICENSE`](LICENSE)).

This document lists the third-party code SnapAdmin **depends on** or **bundles**, with each item's
licence, so you can see at a glance what a base install pulls in versus what is opt-in. It exists
because SnapAdmin aims to keep the **base install fully usable for commercial / proprietary / in-house
business** use — the base carries only permissive licences (MIT / BSD / Apache-2.0), and anything
copyleft or commercially-restricted is an **optional extra** you choose to install.

> This is an informational summary, **not legal advice**. Licences can change between versions —
> verify against the versions you actually install (`pip show <pkg>` / each project's own `LICENSE`)
> and consult counsel for commercial use. Where a table says "bundles", note the licence of the
> *bundled* content, not just the Python wrapper.

## Legend

- 🟢 **Permissive** (MIT / BSD / Apache-2.0 / ISC): use freely, including in closed-source and
  commercial products; keep the copyright/licence notice.
- 🟡 **Weak copyleft** (LGPL): fine for proprietary use via normal Python import (dynamic linking) as
  long as the library is unmodified and remains replaceable; releasing modifications to *it* falls
  under its licence.
- 🔴 **Strong copyleft / commercial** (GPL, "GPL-or-commercial", etc.): obligations on distribution —
  kept out of the base install; opt-in only.

## Core runtime dependencies (installed by the base package)

Everything a plain `pip install django-snapadmin` pulls in. All permissive except one weak-copyleft
item, flagged below.

| Package | Licence | | Used for |
|---------|---------|---|----------|
| Django | BSD-3-Clause | 🟢 | The web framework SnapAdmin builds on |
| djangorestframework | BSD-3-Clause | 🟢 | REST API layer |
| drf-spectacular | BSD-3-Clause | 🟢 | OpenAPI schema + Swagger/ReDoc |
| django-filter | BSD-3-Clause | 🟢 | REST filtering backend |
| graphene-django | MIT | 🟢 | Dynamic GraphQL schema |
| django-unfold | MIT | 🟢 | Admin theme / UI |
| django-admin-rangefilter | MIT | 🟢 | Admin date-range filters |
| structlog | MIT or Apache-2.0 | 🟢 | Structured logging |
| colorama | BSD-3-Clause | 🟢 | Coloured console output |
| nh3 | MIT | 🟢 | HTML sanitisation (wysiwyg stored-XSS defence) |
| **django-admin-autocomplete-filter** | **LGPL-3.0** | 🟡 | Admin autocomplete filters |

> **The one weak-copyleft core dependency** is `django-admin-autocomplete-filter` (LGPL-3.0). Used
> unmodified as a normal pip dependency it is fine for commercial/proprietary use (dynamic linking);
> keep it replaceable and don't ship a modified fork closed-source. If your policy requires a
> strictly-permissive-only tree, it can be made an optional extra — open an issue/ask and it will be
> moved behind an extra like the others.

## Optional extras (installed only when you ask for them)

Nothing here is installed by a base `pip install`. Install via, e.g., `pip install django-snapadmin[celery]`.

| Extra | Package(s) | Licence | | Purpose |
|-------|------------|---------|---|---------|
| `elasticsearch` | elasticsearch | Apache-2.0 | 🟢 | Full-text search (`ES_ONLY` / `DUAL` models) |
| `celery` | celery, django-celery-beat, django-celery-results | BSD-3 / BSD / BSD | 🟢 | Background tasks (async export, GDPR purge, digests, backups) |
| `extra-settings` | django-extra-settings | MIT | 🟢 | In-admin dynamic key/value `Setting` model |
| `backup` | paramiko | **LGPL-2.1** | 🟡 | SFTP transport for offsite backups |
| `wysiwyg` | django-ckeditor-5 (BSD wrapper) **bundling CKEditor 5** | **GPL-2.0+ or commercial** | 🔴 | Rich-text fields (`SnapRichTextField` / `wysiwyg=True`) |

> **`wysiwyg` (CKEditor 5) is the one to watch for commercial use.** The Python wrapper
> `django-ckeditor-5` is BSD, but it ships **CKEditor 5**, which is dual-licensed **GPL-2.0+ or a
> commercial licence** (modern versions require a `licenseKey`). It is deliberately **not** a core
> dependency and is imported lazily; the base package never ships it. If you build a commercial product
> with rich-text editing, obtain a CKEditor licence (they offer a free tier) or provide your own
> widget. Using a `wysiwyg=True` field without the extra raises a clear `ImproperlyConfigured`.
>
> **`backup` (paramiko)** is LGPL-2.1 — the same weak-copyleft guidance as above applies, and it is
> already optional.

## Bundled front-end assets (shipped inside the package)

Vendored into `snapadmin/static/snapadmin/vendor/` so the admin dashboard renders with **no external
network requests** (air-gap friendly). Full licence texts ship alongside them.

| Asset | Licence | | Notes |
|-------|---------|---|-------|
| Chart.js (`chart.umd.min.js`, v4.4.1) | MIT | 🟢 | Dashboard charts. Text: `vendor/LICENSE-chartjs.txt` |
| Material Icons (`material-icons.css` + `.woff2`) | Apache-2.0 | 🟢 | Dashboard icons. Text: `vendor/LICENSE-material-icons.txt` |

Font Awesome was previously used for a single icon and has been **removed** (replaced by an inline
SVG), so its more restrictive split licence no longer applies.

See [`snapadmin/static/snapadmin/vendor/THIRD_PARTY_LICENSES.txt`](snapadmin/static/snapadmin/vendor/THIRD_PARTY_LICENSES.txt)
for the bundled attribution note and full licence files.

## Summary

- A base `pip install django-snapadmin` is **permissive** (MIT/BSD/Apache) except the single
  weak-copyleft LGPL admin-filter helper, which is safe for proprietary use as an unmodified dependency.
- **No GPL/commercial code is installed by default.** The only such item, CKEditor 5, is opt-in via
  `django-snapadmin[wysiwyg]`.
- Related policy: [`SECURITY.md`](SECURITY.md) → "Supply chain".
