# Ecosystem Compatibility

How `django-snapadmin` coexists with popular third-party Django packages (issue #1).

## The two rules that make SnapAdmin safe by default

1. **SnapAdmin only auto-registers `SnapModel` subclasses.** Every other model in your project —
   including third-party ones (`taggit.Tag`, `guardian`'s permission models, `reversion.Version`,
   `django_celery_beat`, …) — is left entirely alone. There is no global admin takeover.
2. **Auto-registration never clobbers an existing admin.** If a model is already registered (by you or
   a package) when SnapAdmin runs, it skips it (`AlreadyRegistered`). Your custom admin wins.

Two escape hatches let a package take over a `SnapModel`'s admin when you want it to:

| Goal | How |
| --- | --- |
| Add a package's admin behaviour **on top of** SnapAdmin's auto-config | `admin_mixins = [ThePackageAdminMixin]` on the `SnapModel` |
| Let a package **fully own** the admin for a model | `admin_enabled = False` on the `SnapModel`, then register the package admin yourself |

`admin_mixins` classes are placed **first in the MRO**, so their `get_queryset` / `changelist_view` /
actions wrap SnapAdmin's, which in turn wraps Django/Unfold's `ModelAdmin`.

## Package matrix

| Package | Works with SnapAdmin? | Notes / recommended integration |
| --- | --- | --- |
| **django-mptt** (tree structures) | ✅ | Multiple-inherit `MPTTModel` alongside `SnapModel`. For the tree UI in the admin, add `admin_mixins = [MPTTModelAdmin]`. Model fields still auto-generate. |
| **django-guardian** (object-level perms) | ✅ | Guardian's models are untouched (not `SnapModel`s). For per-object permissions in the admin, `admin_mixins = [GuardedModelAdmin]`. SnapAdmin's REST API honours standard Django `has_perm`, which guardian backends extend. |
| **django-reversion** (versioning) | ✅ | `admin_mixins = [reversion.admin.VersionAdmin]` to get version history on the auto-generated admin. Reversion's own models are separate. |
| **django-debug-toolbar** | ✅ | Purely middleware/URLs; no interaction with model or admin generation. Add its middleware as usual. |
| **django-import-export** | ✅ | `admin_mixins = [ImportExportModelAdmin]` layers import/export buttons onto the auto-generated changelist. (SnapAdmin also ships its own async export API — issue #6.) |
| **django-simple-history** (history) | ✅ | Add `HistoricalRecords()` to the model and `admin_mixins = [SimpleHistoryAdmin]`. The historical model is a plain model SnapAdmin ignores. |
| **django-filter** (filtering) | ✅ | The SnapAdmin REST API uses standard DRF; add `DjangoFilterBackend` to `DEFAULT_FILTER_BACKENDS` (or per-view) and it composes with SnapAdmin's search/ordering backends. Admin filters come from the `filterable=True` field flag. |
| **django-taggit** (tags) | ✅ | Taggit's `TaggableManager` is a normal field; it appears in the auto-generated form/API. Taggit's own `Tag`/`TaggedItem` models are not `SnapModel`s, so they are not auto-registered. |

Legend: ✅ compatible — no SnapAdmin change needed beyond the documented hook.

## Worked example — import/export + versioning on one model

```python
from import_export.admin import ImportExportModelAdmin
from reversion.admin import VersionAdmin
from snapadmin import models as snap_models, fields as snap_fields

class Invoice(snap_models.SnapModel):
    number = snap_fields.SnapCharField(max_length=32, searchable=True, show_in_list=True, show_in_form=True)

    # Compose ecosystem admin behaviour with SnapAdmin's auto-config:
    admin_mixins = [ImportExportModelAdmin, VersionAdmin]
```

The generated admin is `Invoice → ImportExportModelAdmin → VersionAdmin → (SnapAdmin mixins) →
ModelAdmin`: import/export buttons, version history, **and** SnapAdmin's field-driven
`list_display` / search / filters / PII masking / audit logging, all at once.

## When there's a genuine conflict

If a package needs to be the sole owner of a model's admin (rare — usually a custom `AdminSite`), set
`admin_enabled = False` on that `SnapModel` and register the package admin yourself. SnapAdmin will not
touch it, and the model's REST API / GraphQL / search continue to work independently of the admin.
