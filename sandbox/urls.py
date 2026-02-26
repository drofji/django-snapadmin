from django.contrib import admin
from django.urls import path, include

# TODO - Make dashboard on "/" with links to admin, api-docs, api-redoc, api, kibana, postgre, elasticsearch in telegram-style. With online/offline health status of services (you can make health method in api to check all connections), and infos like main configurations from settings.py like debug, allowed hosts, etc.
urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('snapadmin.urls')),
]
