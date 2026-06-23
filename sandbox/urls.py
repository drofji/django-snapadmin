from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from snapadmin.views import DashboardView
from demo.views import product_search

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('admin/', admin.site.urls),
    path('api/', include('snapadmin.urls')),
    path('demo/search/', product_search, name='product-search'),
    path("ckeditor5/", include('django_ckeditor_5.urls'), name="ck_editor_5_upload_file"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
