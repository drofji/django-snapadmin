"""Tests for :mod:`snapadmin.scaffold.render` (#SCAFF1b)."""

from __future__ import annotations

import stat

import pytest

from snapadmin.scaffold import render

#: Every placeholder render.py resolves. A leftover literal "$name" in a generated
#: file means a context key was renamed/typo'd on one side of a fragment/outer split —
#: safe_substitute would otherwise let that pass through silently (see render.py's
#: module docstring for why safe_substitute is used at all).
KNOWN_PLACEHOLDERS = [
    "project_name",
    "app_name",
    "app_class_name",
    "secret_key",
    "snapadmin_version",
    "database_block",
    "optional_services_block",
    "full_env_extra",
    "full_readme_extra",
    "full_requirements_extra",
]


def _assert_no_leftover_placeholders(paths):
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for name in KNOWN_PLACEHOLDERS:
            assert f"${name}" not in text, f"{path} still has an unresolved ${name}"


class TestGenerateProjectMinimal:
    def test_writes_expected_files(self, tmp_path):
        dest = tmp_path / "myshop"
        written = render.generate_project(dest, project_name="myshop", app_name="catalog", full=False)
        assert written  # non-empty
        rel = {p.relative_to(dest) for p in written}
        expected = {
            "manage.py",
            ".gitignore",
            ".env",
            "dist.env",
            "requirements.txt",
            "README.md",
            "myshop/__init__.py",
            "myshop/settings.py",
            "myshop/urls.py",
            "myshop/wsgi.py",
            "catalog/__init__.py",
            "catalog/apps.py",
            "catalog/models.py",
            "catalog/admin.py",
            "catalog/migrations/__init__.py",
            "catalog/migrations/0001_initial.py",
        }
        assert {str(p) for p in rel} == expected

    def test_does_not_write_docker_files(self, tmp_path):
        dest = tmp_path / "myshop"
        render.generate_project(dest, project_name="myshop", app_name="catalog", full=False)
        assert not (dest / "Dockerfile").exists()
        assert not (dest / "docker-compose.yml").exists()
        assert not (dest / ".dockerignore").exists()

    def test_settings_has_no_postgres_or_es_wiring(self, tmp_path):
        dest = tmp_path / "myshop"
        render.generate_project(dest, project_name="myshop", app_name="catalog", full=False)
        settings = (dest / "myshop" / "settings.py").read_text(encoding="utf-8")
        assert "POSTGRES_HOST" not in settings
        assert "ELASTICSEARCH_ENABLED" not in settings
        assert "REDIS_URL" not in settings
        assert 'ENGINE": "django.db.backends.sqlite3"' in settings

    def test_settings_substitutes_project_and_app_name(self, tmp_path):
        dest = tmp_path / "myshop"
        render.generate_project(dest, project_name="myshop", app_name="catalog", full=False)
        settings = (dest / "myshop" / "settings.py").read_text(encoding="utf-8")
        assert 'ROOT_URLCONF = "myshop.urls"' in settings
        assert 'WSGI_APPLICATION = "myshop.wsgi.application"' in settings
        assert '"catalog",' in settings

    def test_secret_key_is_fresh_and_nonempty(self, tmp_path):
        dest1 = tmp_path / "a"
        dest2 = tmp_path / "b"
        render.generate_project(dest1, project_name="a", app_name="catalog", full=False)
        render.generate_project(dest2, project_name="b", app_name="catalog", full=False)
        key1 = (dest1 / "a" / "settings.py").read_text(encoding="utf-8")
        key2 = (dest2 / "b" / "settings.py").read_text(encoding="utf-8")
        secret1 = [line for line in key1.splitlines() if line.startswith("SECRET_KEY")][0]
        secret2 = [line for line in key2.splitlines() if line.startswith("SECRET_KEY")][0]
        assert secret1 != secret2
        assert 'os.getenv("SECRET_KEY", "' in secret1

    def test_manage_py_is_executable(self, tmp_path):
        dest = tmp_path / "myshop"
        render.generate_project(dest, project_name="myshop", app_name="catalog", full=False)
        mode = (dest / "manage.py").stat().st_mode
        assert mode & stat.S_IXUSR

    def test_app_class_name_is_camel_cased(self, tmp_path):
        dest = tmp_path / "myshop"
        render.generate_project(dest, project_name="myshop", app_name="my_catalog", full=False)
        apps_py = (dest / "my_catalog" / "apps.py").read_text(encoding="utf-8")
        assert "class MyCatalogConfig(AppConfig):" in apps_py
        assert 'name = "my_catalog"' in apps_py

    def test_no_leftover_placeholders(self, tmp_path):
        dest = tmp_path / "myshop"
        written = render.generate_project(dest, project_name="myshop", app_name="catalog", full=False)
        _assert_no_leftover_placeholders(written)

    def test_model_has_no_wysiwyg_or_es_fields(self, tmp_path):
        """The worked example must run on the base install — no [wysiwyg]/[elasticsearch] extra."""
        dest = tmp_path / "myshop"
        render.generate_project(dest, project_name="myshop", app_name="catalog", full=False)
        models_py = (dest / "catalog" / "models.py").read_text(encoding="utf-8")
        assert "wysiwyg" not in models_py
        assert "es_index_enabled" not in models_py

    def test_env_has_no_postgres_or_es_lines(self, tmp_path):
        dest = tmp_path / "myshop"
        render.generate_project(dest, project_name="myshop", app_name="catalog", full=False)
        env_text = (dest / ".env").read_text(encoding="utf-8")
        assert "POSTGRES_HOST" not in env_text
        dist_text = (dest / "dist.env").read_text(encoding="utf-8")
        assert "POSTGRES_HOST" not in dist_text

    def test_requirements_has_no_docker_only_deps(self, tmp_path):
        dest = tmp_path / "myshop"
        render.generate_project(dest, project_name="myshop", app_name="catalog", full=False)
        req = (dest / "requirements.txt").read_text(encoding="utf-8")
        assert "django-snapadmin" in req
        assert "gunicorn" not in req
        assert "psycopg2" not in req

    def test_readme_has_no_docker_section(self, tmp_path):
        dest = tmp_path / "myshop"
        render.generate_project(dest, project_name="myshop", app_name="catalog", full=False)
        readme = (dest / "README.md").read_text(encoding="utf-8")
        assert "## Docker" not in readme


class TestGenerateProjectFull:
    def test_writes_docker_files_on_top_of_common(self, tmp_path):
        dest = tmp_path / "myshop"
        written = render.generate_project(dest, project_name="myshop", app_name="catalog", full=True)
        rel = {str(p.relative_to(dest)) for p in written}
        for extra in ("Dockerfile", "docker-compose.yml", ".dockerignore"):
            assert extra in rel
            assert (dest / extra).is_file()
        # still has everything the minimal manifest writes
        assert "myshop/settings.py" in rel
        assert "catalog/models.py" in rel

    def test_settings_has_postgres_and_es_wiring(self, tmp_path):
        dest = tmp_path / "myshop"
        render.generate_project(dest, project_name="myshop", app_name="catalog", full=True)
        settings = (dest / "myshop" / "settings.py").read_text(encoding="utf-8")
        assert "POSTGRES_HOST = os.getenv(" in settings
        assert 'ENGINE": "django.db.backends.postgresql"' in settings
        assert 'ENGINE": "django.db.backends.sqlite3"' in settings  # still the local fallback
        assert "ELASTICSEARCH_ENABLED = env_bool(" in settings
        assert "REDIS_URL = os.getenv(" in settings

    def test_dockerfile_and_compose_reference_project_name(self, tmp_path):
        dest = tmp_path / "myshop"
        render.generate_project(dest, project_name="myshop", app_name="catalog", full=True)
        dockerfile = (dest / "Dockerfile").read_text(encoding="utf-8")
        assert "myshop.wsgi:application" in dockerfile
        compose = (dest / "docker-compose.yml").read_text(encoding="utf-8")
        assert "myshop.settings" in compose
        assert "myshop.wsgi:application" in compose

    def test_dockerfile_keeps_docker_native_path_syntax(self, tmp_path):
        """$PATH is Docker's own ENV interpolation syntax, not one of ours — it must
        survive rendering untouched rather than raising or being blanked out."""
        dest = tmp_path / "myshop"
        render.generate_project(dest, project_name="myshop", app_name="catalog", full=True)
        dockerfile = (dest / "Dockerfile").read_text(encoding="utf-8")
        assert '$PATH' in dockerfile

    def test_compose_keeps_bash_default_syntax(self, tmp_path):
        dest = tmp_path / "myshop"
        render.generate_project(dest, project_name="myshop", app_name="catalog", full=True)
        compose = (dest / "docker-compose.yml").read_text(encoding="utf-8")
        assert "${POSTGRES_DB:-myshop}" in compose

    def test_env_has_postgres_redis_es_lines(self, tmp_path):
        dest = tmp_path / "myshop"
        render.generate_project(dest, project_name="myshop", app_name="catalog", full=True)
        env_text = (dest / ".env").read_text(encoding="utf-8")
        for token in ("POSTGRES_HOST=db", "REDIS_URL=redis://redis:6379/0", "ELASTICSEARCH_ENABLED=False"):
            assert token in env_text

    def test_requirements_has_docker_only_deps(self, tmp_path):
        dest = tmp_path / "myshop"
        render.generate_project(dest, project_name="myshop", app_name="catalog", full=True)
        req = (dest / "requirements.txt").read_text(encoding="utf-8")
        assert "gunicorn" in req
        assert "psycopg2-binary" in req

    def test_readme_has_docker_section(self, tmp_path):
        dest = tmp_path / "myshop"
        render.generate_project(dest, project_name="myshop", app_name="catalog", full=True)
        readme = (dest / "README.md").read_text(encoding="utf-8")
        assert "## Docker" in readme
        assert "docker compose up --build" in readme

    def test_no_leftover_placeholders(self, tmp_path):
        dest = tmp_path / "myshop"
        written = render.generate_project(dest, project_name="myshop", app_name="catalog", full=True)
        _assert_no_leftover_placeholders(written)


class TestInstalledVersion:
    def test_falls_back_when_package_not_found(self, monkeypatch):
        from importlib.metadata import PackageNotFoundError

        def _boom(name):
            raise PackageNotFoundError(name)

        monkeypatch.setattr(render, "_pkg_version", _boom)
        assert render._installed_version() == "0.0.0.dev0"


class TestCamelCase:
    @pytest.mark.parametrize(
        "name, expected",
        [
            ("catalog", "Catalog"),
            ("my_app", "MyApp"),
            ("shop_front_end", "ShopFrontEnd"),
        ],
    )
    def test_camel_case(self, name, expected):
        assert render._camel_case(name) == expected


# ─────────────────────────────────────────────────────────────────────────────
# The generated project must be installable from its own requirements.txt
# ─────────────────────────────────────────────────────────────────────────────

#: Django app label -> the distribution on PyPI that provides it. Only the third-party apps the
#: generated ``INSTALLED_APPS`` can contain need an entry; Django's own ``django.contrib.*`` and
#: the generated project's own app are skipped by the test.
_APP_TO_DISTRIBUTION = {
    "rest_framework": "djangorestframework",
    "drf_spectacular": "drf-spectacular",
    "django_filters": "django-filter",
    "graphene_django": "graphene-django",
    "unfold": "django-unfold",
    "django_ckeditor_5": "django-ckeditor-5",
    "django_celery_beat": "django-celery-beat",
    "django_celery_results": "django-celery-results",
    "extra_settings": "django-extra-settings",
    "admin_auto_filters": "django-admin-autocomplete-filter",
}


def _installed_apps(settings_text: str) -> list[str]:
    """The app labels the rendered settings module lists, ignoring commented-out lines."""
    body = settings_text.split("INSTALLED_APPS = [", 1)[1].split("]", 1)[0]
    apps = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith('"') or line.startswith("'"):
            apps.append(line.strip("\"',"))
    return apps


def _requested_extras(requirements_text: str) -> set[str]:
    """The extras the generated requirements.txt asks for on the django-snapadmin line."""
    for line in requirements_text.splitlines():
        line = line.strip()
        if not line.startswith("django-snapadmin"):
            continue
        if "[" not in line:
            return set()
        return {e.strip() for e in line.split("[", 1)[1].split("]", 1)[0].split(",")}
    raise AssertionError("requirements.txt does not depend on django-snapadmin at all")


@pytest.mark.parametrize("full", [False, True], ids=["minimal", "full"])
def test_requirements_cover_every_third_party_app_in_installed_apps(tmp_path, full):
    """``pip install -r requirements.txt`` then ``manage.py check`` must work in a clean venv.

    The generated ``INSTALLED_APPS`` lists ``rest_framework``, ``drf_spectacular``,
    ``django_filters`` and ``graphene_django``. Those moved behind the ``[api]``/``[graphql]``
    extras (#DEP1e) and a bare ``pip install django-snapadmin`` no longer brings them, so a
    requirements line without the extras produces a project that dies at ``django.setup()`` with
    ``ModuleNotFoundError: No module named 'rest_framework'`` — before any SnapAdmin check or
    friendly ``ImproperlyConfigured`` gets a chance to explain it. The scaffold's whole promise is
    that ``migrate`` and ``runserver`` work immediately with no manual edits.

    ``test_scaffold_e2e.py`` cannot catch this: it runs in the development environment, where every
    extra is already installed.
    """
    import tomllib
    import pathlib as _pathlib

    dest = tmp_path / "myshop"
    render.generate_project(dest, project_name="myshop", app_name="catalog", full=full)

    apps = _installed_apps((dest / "myshop" / "settings.py").read_text(encoding="utf-8"))
    extras_requested = _requested_extras((dest / "requirements.txt").read_text(encoding="utf-8"))

    pyproject = tomllib.loads(
        (_pathlib.Path(render.__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    )
    declared_extras: dict[str, list[str]] = pyproject["tool"]["poetry"]["extras"]
    # distribution -> the extras that ship it, straight from pyproject so a renamed extra fails here
    provided_by: dict[str, set[str]] = {}
    for extra, distributions in declared_extras.items():
        for distribution in distributions:
            provided_by.setdefault(distribution, set()).add(extra)

    uncovered = []
    for app in apps:
        if app.startswith("django.contrib.") or app in {"snapadmin", "catalog"}:
            continue
        distribution = _APP_TO_DISTRIBUTION.get(app)
        assert distribution, f"{app!r} is in the generated INSTALLED_APPS but this test has no " \
                             f"mapping for which distribution provides it — add one"
        shipping_extras = provided_by.get(distribution, set())
        if shipping_extras and not (shipping_extras & extras_requested):
            uncovered.append(f"{app} (needs one of {sorted(shipping_extras)})")

    assert not uncovered, (
        "the generated requirements.txt requests "
        f"{sorted(extras_requested) or 'no extras'}, which does not install: "
        + ", ".join(uncovered)
        + " — the generated project will not boot from its own requirements file"
    )
