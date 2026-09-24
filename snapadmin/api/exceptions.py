"""
snapadmin/api/exceptions.py

Make a Django ``ValidationError`` raised on an API write path answer as a 400
instead of escaping as a 500 (#EXT1k).

Django Rest Framework only understands its **own** exception hierarchy. A
``django.core.exceptions.ValidationError`` — what ``Model.clean()``,
``Model.full_clean()`` and a hand-written ``save()`` guard all raise — is not an
``APIException``, so DRF's ``handle_exception`` re-raises it and the client gets
an HTML 500 it cannot act on. A cross-field rule failing is a client mistake, not
a server fault, and must be reported as one.

Two entry points, one translation:

* :class:`DjangoValidationErrorMixin` — mixed into
  :class:`snapadmin.api.authentication.SnapAPIAuthMixin`, so every SnapAdmin
  endpoint gets the behaviour with no project configuration at all.
* :func:`snap_exception_handler` — a drop-in DRF ``EXCEPTION_HANDLER`` for a
  project that wants the same translation on **its own** views::

      REST_FRAMEWORK = {
          "EXCEPTION_HANDLER": "snapadmin.api.exceptions.snap_exception_handler",
      }
"""

from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import exceptions as drf_exceptions

if TYPE_CHECKING:  # pragma: no cover - import cycle, see below
    from rest_framework.response import Response
    from rest_framework.views import APIView as _ViewBase
else:
    _ViewBase = object

# ``rest_framework.views`` and ``rest_framework.serializers`` are imported inside
# the functions below, not here. This module is reached while DRF is still
# importing itself: ``rest_framework.views`` pulls in ``rest_framework.schemas``,
# which resolves ``DEFAULT_AUTHENTICATION_CLASSES`` — i.e. imports
# ``snapadmin.api.authentication``, which imports this module. A module-level
# ``from rest_framework.views import exception_handler`` would hit a
# half-initialised ``rest_framework.views`` and fail the whole install at import
# time. ``rest_framework.exceptions`` above is already fully loaded by then.


def as_drf_validation_error(exc: DjangoValidationError) -> drf_exceptions.ValidationError:
    """Translate a Django ``ValidationError`` into DRF's own.

    Field names survive: ``ValidationError({"name": [...]})`` keeps ``name`` as
    the response key, and a message raised without a field lands under DRF's
    ``NON_FIELD_ERRORS_KEY``. The mapping is DRF's own ``as_serializer_error``,
    the same helper its serializers use for a field validator that raises
    Django's flavour of the exception — so a model-level rule is reported in
    exactly the shape a client already parses for a field-level one.

    The one place the two disagree is the key for an error that belongs to no
    field: Django calls it ``__all__``, DRF calls it ``non_field_errors``, and
    ``as_serializer_error`` passes Django's spelling straight through. That only
    shows up once an error dict has been through ``Model.full_clean()``, which
    files a bare ``clean()`` message under ``__all__`` — so it is renamed here
    rather than leaking Django's internal key into a JSON response.
    """
    from django.core.exceptions import NON_FIELD_ERRORS
    from rest_framework.serializers import as_serializer_error
    from rest_framework.settings import api_settings

    detail = as_serializer_error(exc)
    if NON_FIELD_ERRORS in detail:
        detail.setdefault(api_settings.NON_FIELD_ERRORS_KEY, []).extend(
            detail.pop(NON_FIELD_ERRORS)
        )
    return drf_exceptions.ValidationError(detail)


def snap_exception_handler(exc: Exception, context: dict[str, Any]) -> "Response | None":
    """A drop-in DRF ``EXCEPTION_HANDLER`` that adds Django's ``ValidationError``.

    Everything else is handed to DRF's own handler unchanged, including the
    ``None`` it returns for an exception it does not recognise (which DRF reads
    as "re-raise, this really is a 500").

    SnapAdmin's own endpoints do **not** need this setting —
    :class:`DjangoValidationErrorMixin` already covers them. It exists so a
    project can get the same behaviour on views SnapAdmin never generated.
    """
    from rest_framework.views import exception_handler as drf_exception_handler

    if isinstance(exc, DjangoValidationError):
        exc = as_drf_validation_error(exc)
    return drf_exception_handler(exc, context)


#: What the mixin below is mixed *into*. At runtime it stays a plain mixin
#: (``object``); for the type checker it is the DRF class whose hooks it calls
#: through ``super()``, which is what makes those calls checkable instead of
#: silently untyped.
class DjangoValidationErrorMixin(_ViewBase):
    """View mixin translating an escaping Django ``ValidationError`` into a 400.

    **The project's own handler keeps first refusal.** The translation happens
    in the ``except`` branch, after ``super().handle_exception()`` has already
    offered the untouched exception to whatever ``EXCEPTION_HANDLER`` the
    project configured. A project that already handles Django's flavour itself
    therefore sees exactly what it saw before; SnapAdmin only steps in where the
    alternative is the 500 that DRF is about to raise.
    """

    def handle_exception(self, exc: Exception) -> "Response":
        try:
            return super().handle_exception(exc)
        except DjangoValidationError as unhandled:
            return super().handle_exception(as_drf_validation_error(unhandled))
