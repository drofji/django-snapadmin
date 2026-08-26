# demo/models.py

from django.utils.translation import gettext_lazy as _
from django.db import models as django_models
from snapadmin import fields as snap_fields, models as snap_models
from snapadmin import validators
from snapadmin.admin import SnapTabularInline, SnapStackedInline
import uuid

# ── api_write_fields, on every model below ───────────────────────────────────
# Each model declares the exact set of fields an API client may supply on
# create/update — the mass-assignment allowlist. Leaving it unset makes every
# non-excluded field writable and raises snapadmin.W004 at startup; the demo
# ships the guard turned on so it *demonstrates* the safe posture instead of
# tripping the warning. Read-only models (ExchangeRate) need no allowlist: they
# answer 405 to write verbs, so there is nothing to mass-assign.


class Category(snap_models.SnapModel):
    # searchable=True → adds "name" to admin search box
    name = snap_fields.SnapCharField(max_length=100, verbose_name=_("Name"), searchable=True, show_in_form=True)
    # row="basic" → groups slug + is_active into a single horizontal row in the form
    slug = snap_fields.SnapSlugField(verbose_name=_("Slug"), show_in_form=True, row="basic")
    is_active = snap_fields.SnapBooleanField(default=True, verbose_name=_("Active"), show_in_form=True, row="basic")

    api_write_fields = ["name", "slug", "is_active"]

    class Meta:
        verbose_name = _("Category")
        verbose_name_plural = _("Categories")

class Tag(snap_models.SnapModel):
    name = snap_fields.SnapCharField(max_length=50, verbose_name=_("Tag Name"), searchable=True, show_in_form=True)

    api_write_fields = ["name"]

    class Meta:
        verbose_name = _("Tag")
        verbose_name_plural = _("Tags")

class Product(snap_models.SnapModel):
    # filterable=True → adds a sidebar filter for this FK; autocomplete uses Django's search
    category = snap_fields.SnapForeignKey(Category, on_delete=django_models.SET_NULL, null=True, blank=True, verbose_name=_("Category"), show_in_form=True, filterable=True)
    tags = snap_fields.SnapManyToManyField(Tag, blank=True, verbose_name=_("Tags"), show_in_form=True)
    # searchable=True → full-text search on this field in the admin list view
    name = snap_fields.SnapCharField(max_length=200, verbose_name=_("Name"), searchable=True, show_in_form=True)
    # filterable=True on a DecimalField → generates a numeric range filter in the sidebar
    # row="pricing" → price + available appear side-by-side in the form
    price = snap_fields.SnapDecimalField(max_digits=10, decimal_places=2, verbose_name=_("Price"), show_in_form=True, filterable=True, row="pricing")
    available = snap_fields.SnapBooleanField(default=True, verbose_name=_("Available"), show_in_form=True, filterable=True, row="pricing")

    # The auto-generated REST FilterSet exposes null-checks and membership lists per
    # field type — no config needed. On this model:
    #   GET /api/models/demo/Product/?category_id__isnull=true  (rows with no category)
    #   GET /api/models/demo/Product/?category_id__in=1,2       (FK membership)
    #   GET /api/models/demo/Product/?price__in=9.99,19.99      (numeric membership)
    #   GET /api/models/demo/Product/?price__isnull=false       (rows that have a price)
    # A text field opts into ?name__isnull=<bool> by adding "isnull" to
    # api_filter_lookups (dates get ?field__isnull= too, but no __in).

    # SnapStatusBadgeField renders a color-coded pill badge in the list view
    # True → green badge, False → red badge (no extra template needed).
    # Source field and choices read positionally here; the keyword form (see Customer
    # below) is identical — both are supported.
    status_badge = snap_fields.SnapStatusBadgeField(
        "available",
        [
            snap_fields.SnapStatusBadgeFieldChoice(True, "#065F46", "#D1FAE5", "#10B981"),
            snap_fields.SnapStatusBadgeFieldChoice(False, "#991B1B", "#FEE2E2", "#EF4444"),
        ],
        verbose_name=_("In Stock"),
    )
    # wysiwyg=True → renders a rich-text editor (CKEditor 5) in the admin form. The HTML is
    # sanitized on the way into the database (every write path: admin, API, bulk_create) and again
    # when the changelist renders it. Opt out per field with safe_html=True (trust it verbatim) or
    # auto_sanitize=False (store as submitted, still sanitized on render).
    description = snap_fields.SnapTextField(verbose_name=_("Description"), wysiwyg=True, show_in_form=True)

    # Unfold: compress empty tab panels into collapsible sections
    compressed_fields = True
    # Unfold: show a browser alert when the user tries to leave with unsaved changes
    warn_unsaved_form = True
    admin_tabs = [
        {"title": _("General"), "link": "#"},
        {"title": _("Advanced"), "link": "#"},
    ]

    # es_storage_mode = DUAL → writes to both PostgreSQL and Elasticsearch on save
    # Use DUAL when you need fast full-text search but also want DB reliability
    es_index_enabled = True
    es_storage_mode = snap_models.EsStorageMode.DUAL
    # es_query_routing = True (default) → REST API `?search=` requests on this model
    # are executed on Elasticsearch (fuzzy, relevance-ranked) instead of DB icontains;
    # plain listings and filters stay on the database. Try it:
    #   GET /api/models/demo/Product/?search=laptop
    # and check the X-Snap-Query-Backend response header. Set False to opt out.
    es_query_routing = True
    # es_search() is fuzzy full-text; for a *structured* term filter use es_filter(),
    # which runs in ES filter context (no scoring, cacheable) and mirrors es_search's
    # return shape. A scalar builds a `term` clause, a list a `terms` clause:
    #   Product.es_filter(available=True, price=[999, 1299])
    # Term keys resolve through the ES mapping — a `text` field automatically targets
    # its keyword sub-field, and a `payload__status` path reaches into a JSON/object
    # mapping (the case a plain DB column can't index). When ES is disabled or errors,
    # DUAL models fall back to the equivalent database filter.
    #
    # es_aggregate() is the faceting counterpart — one ES `terms` aggregation per
    # field, returned as bucket dicts, with the same field resolution and optional
    # filter context as es_filter():
    #   Product.es_aggregate("available", available=True)
    #   # {"available": [{"key": True, "count": 42}, ...]}
    # When ES is down, DUAL models recompute the facets over the DB
    # (values(field).annotate(Count)); ES_ONLY models return empty buckets.
    #
    # es_count() gives the *true* match total of a structured query via ES's
    # _count API — unlike len(es_filter(...)) it is not capped at
    # SNAPADMIN_ES_SEARCH_LIMIT, so it stays exact past the 10k window:
    #   Product.es_count(available=True)  # e.g. 4217, however large
    # When ES is down, DUAL models fall back to a DB count(); ES_ONLY returns 0.
    #
    # db_fallback=False opts OUT of that silent DB fallback on any of the four
    # methods above — instead of quietly running a query that can't scale (a
    # full-table GROUP BY, an unbounded .iterator()), it raises SnapEsUnavailable
    # when ES can't answer. Use it on a large, DB-unindexable table where a clear
    # failure beats a silent scan:
    #   Product.es_count(available=True, db_fallback=False)  # raise if ES is down
    # Set the project-wide default with SNAPADMIN_ES_DB_FALLBACK (default True).
    #
    # es_scan() streams *every* match past ES's 10k max_result_window — a lazy
    # search_after iterator with the same filter args as es_filter():
    #   for product in Product.es_scan(available=True):  # no 10k ceiling
    #       ...
    # DUAL models yield DB objects in cursor order; when ES is down they fall
    # back to a .iterator() over the equivalent DB filter.
    # For the pks of millions of matches, skip the document body + the DB
    # in_bulk() round-trip with source=False, and bound the walk with limit:
    #   for pk in Product.es_scan(available=True, source=False, limit=10000):
    #       ...  # yields pks straight from the sort cursor, no hydration
    es_mapping = {
        "name": {"type": "text", "analyzer": "standard"},
        "price": {"type": "float"},
        "available": {"type": "boolean"},
    }
    # es_index_settings → index-level settings (custom analyzers, shards, replicas)
    # applied when the index is first created. To change them later: delete the
    # index, then run Product.es_reindex_all().
    es_index_settings = {"number_of_shards": 1}

    api_write_fields = ["category", "tags", "name", "price", "available", "description"]

    class Meta:
        verbose_name = _("Product")
        verbose_name_plural = _("Products")

class Customer(snap_models.SnapModel):
    # row="name" → first_name + last_name appear side-by-side
    first_name = snap_fields.SnapCharField(max_length=100, verbose_name=_("First Name"), show_in_form=True, row="name")
    last_name = snap_fields.SnapCharField(max_length=100, verbose_name=_("Last Name"), show_in_form=True, row="name")
    origin = snap_fields.SnapCharField(
        max_length=100,
        verbose_name=_("Origin"),
        choices=[('status_a', 'Status A'), ('status_b', 'Status B'), ('status_c', 'Status C')],
        show_in_list=False,  # hide from list but still filterable via sidebar
        filterable=True,
    )
    email = snap_fields.SnapEmailField(max_length=200, verbose_name=_("Email"), show_in_form=True, row="status")
    active = snap_fields.SnapBooleanField(default=True, verbose_name=_("Is Active"), show_in_form=True, filterable=True, row="status")

    # Demonstrates status badges on a boolean with custom colors
    status_badge = snap_fields.SnapStatusBadgeField(
        field_name="active",
        verbose_name=_("Status"),
        choices=[
            snap_fields.SnapStatusBadgeFieldChoice(True, "#065F46", "#D1FAE5", "#10B981"),
            snap_fields.SnapStatusBadgeFieldChoice(False, "#991B1B", "#FEE2E2", "#EF4444"),
        ]
    )

    # offline_mode = True → the Customer list view is cached in IndexedDB so it stays
    # usable without a connection; a dynamic toast + saved-objects panel appear when the
    # backend becomes unreachable and queued changes sync automatically on reconnect.
    offline_mode = True
    # offline_cache_limit → prefetch only the 50 most-recent customers for offline view
    # (overrides the default of 100) to keep the IndexedDB snapshot small.
    offline_cache_limit = 50

    api_write_fields = ["first_name", "last_name", "origin", "email", "active"]

    class Meta:
        verbose_name = _("Customer")
        verbose_name_plural = _("Customers")

class CustomerProfile(snap_models.SnapModel):
    """Demonstrates SnapOneToOneField — a one-to-one extension of Customer.

    A OneToOne models "exactly one related row" (here: one profile per customer).
    on_delete=CASCADE removes the profile when its customer is deleted.
    """
    customer = snap_fields.SnapOneToOneField(
        Customer, on_delete=django_models.CASCADE, related_name="profile",
        verbose_name=_("Customer"), autocomplete=True, show_in_list=True, show_in_form=True,
    )
    newsletter = snap_fields.SnapBooleanField(default=False, verbose_name=_("Newsletter Opt-in"), show_in_form=True, filterable=True)
    bio = snap_fields.SnapTextField(blank=True, verbose_name=_("Bio"), show_in_form=True)

    api_write_fields = ["customer", "newsletter", "bio"]

    class Meta:
        verbose_name = _("Customer Profile")
        verbose_name_plural = _("Customer Profiles")

class Order(snap_models.SnapModel):
    # autocomplete=True → FK rendered as searchable autocomplete widget (requires search_fields on Customer)
    customer = snap_fields.SnapForeignKey(Customer, on_delete=django_models.PROTECT, verbose_name=_("Customer"), autocomplete=True, show_in_list=True, show_in_form=True)
    total = snap_fields.SnapDecimalField(max_digits=10, decimal_places=2, verbose_name=_("Total"), show_in_form=True, filterable=True)
    created_at = snap_fields.SnapDateTimeField(auto_now_add=True, verbose_name=_("Created At"))

    snap_inlines = []  # populated below after OrderItemInline is defined

    # created_at is auto_now_add (never client-supplied), so the allowlist is
    # just the two fields an order actually carries.
    api_write_fields = ["customer", "total"]

    class Meta:
        verbose_name = _("Order")
        verbose_name_plural = _("Orders")

class SearchLog(snap_models.SnapModel):
    """
    Demonstrates es_storage_mode = ES_ONLY.
    No database table — records live only in Elasticsearch.
    """
    query = snap_fields.SnapCharField(max_length=255, verbose_name=_("Query"), searchable=True)
    results_count = snap_fields.SnapIntegerField(verbose_name=_("Results Count"))
    # snap_field() attaches SnapAdmin metadata to a plain Django field instead
    # of a Snap*Field subclass — the interop path for a third-party field
    # package (django-money, phonenumber_field, model-utils, ...) or a model
    # that cannot be rewritten onto the Snap*Field classes. filterable=True
    # here gets the same sidebar range filter a SnapDateTimeField would get.
    timestamp = snap_fields.snap_field(
        django_models.DateTimeField(auto_now_add=True, verbose_name=_("Timestamp")),
        filterable=True,
    )
    # Plain on purpose — it needs no Snap behaviour. A SnapModel is an ordinary
    # Django model underneath, so a bare field works exactly as it would on any
    # other model: stored, editable, but absent from the generated search box
    # and sidebar filters (see #mixing-fields for the full contrast with the
    # two Snap*Fields and the snap_field()-wrapped one above).
    user_agent = django_models.CharField(max_length=255, blank=True, default="")

    # ES_ONLY → managed=False, no migration; data is written only to the ES index
    es_storage_mode = snap_models.EsStorageMode.ES_ONLY
    # es_auto_mapping = True → the index mapping is derived from the model fields
    # automatically: query → text + .raw keyword subfield, results_count → long,
    # timestamp → date. Declare es_mapping only for fields needing an override.
    es_auto_mapping = True

    api_write_fields = ["query", "results_count"]

    class Meta:
        managed = False  # No DB table — required for ES_ONLY models
        verbose_name = _("Search Log")
        verbose_name_plural = _("Search Logs")


class AuditLog(snap_models.SnapModel):
    """
    Demonstrates GDPR data retention.
    Records older than data_retention_days are auto-deleted by the purge_expired_data
    Celery task. Run manually: python demo/manage.py snapadmin_purge_expired_data --dry-run
    """
    action = snap_fields.SnapCharField(max_length=100, verbose_name=_("Action"), searchable=True, show_in_list=True, show_in_form=True)
    user_email = snap_fields.SnapEmailField(verbose_name=_("User Email"), show_in_list=True, show_in_form=True)
    created_at = snap_fields.SnapDateTimeField(auto_now_add=True, verbose_name=_("Created At"), filterable=True)

    # Auto-delete records older than 90 days via the purge_expired_data Celery task
    data_retention_days = 90
    data_retention_field = "created_at"  # the DateTimeField used to calculate record age

    # api_exclude_fields → user_email (PII) never appears in the REST API,
    # GraphQL or /api/models/schema/ — the admin still shows it.
    api_exclude_fields = ["user_email"]

    # api_write_fields → only "action" accepts a client-supplied value on
    # create/update through the API; created_at is already read-only
    # (auto_now_add) and user_email is excluded above, so this is the
    # mass-assignment allowlist for whatever's left as the model grows.
    api_write_fields = ["action"]

    class Meta:
        verbose_name = _("Audit Log")
        verbose_name_plural = _("Audit Logs")

class ExchangeRate(snap_models.SnapModel):
    """
    Demonstrates the generic ETL helper `snapadmin.etl.upsert_from_source`.

    A currency-rate feed pulled from an external provider and upserted in bulk
    on the unique `code` — the textbook ETL case: refresh a whole table from an
    external source without duplicating rows. See the `sync_exchange_rates`
    management command. DUAL storage means one bulk ES reindex runs after the
    sync instead of a write per row (when ELASTICSEARCH_ENABLED).
    """
    code = snap_fields.SnapCharField(max_length=3, unique=True, required=True, verbose_name=_("Currency"), searchable=True, show_in_form=True)
    base = snap_fields.SnapCharField(max_length=3, default="EUR", verbose_name=_("Base"), filterable=True, show_in_form=True)
    rate = snap_fields.SnapDecimalField(max_digits=18, decimal_places=6, verbose_name=_("Rate"), filterable=True, show_in_form=True)
    synced_at = snap_fields.SnapDateTimeField(auto_now=True, verbose_name=_("Last Synced"))
    # Watermark for stale_sync(strategy="last_seen"): the sync stamps every row it
    # still reports with run_started, then stale_sync deletes rows left below it —
    # a DB-side prune that never materialises a natural-key set in memory. Distinct
    # from synced_at (auto_now, which bulk upserts don't trigger); this is set
    # explicitly in the upsert rows. Nullable so a never-synced row is never stale.
    last_seen = snap_fields.SnapDateTimeField(null=True, blank=True, verbose_name=_("Last Seen In Feed"))

    es_index_enabled = True
    es_storage_mode = snap_models.EsStorageMode.DUAL
    es_auto_mapping = True
    # Rates are imported from the external feed (see sync_exchange_rates), never
    # written by API clients — so the dynamic REST API serves this table read-only:
    # list/retrieve/count/export work, POST/PUT/PATCH/DELETE answer 405.
    api_read_only = True

    class Meta:
        verbose_name = _("Exchange Rate")
        verbose_name_plural = _("Exchange Rates")


class OrderItem(snap_models.SnapModel):
    order = snap_fields.SnapForeignKey(Order, on_delete=django_models.CASCADE, related_name="items", verbose_name=_("Order"))
    product = snap_fields.SnapForeignKey(Product, on_delete=django_models.CASCADE, verbose_name=_("Product"), show_in_form=True)
    quantity = snap_fields.SnapPositiveIntegerField(default=1, verbose_name=_("Quantity"), show_in_form=True)
    price = snap_fields.SnapDecimalField(max_digits=10, decimal_places=2, verbose_name=_("Price at purchase"), show_in_form=True)

    # @snap_property — a computed, display-only changelist column written as a
    # method instead of a SnapFunctionField assignment. No database column, no
    # migration: it never appears in _meta.get_fields(). Equivalent to
    #   line_total = snap_fields.SnapFunctionField(
    #       func=lambda obj: f"{obj.quantity * obj.price:.2f}", verbose_name="Line Total",
    #   )
    @snap_models.snap_property(verbose_name=_("Line Total"))
    def line_total(self):
        return f"{self.quantity * self.price:.2f}"

    api_write_fields = ["order", "product", "quantity", "price"]

    class Meta:
        verbose_name = _("Order Item")
        verbose_name_plural = _("Order Items")

# SnapTabularInline renders OrderItems inside the Order change form as a compact table
class OrderItemInline(SnapTabularInline):
    model = OrderItem
    extra = 1

Order.snap_inlines = [OrderItemInline]

# ===========================================================================
# Showcase Model — every SnapField type in one place, grouped by Unfold tabs
# ===========================================================================

class Showcase(snap_models.SnapModel):
    # Tab: Text Content
    char_field = snap_fields.SnapCharField(max_length=100, verbose_name=_("Char Field"), show_in_form=True, searchable=True, tab=_("Text Content"))
    text_field = snap_fields.SnapTextField(verbose_name=_("Text Field"), show_in_form=True, tab=_("Text Content"))
    # wysiwyg=True enables the rich-text editor (same as SnapRichTextField shorthand)
    wysiwyg_field = snap_fields.SnapTextField(verbose_name=_("WYSIWYG Field"), wysiwyg=True, show_in_form=True, tab=_("Text Content"))

    # Tab: Numeric Data — filterable fields show numeric range filters in the sidebar
    integer_field = snap_fields.SnapIntegerField(verbose_name=_("Integer Field"), show_in_form=True, filterable=True, tab=_("Numbers"))
    positive_integer = snap_fields.SnapPositiveIntegerField(verbose_name=_("Positive Int"), show_in_form=True, tab=_("Numbers"))
    float_field = snap_fields.SnapFloatField(verbose_name=_("Float Field"), show_in_form=True, tab=_("Numbers"))
    decimal_field = snap_fields.SnapDecimalField(max_digits=10, decimal_places=2, verbose_name=_("Decimal Field"), show_in_form=True, tab=_("Numbers"))
    big_int = snap_fields.SnapBigIntegerField(verbose_name=_("Big Int"), show_in_form=True, tab=_("Numbers"))

    # Tab: Temporal — filterable DateFields get date range filters
    date_field = snap_fields.SnapDateField(verbose_name=_("Date Field"), show_in_form=True, filterable=True, tab=_("Dates & Times"))
    datetime_field = snap_fields.SnapDateTimeField(verbose_name=_("DateTime Field"), show_in_form=True, filterable=True, tab=_("Dates & Times"))
    time_field = snap_fields.SnapTimeField(verbose_name=_("Time Field"), show_in_form=True, tab=_("Dates & Times"))
    duration_field = snap_fields.SnapDurationField(verbose_name=_("Duration Field"), show_in_form=True, tab=_("Dates & Times"))

    # Tab: Specialized
    email_field = snap_fields.SnapEmailField(verbose_name=_("Email Field"), show_in_form=True, tab=_("Specialized"))
    slug_field = snap_fields.SnapSlugField(verbose_name=_("Slug Field"), show_in_form=True, tab=_("Specialized"))
    url_field = snap_fields.SnapURLField(verbose_name=_("URL Field"), show_in_form=True, tab=_("Specialized"))
    uuid_field = snap_fields.SnapUUIDField(default=uuid.uuid4, verbose_name=_("UUID Field"), show_in_form=True, tab=_("Specialized"))
    ip_field = snap_fields.SnapGenericIPAddressField(verbose_name=_("IP Address"), show_in_form=True, tab=_("Specialized"))

    # Tab: Files & Misc
    image_field = snap_fields.SnapImageField(upload_to="showcase/images/", verbose_name=_("Image Field"), show_in_form=True, blank=True, null=True, tab=_("Assets"))
    file_field = snap_fields.SnapFileField(upload_to="showcase/files/", verbose_name=_("File Field"), show_in_form=True, blank=True, null=True, tab=_("Assets"))
    boolean_field = snap_fields.SnapBooleanField(default=False, verbose_name=_("Boolean Field"), show_in_form=True, filterable=True, tab=_("Assets"))
    json_field = snap_fields.SnapJSONField(default=dict, verbose_name=_("JSON Field"), show_in_form=True, tab=_("Assets"))

    # Tab: Extended Types — new field types added in v0.1.0a2
    # SnapRichTextField is SnapTextField with wysiwyg=True pre-applied
    rich_text_field = snap_fields.SnapRichTextField(verbose_name=_("Rich Text"), show_in_form=True, tab=_("Extended"))
    # SnapPhoneField validates E.164 and common national formats
    phone_field = snap_fields.SnapPhoneField(verbose_name=_("Phone Number"), show_in_form=True, tab=_("Extended"))
    # SnapColorField validates #RRGGBB and #RGB hex strings
    color_field = snap_fields.SnapColorField(default="#3B82F6", verbose_name=_("Color"), show_in_form=True, tab=_("Extended"))
    small_int_field = snap_fields.SnapSmallIntegerField(verbose_name=_("Small Integer"), show_in_form=True, tab=_("Extended"))
    pos_small_int_field = snap_fields.SnapPositiveSmallIntegerField(verbose_name=_("Pos. Small Integer"), show_in_form=True, tab=_("Extended"))
    pos_big_int_field = snap_fields.SnapPositiveBigIntegerField(verbose_name=_("Pos. Big Integer"), show_in_form=True, tab=_("Extended"))

    # SnapFunctionField — a computed, read-only column rendered from a callable.
    # It is NOT a database column (no migration), so it's perfect for derived or
    # aggregated display values. safe_html=False escapes the output.
    summary = snap_fields.SnapFunctionField(
        func=lambda obj: f"{obj.char_field or '—'} · int={obj.integer_field or 0}",
        verbose_name=_("Computed Summary"),
        show_in_list=True,
        show_in_form=False,
    )

    # Unfold: group fields into collapsible sections and warn on unsaved changes
    compressed_fields = True
    warn_unsaved_form = True

    # api_json_filters → exposes ?json_field__a__b=value (nested scalar match) and
    # ?json_field__tags=value (list-membership match, since "tags" is stored as a
    # JSON array) through the auto-generated REST API filter set. A comma is an OR,
    # like __in: ?json_field__tags=red,green matches either. On PostgreSQL/MySQL this
    # is a lazy native JSON query (streams with export/); on SQLite it is a capped
    # Python row scan (SNAPADMIN_API_JSON_FILTER_SCAN_CAP → 400 past the cap).
    api_json_filters = {"json_field": ["a.b", "tags"]}

    # api_default_text_lookups → model-wide default lookup set for EVERY text field
    # (char_field/text_field/wysiwyg_field/…), so you set the posture once instead of
    # enumerating each column in api_filter_lookups. Here we drop the non-indexable
    # `icontains` to keep the auto REST filters index-friendly: ?char_field=value is
    # an exact match, with ?char_field__startswith= and ?char_field__in= available.
    # A per-field api_filter_lookups entry (or the project-wide SNAPADMIN_API_TEXT_LOOKUPS
    # setting) still overrides this.
    api_default_text_lookups = ["exact", "startswith", "in"]

    # Every stored column is client-writable here — this model exists to exercise
    # the field types — but the allowlist is still spelled out, so adding a field
    # never silently widens the API's write surface.
    api_write_fields = [
        "char_field", "text_field", "wysiwyg_field",
        "integer_field", "positive_integer", "float_field", "decimal_field", "big_int",
        "date_field", "datetime_field", "time_field", "duration_field",
        "email_field", "slug_field", "url_field", "uuid_field", "ip_field",
        "image_field", "file_field", "boolean_field", "json_field",
        "rich_text_field", "phone_field", "color_field",
        "small_int_field", "pos_small_int_field", "pos_big_int_field",
    ]

    class Meta:
        verbose_name = _("Showcase")
        verbose_name_plural = _("Showcase")
