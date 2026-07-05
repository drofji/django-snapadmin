from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from snapadmin.views import DashboardView
from demo.views import product_search, trigger_error

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('admin/', admin.site.urls),
    # i18n: set_language view backing the SnapAdmin language switcher (issue #9).
    path('i18n/', include('django.conf.urls.i18n')),
    path('api/', include('snapadmin.urls')),
    path('demo/search/', product_search, name='product-search'),
    path('demo/error/', trigger_error, name='trigger-error'),
    path("ckeditor5/", include('django_ckeditor_5.urls'), name="ck_editor_5_upload_file"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
