"""``Snap*Field("Label", ...)`` accepts ``verbose_name`` positionally, like Django (#EXT2i).

Every Django field takes ``verbose_name`` as its first positional argument, and
existing model code is full of ``models.CharField("Label", max_length=200)``.
Switching such a model onto Snap fields used to fail on every field with a
``TypeError`` that named neither ``verbose_name`` nor the fix. These tests pin
the Django spelling for every concrete, non-relational Snap field — discovered
from the module, so a field added later is covered without anyone remembering
to list it here.
"""

import inspect

import pytest
from django.db import models
from django.utils.translation import gettext_lazy as _

from snapadmin import fields as snapfields


def _plain_snap_field_classes() -> list[type[models.Field]]:
    """Snap model fields whose first positional slot is ``verbose_name`` in Django.

    Every ``models.Field`` subclass the module defines — including
    ``SnapBlindIndexField``, which is a model field without the ``SnapField``
    mixin and must behave the same way.

    Relational fields are excluded on purpose: Django's own ``ForeignKey`` /
    ``OneToOneField`` / ``ManyToManyField`` take ``to`` first, and so do theirs.
    """
    found = []
    for name, obj in vars(snapfields).items():
        if not (inspect.isclass(obj) and name.startswith("Snap")):
            continue
        if obj.__module__ != snapfields.__name__:
            continue
        if not issubclass(obj, models.Field):
            continue
        if issubclass(obj, (models.ForeignKey, models.ManyToManyField)):
            continue
        found.append(obj)
    return sorted(found, key=lambda cls: cls.__name__)


PLAIN_FIELDS = _plain_snap_field_classes()


def test_discovery_finds_the_known_field_families():
    names = {cls.__name__ for cls in PLAIN_FIELDS}
    assert {"SnapCharField", "SnapIntegerField", "SnapFileField", "SnapEncryptedCharField",
            "SnapBlindIndexField", "SnapColorField"} <= names
    assert "SnapForeignKey" not in names
    assert len(PLAIN_FIELDS) >= 35


@pytest.mark.parametrize("field_cls", PLAIN_FIELDS, ids=lambda cls: cls.__name__)
def test_positional_verbose_name_is_accepted(field_cls):
    field = field_cls("Customer label")

    assert field.verbose_name == "Customer label"


@pytest.mark.parametrize("field_cls", PLAIN_FIELDS, ids=lambda cls: cls.__name__)
def test_positional_and_keyword_spellings_deconstruct_identically(field_cls):
    positional = field_cls("Customer label")
    keyword = field_cls(verbose_name="Customer label")

    assert positional.deconstruct()[1:] == keyword.deconstruct()[1:]
    assert positional.deconstruct()[3]["verbose_name"] == "Customer label"


@pytest.mark.parametrize("field_cls", PLAIN_FIELDS, ids=lambda cls: cls.__name__)
def test_both_spellings_at_once_is_a_type_error(field_cls):
    with pytest.raises(TypeError, match="verbose_name"):
        field_cls("Positional", verbose_name="Keyword")


def test_lazy_translation_string_is_kept_lazy():
    label = _("Customer label")

    field = snapfields.SnapCharField(label, max_length=20)

    assert field.verbose_name is label


def test_positional_verbose_name_combines_with_snap_kwargs():
    field = snapfields.SnapCharField("Nickname", max_length=20, required=True, show_in_form=True)

    assert field.verbose_name == "Nickname"
    assert field.null is False
    assert field.show_in_form is True
    assert "show_in_form" not in field.deconstruct()[3]


def test_omitting_verbose_name_still_derives_it_from_the_attribute_name():
    field = snapfields.SnapCharField(max_length=20)
    field.set_attributes_from_name("delivery_note")

    assert field.verbose_name == "delivery note"
