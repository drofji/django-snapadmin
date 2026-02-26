
import pytest
from django.urls import reverse
from rest_framework.test import APIClient

@pytest.mark.django_db
def test_health_check_endpoint():
    client = APIClient()
    url = reverse('api-health')
    response = client.get(url)
    assert response.status_code == 200
    data = response.json()
    assert 'status' in data
    assert 'services' in data
    assert 'database' in data['services']

@pytest.mark.django_db
def test_dashboard_view(admin_client):
    url = reverse('dashboard')
    response = admin_client.get(url)
    assert response.status_code == 200
    assert b"SnapAdmin Dashboard" in response.content
