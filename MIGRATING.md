# Migration Guide

How to move to **`django-snapadmin`** from the legacy packages
`drofji-automatically-django-admin` / `drofji-snapadmin`.

> `django-snapadmin` is a drop-in successor. The core idea is unchanged — declare your model once
> and get the admin, REST API, GraphQL and search for free — but the distribution name, import root,
> base class and settings prefix were normalised. This guide lists every rename and a checklist.

---

## TL;DR — what changed

| Area | Legacy | `django-snapadmin` |
| --- | --- | --- |
| PyPI package | `drofji-automatically-django-admin` / `drofji-snapadmin` (both retired) | `django-snapadmin` |
| `INSTALLED_APPS` entry | legacy app label | `"snapadmin"` |
| Import root | legacy module | `snapadmin` |
| Model base class | legacy auto-admin base | `snapadmin.models.SnapModel` |
| Field types | plain Django fields + separate `ModelAdmin` | `snapadmin.fields.Snap*` fields with inline admin config |
| Settings prefix | legacy / unprefixed | `SNAPADMIN_*` |
| Admin registration | hand-written `admin.py` | automatic for every `SnapModel` |

---

## 1. Install

```bash
pip uninstall drofji-automatically-django-admin drofji-snapadmin   # remove the old ones
pip install django-snapadmin
```

Both legacy distributions are retired on PyPI and will not receive updates.

## 2. `INSTALLED_APPS`

Replace the legacy app label with `"snapadmin"`. If you use the Unfold theme, keep its apps ahead of
`django.contrib.admin` as before:

```python
INSTALLED_APPS = [
    "unfold",                 # optional theme, before admin
    "django.contrib.admin",
    # ...
    "snapadmin",
    "rest_framework",         # REST API
    "drf_spectacular",        # Swagger / OpenAPI
    # your apps
]
```

## 3. Models → `SnapModel`

Point your models at `SnapModel` and swap plain fields for the `Snap*` equivalents, moving the admin
options that used to live in a separate `ModelAdmin` onto the field itself:

```python
# before — model + separate admin
class Product(models.Model):
    name = models.CharField(max_length=200)

class ProductAdmin(admin.ModelAdmin):
    list_display = ["name"]
    search_fields = ["name"]
admin.site.register(Product, ProductAdmin)

# after — one declaration, admin auto-generated
from snapadmin import models as snap_models, fields as snap_fields

class Product(snap_models.SnapModel):
    name = snap_fields.SnapCharField(max_length=200, searchable=True, show_in_list=True, show_in_form=True)
```

Common field-level options: `show_in_list`, `show_in_form`, `searchable`, `filterable`,
`autocomplete`, `row=` (group fields in a row), `tab=` (group into a tab).

## 4. Delete the hand-written `admin.py`

Auto-registration replaces it. Every `SnapModel` is registered automatically — remove the
`admin.site.register(...)` calls. Keep a custom `ModelAdmin` only for a model you deliberately want
to configure by hand (register it before SnapAdmin runs, and SnapAdmin will skip the already-registered
model).

## 5. Settings → `SNAPADMIN_*`

All configuration now lives under the `SNAPADMIN_*` prefix. The most common toggles:

```python
SNAPADMIN_REST_API_ENABLED = True     # /api/ CRUD
SNAPADMIN_GRAPHQL_ENABLED = True      # /api/graphql/
SNAPADMIN_SWAGGER_ENABLED = True      # /api/docs/
ELASTICSEARCH_ENABLED = False         # opt-in full-text search
```

See the [Environment Variables Reference](README.md#-environment-variables-reference) for the full
list, including the newer enterprise settings (read-replica routing, PII masking, SSO, audit trail,
background export).

## 6. URLs

Mount the API (unchanged shape):

```python
urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("snapadmin.urls")),
]
```

## 7. Run the checks and migrate

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py check        # SnapAdmin config checks flag any mis-set SNAPADMIN_* value
```

`manage.py check` now validates your SnapAdmin settings (see issue #2) — a typo in a masked-field,
nested-app or replica-alias setting is reported here rather than failing silently at runtime.

---

## Upgrade checklist

- [ ] Uninstall `drofji-automatically-django-admin` / `drofji-snapadmin`; install `django-snapadmin`.
- [ ] Add `"snapadmin"` (and DRF / drf-spectacular) to `INSTALLED_APPS`.
- [ ] Change model base classes to `SnapModel`.
- [ ] Convert fields to `Snap*` types and move `list_display` / `search_fields` / filters onto the fields.
- [ ] Remove hand-written `admin.site.register(...)` calls that SnapAdmin now auto-generates.
- [ ] Rename settings to the `SNAPADMIN_*` prefix.
- [ ] `include("snapadmin.urls")` under `/api/`.
- [ ] `makemigrations && migrate`.
- [ ] `python manage.py check` — resolve any `snapadmin.*` warnings/errors.
- [ ] Smoke-test the admin index, a changelist, and `/api/` in the browser.

No state is lost by the switch: your tables are unchanged — only the Python declaration and settings
prefix move. Migrations generated after converting to `Snap*` fields are additive (field options,
not column drops) unless you also change column types.
