# Migration Guides

Step-by-step guides for **hard migrations** — upgrades that require manual action beyond reading
`docs/releases/X.Y.Z.txt` (a data/schema reset, a renamed import root, a settings rewrite, dropping a
table). Most releases don't need one; SnapAdmin aims for backward compatibility on every change, so a
file only gets added here when that isn't possible.

## Naming convention

```
docs/migrations/<from>_to_<to>.md
```

- `<from>` / `<to>` are version numbers (`0.1.0a10_to_0.1.0a11.md`) or, for a cross-package migration,
  the PyPI distribution names (`drofji-automatically-django-admin_to_django-snapadmin.md`).
- Use the exact identifier that changed — a version range for same-package upgrades, a package name for
  package renames. Sorts naturally either way since both start with the "from" side.

## When to add one

Only when the change is genuinely breaking and needs manual steps: a migration-history reset, a
required `INSTALLED_APPS`/import rename, a settings key rename with no deprecation shim, or similar. If
the changelog entry in `docs/releases/Unreleased.txt` alone is enough to upgrade safely, don't create a
file here — that's the common case and the goal.

## Linking

Each guide gets a one-line pointer added to `docs/index.html`'s "Migration Guides" nav section
(anchor list only — full content lives here, not duplicated in the HTML) and a note in the relevant
`docs/releases/X.Y.Z.txt` entry ("See the migration guide: `docs/migrations/<file>.md`").
