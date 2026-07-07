# Migrating: `drofji-automatically-django-admin` → `django-snapadmin`

The legacy package `drofji-automatically-django-admin` (import root `drofji_autoadmin`, last tag
`v1.1.0`) is retired and its repository removed. **SnapAdmin is its direct successor** — same
declarative, field-driven admin, now renamed and extended with a REST API, GraphQL, the Unfold theme,
Elasticsearch, GDPR retention and offline mode. The underlying Django field types are unchanged, so
this is a **rename + settings swap, not a data migration**.

> **No data migration.** SnapAdmin does not alter your existing model tables. The only new table is
> `snapadmin_apitoken` (token auth). Your data stays exactly where it is.

## 1. Swap the package

```bash
pip uninstall drofji-automatically-django-admin
pip install drofji-snapadmin
# or pin the repo:
pip install git+https://github.com/drofji/django-snapadmin.git
```

If you pinned the old GitHub URL in `requirements.txt`, replace that line with `drofji-snapadmin`.

## 2. Rename the import root (`drofji_autoadmin` → `snapadmin`)

```python
# Before
from drofji_autoadmin import models as drofji_models, fields as drofji_fields
from drofji_autoadmin import validators

# After
from snapadmin import models as snap_models, fields as snap_fields
from snapadmin import validators
```

One-shot, repo-wide:

```bash
grep -rl drofji_autoadmin . | xargs sed -i '' 's/drofji_autoadmin/snapadmin/g'
```

## 3. Rename the base class & fields (`AutoAdmin*` → `Snap*`)

```python
# Before
class Product(drofji_models.AutoAdminModel):
    name = drofji_fields.AutoAdminCharField(max_length=200, searchable=True)

# After
class Product(snap_models.SnapModel):
    name = snap_fields.SnapCharField(max_length=200, searchable=True)
```

All field flags (`show_in_list`, `searchable`, `filterable`, `row`, `tab`, …) and the per-model
`admin_overrides` dict keep the same names and behaviour. Mechanical replace:

```bash
grep -rl AutoAdmin . | xargs sed -i '' 's/AutoAdmin/Snap/g'
# AutoAdminModel→SnapModel, AutoAdminCharField→SnapCharField, AutoAdminFunctionField→SnapFunctionField, …
```

## 4. Swap the theme in `INSTALLED_APPS`

The old package themed the admin with `admin_interface` + `colorfield`. SnapAdmin uses **Unfold**.
**Remove the old theme apps** (leaving them alongside Unfold causes conflicting admin overrides) and
add the new stack — order matters, Unfold and its contrib apps must precede `django.contrib.admin`:

```diff
 INSTALLED_APPS = [
-   "admin_interface",
-   "colorfield",
+   "unfold",
+   "unfold.contrib.filters",
+   "unfold.contrib.forms",
+   "unfold.contrib.inlines",
+   "django_ckeditor_5",
    "django.contrib.admin",
    "django.contrib.auth",
    # … other django.contrib.* …
    "rangefilter",            # keep — SnapAdmin still uses it
-   "drofji_autoadmin",
+   "rest_framework",
+   "drf_spectacular",
+   "django_filters",
+   "graphene_django",
+   "snapadmin",
    # your apps …
 ]
```

## 5. Register the admin explicitly (new requirement)

The old package auto-registered models purely by inheritance. SnapAdmin needs one explicit call — add
it to your app's `admin.py`, or your models won't appear in the admin:

```python
# admin.py
from snapadmin.models import SnapModel
SnapModel.register_all_admins()
```

## 6. Replace color fields

If you used `ColorField` from `django-colorfield`, switch to `snap_fields.SnapColorField` (validates
`#RRGGBB` / `#RGB`).

## 7. (Optional) Wire up the new APIs

SnapAdmin ships a REST API, GraphQL endpoint and Swagger UI — features the old package did not have.
Include the routes only if you want them:

```python
# urls.py
urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("snapadmin.urls")),   # /api/, /api/docs/, /graphql/
]

# settings.py — all default True
SNAPADMIN_REST_API_ENABLED = True
SNAPADMIN_GRAPHQL_ENABLED  = True
SNAPADMIN_SWAGGER_ENABLED  = True
ELASTICSEARCH_ENABLED      = False   # opt-in; needs the [elasticsearch] extra
SNAPADMIN_ES_QUERY_ROUTING = True    # route ?search= on DUAL models to ES
SNAPADMIN_ES_SEARCH_LIMIT  = 1000    # max ES hits per routed search
SNAPADMIN_GRAPHQL_REQUIRE_AUTH = True  # auth + perms on every GraphQL resolver
SNAPADMIN_GRAPHIQL_ENABLED = DEBUG     # GraphiQL playground — dev only
```

## 8. Migrate & collect static

```bash
python manage.py migrate          # creates only snapadmin_apitoken
python manage.py collectstatic    # if you serve static yourself
```

> **Don't run both packages at once.** Keeping `drofji_autoadmin` installed and in `INSTALLED_APPS`
> alongside SnapAdmin makes both try to register the admin → `AlreadyRegistered`. Fully uninstall the
> old package and remove it from `INSTALLED_APPS` before enabling SnapAdmin.
