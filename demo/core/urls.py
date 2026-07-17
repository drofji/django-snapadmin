from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from snapadmin.views import DashboardView
from demo.app.views import LandingView, product_search, trigger_error

urlpatterns = [
    # Public landing page (login form for anonymous, session + demo facts for
    # authenticated). The staff-only system dashboard lives at /dashboard/.
    path('', LandingView.as_view(), name='landing'),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    path('logout/', auth_views.LogoutView.as_view(next_page='landing'), name='logout'),
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
