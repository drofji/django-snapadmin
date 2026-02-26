from django.contrib import admin
from django.urls import path, include
from snapadmin.views import DashboardView
from demo.views import product_search

urlpatterns = [
    path('', DashboardView.as_view(), name='dashboard'),
    path('admin/', admin.site.urls),
    path('api/', include('snapadmin.urls')),
    path('demo/search/', product_search, name='product-search'),
]
