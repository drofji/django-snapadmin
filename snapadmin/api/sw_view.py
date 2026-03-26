from django.http import FileResponse
from django.conf import settings
import os

def service_worker(request):
    path = os.path.join(settings.BASE_DIR, 'snapadmin', 'static', 'snapadmin', 'js', 'sw.js')
    return FileResponse(open(path, 'rb'), content_type='application/javascript')
