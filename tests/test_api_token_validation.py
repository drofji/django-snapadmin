
import pytest
from django.core.exceptions import ValidationError
from snapadmin.models import APIToken, validate_allowed_models

@pytest.mark.django_db
class TestAllowedModelsValidation:
    def test_valid_format(self):
        # Should not raise
        validate_allowed_models(["demo.Product", "demo.Customer"])

    def test_invalid_type(self):
        with pytest.raises(ValidationError, match="Allowed models must be a list"):
            validate_allowed_models("demo.Product")

    def test_invalid_format_missing_dot(self):
        with pytest.raises(ValidationError, match="Invalid model format"):
            validate_allowed_models(["Product"])

    def test_invalid_format_too_many_dots(self):
        with pytest.raises(ValidationError, match="Invalid model format"):
            validate_allowed_models(["demo.app.Product"])

    def test_non_existent_model(self):
        with pytest.raises(ValidationError, match="does not exist or is not registered"):
            validate_allowed_models(["demo.NonExistent"])

    def test_non_existent_app(self):
        with pytest.raises(ValidationError, match="does not exist or is not registered"):
            validate_allowed_models(["nonexistent.Product"])

    def test_token_save_validation(self, admin_user):
        token = APIToken(
            user=admin_user,
            token_name="Test",
            allowed_models=["invalid_format"]
        )
        with pytest.raises(ValidationError):
            token.full_clean()
