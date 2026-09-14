"""
tests/test_api_filters.py

``snapadmin.api.filters`` — the per-model ``FilterSet`` the REST API builds and
the backend that hands it to a view.

The builder is memoised, because a filterset is rebuilt on every list request
otherwise; the backend has to answer "no filterset" for anything that is not a
model view rather than raise. (#QA1b: these tests came from a suite named for
the coverage metric, where the memoisation test asserted only that the second
call returned something non-null — it never compared it to the first.)
"""

import pytest


@pytest.mark.django_db
class TestBuildFiltersetForModel:
    def test_the_filterset_is_built_once_and_reused(self):
        from demo.apps.shop.models import Customer

        from snapadmin.api.filters import build_filterset_for_model

        first = build_filterset_for_model(Customer)
        second = build_filterset_for_model(Customer)

        assert second is first, "a list request must not rebuild the filterset"

    def test_a_text_field_gets_its_lookup_family(self):
        from demo.apps.shop.models import Customer

        from snapadmin.api.filters import build_filterset_for_model

        filters = build_filterset_for_model(Customer).base_filters

        assert {"email", "email__in", "email__startswith", "email__icontains"} <= set(filters)

    def test_a_uuid_field_is_filterable_by_exact_value_only(self):
        from demo.apps.shop.models import Showcase

        from snapadmin.api.filters import build_filterset_for_model

        filters = build_filterset_for_model(Showcase).base_filters

        assert "uuid_field" in filters
        # No range or text lookups on a UUID — they would not mean anything.
        assert not [name for name in filters if name.startswith("uuid_field__")]

    def test_a_json_field_is_filterable_by_key_path(self):
        from demo.apps.shop.models import Showcase

        from snapadmin.api.filters import build_filterset_for_model

        filters = build_filterset_for_model(Showcase).base_filters

        assert "json_field__tags" in filters
        assert "json_field__a__b" in filters


class TestFilterBackendWithoutAModel:
    """A view that is not a model view gets no filterset, and no exception."""

    def test_a_view_with_no_model_accessor_gets_none(self):
        from snapadmin.api.filters import SnapAdminFilterBackend

        class ViewWithoutAModel:
            pass

        assert (
            SnapAdminFilterBackend().get_filterset_class(ViewWithoutAModel(), queryset=None)
            is None
        )

    def test_a_view_whose_model_cannot_be_resolved_gets_none(self):
        from snapadmin.api.filters import SnapAdminFilterBackend

        class ViewWithAnUnresolvableModel:
            def _get_model_class(self):
                return None

        assert (
            SnapAdminFilterBackend().get_filterset_class(
                ViewWithAnUnresolvableModel(), queryset=None
            )
            is None
        )
