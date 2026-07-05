from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from demo.models import Product


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
