"""
tests/test_graphql.py  –  Dynamic GraphQL API tests
"""

import pytest
from django.test import Client
from snapadmin.models import APIToken


@pytest.mark.django_db
class TestGraphQLAPI:
    def test_graphql_endpoint_200(self, auth_client):
        # GET request to /api/graphql/ should return GraphiQL page
        # Note: Depending on graphene-django version/config, this might require Accept header
        r = auth_client.get("/api/graphql/", HTTP_ACCEPT="text/html")
        assert r.status_code == 200
        assert "GraphiQL" in r.content.decode()

    def test_query_all_products(self, auth_client, product):
        query = """
        query {
            allDemoProduct {
                id
                name
                price
            }
        }
        """
        r = auth_client.post(
            "/api/graphql/",
            {"query": query},
            format="json",
        )
        assert r.status_code == 200
        data = r.json()["data"]
        assert "allDemoProduct" in data
        assert len(data["allDemoProduct"]) >= 1
        assert data["allDemoProduct"][0]["name"] == product.name

    def test_query_single_product(self, auth_client, product):
        query = f"""
        query {{
            demoProduct(id: {product.pk}) {{
                id
                name
            }}
        }}
        """
        r = auth_client.post(
            "/api/graphql/",
            {"query": query},
            format="json",
        )
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["demoProduct"]["name"] == product.name

    def test_unauthenticated_query_returns_none(self, db, product):
        # Use a plain client without any auth headers
        c = Client()
        query = """
        query {
            allDemoProduct {
                id
                name
            }
        }
        """
        r = c.post(
            "/api/graphql/",
            {"query": query},
            content_type="application/json",
        )
        assert r.status_code == 200
        data = r.json()["data"]
        # Resolvers should return none/empty if no token
        assert data["allDemoProduct"] == []

    def test_restricted_token_blocks_model(self, db, product, customer):
        # Create a token restricted only to 'demo.Product'
        from django.contrib.auth.models import User, Permission
        from django.contrib.contenttypes.models import ContentType
        user = User.objects.create_user(username="testuser2", password="password")

        # Grant view permission for Product
        content_type = ContentType.objects.get_for_model(product)
        permission = Permission.objects.get(
            codename="view_product",
            content_type=content_type,
        )
        user.user_permissions.add(permission)
        user = User.objects.get(pk=user.pk) # refresh

        token = APIToken.create_for_user(
            user=user,
            token_name="Restricted",
            allowed_models=["demo.Product"]
        )

        c = Client()
        auth_header = f"Token {token.token_key}"

        # Should work for Product
        query_p = "{ allDemoProduct { id } }"
        rp = c.post(
            "/api/graphql/",
            {"query": query_p},
            content_type="application/json",
            HTTP_AUTHORIZATION=auth_header,
        )
        assert rp.json()["data"]["allDemoProduct"] != []

        # Should be empty for Customer
        query_c = "{ allDemoCustomer { id } }"
        rc = c.post(
            "/api/graphql/",
            {"query": query_c},
            content_type="application/json",
            HTTP_AUTHORIZATION=auth_header,
        )
        assert rc.json()["data"]["allDemoCustomer"] == []

    def test_query_complex_relations(self, auth_client, order, customer):
        query = f"""
        query {{
            demoOrder(id: {order.pk}) {{
                id
                total
                customer {{
                    id
                    firstName
                    lastName
                }}
            }}
        }}
        """
        r = auth_client.post(
            "/api/graphql/",
            {"query": query},
            format="json",
        )
        assert r.status_code == 200
        data = r.json()["data"]["demoOrder"]
        assert data["customer"]["firstName"] == customer.first_name
