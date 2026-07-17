from django.apps import AppConfig


class DemoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'demo.app'
    # Explicit label keeps the pre-restructure app_label ("demo") for migration
    # history and every documented "demo.<Model>" reference (URLs, README curl
    # examples, seed_demo/seed_large/benchmark_list_view commands) unchanged —
    # only the Python package moved, not the app identity.
    label = 'demo'
