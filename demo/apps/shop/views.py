from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, reverse
from django.views.generic import TemplateView

from demo.apps.shop.models import Product


def trigger_error(request):
    """Deliberately fail — showcases SnapErrorMonitorMiddleware.

    Each hit records an ErrorEvent (see Error Events in the admin); hit it
    SNAPADMIN_ERROR_ALERT_THRESHOLD times within the alert window to receive
    the spike-alert email. DEBUG-only so it cannot be abused in production.
    """
    if not settings.DEBUG:
        raise Http404
    raise RuntimeError("Demo error: SnapAdmin error monitoring showcase")


def product_search(request):
    query = request.GET.get('q', '')
    if query:
        # Using the new simplified es_search method in SnapModel
        products = Product.es_search(query)
    else:
        products = Product.objects.all()

    return render(request, 'demo/product_list.html', {
        'products': products,
        'query': query
    })


class LandingView(TemplateView):
    """Public landing page for the demo, served at ``/``.

    Deliberately **separate** from :class:`snapadmin.views.DashboardView`, which
    stays staff-only at ``/dashboard/`` because it surfaces infrastructure
    details (hostname, processor, DB name, ``ALLOWED_HOSTS``, live service
    health). This page is safe for any visitor: it never exposes host or
    connection details, only the non-sensitive facts a demo visitor needs.

    * **Anonymous visitor** → a styled Django login form on this same URL (POST
      handled here, no redirect elsewhere; a successful login returns to ``/``).
    * **Authenticated visitor** → their session (username + logout), demo record
      counts per model, and which optional SnapAdmin surfaces are enabled — the
      enabled/disabled *facts* only, read from the same settings the dashboard
      inspects, never the underlying host/connection details.
    * **Staff visitor** → additionally an "Open Admin" button and a link to the
      full staff dashboard. These links are UI affordances only; the admin and
      the dashboard enforce ``is_staff`` themselves, so hiding the link is not
      the security boundary.
    """

    template_name = "demo/landing.html"

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        # Anonymous login via Django's standard AuthenticationForm, handled on
        # this same URL. An already-authenticated POST just returns to the page
        # (there is no login form rendered for them).
        if request.user.is_authenticated:
            return redirect("landing")
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return redirect("landing")
        context = self.get_context_data(login_form=form, **kwargs)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        if not user.is_authenticated:
            context["login_form"] = kwargs.get("login_form") or AuthenticationForm(self.request)
            return context

        services = self._service_flags()
        context.update({
            "stats": self._demo_model_stats(),
            "services": services,
            "services_enabled_count": sum(1 for s in services if s["enabled"]),
            "admin_url": reverse("admin:index"),
            "dashboard_url": self._safe_reverse("dashboard"),
            "swagger_url": self._safe_reverse("swagger-ui"),
        })
        return context

    @staticmethod
    def _safe_reverse(name):
        try:
            return reverse(name)
        except NoReverseMatch:
            return None

    @staticmethod
    def _demo_model_stats():
        """Record counts for each concrete demo-app SnapModel (non-sensitive).

        Only the ``demo`` app's own models are counted — never SnapAdmin's
        internal bookkeeping models (tokens, audit log, export jobs) — and only
        the row count, never any field value.
        """
        from django.apps import apps
        from snapadmin.models import SnapModel

        stats = []
        for model in apps.get_app_config("demo").get_models():
            if not (issubclass(model, SnapModel) and model is not SnapModel):
                continue
            try:
                count = model.objects.count()
            except Exception:
                count = None
            stats.append({
                "name": model._meta.verbose_name_plural.title(),
                "count": count,
                "url": LandingView._safe_reverse(
                    f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist"
                ),
            })
        return sorted(stats, key=lambda s: s["name"])

    @staticmethod
    def _service_flags():
        """Enabled/disabled facts for each optional surface — no host details.

        Mirrors the switches ``DashboardView`` reads, but returns only the
        boolean each one resolves to (plus a stable key/label for rendering);
        crucially it does **not** ping any service or expose a URL/host, so it is
        safe to show a non-staff visitor.
        """
        def flag(name, default=True):
            return bool(getattr(settings, name, default))

        celery_eager = flag("CELERY_TASK_ALWAYS_EAGER", False)
        return [
            {"key": "rest", "label": "REST API", "enabled": flag("SNAPADMIN_REST_API_ENABLED")},
            {"key": "graphql", "label": "GraphQL", "enabled": flag("SNAPADMIN_GRAPHQL_ENABLED")},
            {"key": "swagger", "label": "Swagger UI", "enabled": flag("SNAPADMIN_SWAGGER_ENABLED")},
            {"key": "user_api", "label": "User API", "enabled": flag("SNAPADMIN_USER_API_ENABLED", False)},
            {"key": "audit", "label": "Audit log", "enabled": flag("SNAPADMIN_AUDIT_LOG_ENABLED")},
            {"key": "es", "label": "Elasticsearch", "enabled": flag("ELASTICSEARCH_ENABLED", False)},
            # Celery counts as "enabled" (real broker) only when not running eagerly.
            {"key": "celery", "label": "Celery / Redis", "enabled": not celery_eager},
        ]
