# django-snapadmin

**Snap Admin or Automatically Django Admin** is a declarative Django package designed to eliminate the boilerplate of writing `admin.ModelAdmin` classes. By embedding admin configuration directly into your model fields, it automatically generates a feature-rich, beautiful, and highly functional Django admin interface.

---

## ✨ Features

- **Declarative Admin** – Define `list_display`, `search_fields`, and `list_filter` directly inside model fields.
- **Enhanced Change Logging** – Automatically logs detailed field-level changes (Old Value → New Value) in `LogEntry`.
- **Smart Filtering** – Built-in support for:
  - `DateRangeFilter`
  - `NumericRangeFilter`
  - AJAX-based `AutocompleteFilter`
- **Status Badges** – Easily create colorful, styled HTML badges for status fields.
- **Advanced Field Validation** – `SnapFileField` provides validation for file size, extensions, and encodings.
- **Dynamic Read-only Logic** – Fields can be marked as non-editable or non-updatable (locked after creation).
- **Functional Fields** – Create calculated display-only fields directly inside model definitions.

---

## 🚀 Installation

### From PyPI

```bash
pip install django-snapadmin
```

### From GitHub

```bash
pip install git+https://github.com/drofji/django-snapadmin.git
```

---

## ⚙️ Settings Configuration

Add the following to your `INSTALLED_APPS` in `settings.py` (order matters):

```python
INSTALLED_APPS = [
    "admin_interface",
    "colorfield",
    "django.contrib.admin",
    # ... other Django apps ...
    "rangefilter",
    "admin_auto_filters",
    "snapadmin",
    "your_app",
]
```

---

## 🛠 Usage

### 1️⃣ Basic Model Setup

Inherit from `SnapModel` and use `Snap` field types in your `models.py`.

```python
from django.utils.translation import gettext_lazy as _
from snapadmin import fields as dra_fields
from snapadmin import models as dra_models


class Product(dra_models.SnapModel):
    name = dra_fields.SnapCharField(
        max_length=200,
        searchable=True,
        show_in_list=True,
        verbose_name=_("Product Name"),
    )

    price = dra_fields.SnapDecimalField(
        max_digits=10,
        decimal_places=2,
        filterable=True,  # Enables NumericRangeFilter
        verbose_name=_("Price"),
    )
```

---

### 2️⃣ Status Badges & Update Logic

Create visually distinct status labels and handle update permissions easily.

```python
class Customer(dra_models.SnapModel):
    status = dra_fields.SnapCharField(
        max_length=20,
        choices=[
            ("active", "Active"),
            ("banned", "Banned"),
        ],
    )

    # Beautiful HTML badges in the list view
    status_display = dra_fields.SnapStatusBadgeField(
        field_name="status",
        choices=[
            dra_fields.SnapStatusBadgeFieldChoice(
                "active",
                text_html_color="#155724",
                background_html_color="#D4EDDA",
            ),
            dra_fields.SnapStatusBadgeFieldChoice(
                "banned",
                text_html_color="#721C24",
                background_html_color="#F8D7DA",
            ),
        ],
    )

    # Cannot be changed after creation
    internal_id = dra_fields.SnapUUIDField(updatable=False)
```

---

### 3️⃣ Automatic Admin Registration

In your `admin.py`, just call:

```python
from snapadmin.models import SnapModel

# Automatically registers all models inheriting from SnapModel
SnapModel.register_all_admins()
```

---

## 📂 Repository Structure

```
.
├── snapadmin/             # Core package source
│   ├── fields.py          # Custom Snap form/model fields
│   ├── models.py          # Base SnapModel and registration logic
│   └── static/            # Custom CSS/JS for the admin interface
├── demo/                  # Sample models and migrations
├── sandbox/               # Django settings and URLs
├── pyproject.toml         # Build system dependencies (Poetry)
└── README.md
```

---

## 🏗 Running the Example Project (Sandbox)

### 1️⃣ Clone the repository

```bash
git clone https://github.com/drofji/django-snapadmin.git
cd django-snapadmin
```

### 2️⃣ Setup environment

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows
pip install -e .
```

### 3️⃣ Run example

```bash
cd example
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open:

```
http://127.0.0.1:8000/admin/
```

---

## ⚙️ Field Flags Reference

Every `SnapField` supports the following parameters:

| Parameter     | Description                                           | Default |
|--------------|-------------------------------------------------------|---------|
| show_in_list | Adds field to `list_display`                          | True    |
| searchable   | Adds field to `search_fields`                         | False   |
| filterable   | Adds field to `list_filter` (supports range filters)  | False   |
| editable     | If False, field is globally read-only in admin        | True    |
| updatable    | If False, field is read-only after object creation    | True    |
| required     | If False, sets `null=True, blank=True` automatically  | False   |
| autocomplete | Enables AJAX search for ForeignKeys/Choices           | True    |

---

## 📜 License

MIT License

---

## 💡 Philosophy

> Write models once.  
> Get a powerful Django Admin automatically.  
> Zero boilerplate. Maximum productivity.
