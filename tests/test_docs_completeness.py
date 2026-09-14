"""
tests/test_docs_completeness.py

#AUDIT1b — the end-of-round documentation completeness sweep, encoded as tests
rather than a note in the roadmap (the roadmap is the thing that rots).

Three surfaces go stale silently, each in its own way:

* a ``SNAPADMIN_*`` setting that exists in code but was never added to the
  demo project or the docs is undiscoverable — nobody reading either would
  know it exists;
* a system-check id (``snapadmin.W0xx``/``E0xx``/``I0xx``) registered in
  ``checks.py`` but never explained anywhere leaves an operator staring at a
  bare code with no context for what to do about it;
* an optional extra listed in one of README/docs/THIRD_PARTY_NOTICES/
  ``licensing.CURATED`` but not the others gives a different answer depending
  on which one a reader happens to open.

Found and fixed while writing this (2026-09-03): 3 settings missing from the
demo, 2 from the docs, 10 check ids explained nowhere — see the #AUDIT1b
roadmap entry for the full list.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPADMIN_ROOT = REPO_ROOT / "snapadmin"
DOCS_INDEX = REPO_ROOT / "docs" / "index.html"
DEMO_SETTINGS = REPO_ROOT / "demo" / "core" / "settings.py"


def _snapadmin_py_files():
    for path in SNAPADMIN_ROOT.rglob("*.py"):
        if "locale" in path.parts:
            continue
        yield path


def _settings_referenced_in_code() -> set[str]:
    """Every ``SNAPADMIN_*`` name read via ``get_setting(...)`` or
    ``getattr(settings, ...)`` anywhere in the shipped package."""
    pattern = re.compile(
        r'get_setting\(\s*"(SNAPADMIN_[A-Z0-9_]+)"|'
        r'getattr\(\s*settings,\s*"(SNAPADMIN_[A-Z0-9_]+)"'
    )
    names: set[str] = set()
    for path in _snapadmin_py_files():
        text = path.read_text(encoding="utf-8")
        for a, b in pattern.findall(text):
            names.add(a or b)
    return names


# ─────────────────────────────────────────────────────────────────────────────
# Every SNAPADMIN_* setting is discoverable from the demo project and the docs
# ─────────────────────────────────────────────────────────────────────────────

class TestSettingsDiscoverable:
    def test_every_setting_appears_in_the_demo_settings_module(self):
        code = DEMO_SETTINGS.read_text(encoding="utf-8")
        declared = set(re.findall(r"SNAPADMIN_[A-Z0-9_]+", code))
        missing = sorted(_settings_referenced_in_code() - declared)
        assert not missing, f"setting(s) read by the package but absent from demo/core/settings.py: {missing}"

    def test_every_setting_appears_in_the_docs(self):
        html = DOCS_INDEX.read_text(encoding="utf-8")
        documented = set(re.findall(r"SNAPADMIN_[A-Z0-9_]+", html))
        # SNAPADMIN_BACKUP_SFTP_PASSWORD is documented via the table's shared
        # "_HOST / _PORT / _PASSWORD" shorthand notation (matching every other
        # multi-credential row in that table), never spelled out in full.
        known_shorthand_only = {"SNAPADMIN_BACKUP_SFTP_PASSWORD"}
        missing = sorted(_settings_referenced_in_code() - documented - known_shorthand_only)
        assert not missing, f"setting(s) read by the package but absent from docs/index.html: {missing}"


# ─────────────────────────────────────────────────────────────────────────────
# Every registered system-check id is explained somewhere in the docs
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckIdsExplained:
    def _registered_ids(self) -> set[str]:
        text = (SNAPADMIN_ROOT / "checks.py").read_text(encoding="utf-8")
        return set(re.findall(r'id="(snapadmin\.[EWI]\d+)"', text))

    def test_every_registered_check_id_is_mentioned_in_the_docs(self):
        html = DOCS_INDEX.read_text(encoding="utf-8")
        # E004/E005 are covered only via the summary range "E003–E005" next to
        # E003's own individual explanation — an en-dash range, not the literal
        # id, so the substring check below can't see them directly.
        known_range_only = {"snapadmin.E004", "snapadmin.E005"}
        missing = sorted(
            check_id for check_id in self._registered_ids()
            if check_id not in known_range_only and check_id not in html
        )
        assert not missing, f"check id(s) registered but never explained in docs/index.html: {missing}"

    def test_known_exceptions_still_exist_as_registered_ids(self):
        """If these ever change shape (a real per-id mention added, or the id
        renumbered), the exception lists above need to shrink or move — this
        keeps that from being missed silently."""
        ids = self._registered_ids()
        assert {"snapadmin.E004", "snapadmin.E005", "snapadmin.E003"} <= ids


# ─────────────────────────────────────────────────────────────────────────────
# Every optional extra is listed consistently everywhere it must be
# ─────────────────────────────────────────────────────────────────────────────

#: Check ids that have been retired, mapped to the release that retired them.
#: A retired id is **never reused** (SECURITY.md's stable-surface rule: an
#: operator who silenced it, or who finds it in an old log, must not have it
#: mean something else later), so it must not reappear in ``checks.py`` — and
#: any documentation that still names it has to say it is gone rather than
#: describe a check that no longer runs.
RETIRED_CHECK_IDS = {"W014": "0.1.0b8"}

#: Every prose surface that may name a check id. ``docs/releases/`` is
#: deliberately out of scope: a release note describes the state at its own
#: release and is not rewritten afterwards.
_DOC_FILES_THAT_NAME_CHECK_IDS = (
    "README.md",
    "SECURITY.md",
    "docs/index.html",
    "llms.txt",
    "docs/llms.txt",
)

#: ``snapadmin.W021`` and a bare ``W021`` are the same id; ranges are written
#: as ``W001``–``W022``, so both endpoints are matched individually.
_CHECK_ID_IN_PROSE = re.compile(r"\b(?:snapadmin\.)?([EWI]\d{3})\b")


class TestNoCheckIdIsDocumentedAfterItsCheckIsGone:
    """The reverse of ``TestCheckIdsExplained``, and the half that was missing.

    That class asks "is every registered id explained?". Nothing asked the
    other way round, so prose describing a check that no longer exists could
    stay indefinitely — and did: ``W014`` was retired at 1.0 and kept its
    ``llms.txt`` entry for a whole release, telling an assistant about a warning
    the package cannot emit. (#FIX2 section C, closed in #QA1b.)
    """

    def _registered_ids(self) -> set[str]:
        text = (SNAPADMIN_ROOT / "checks.py").read_text(encoding="utf-8")
        return set(re.findall(r'id="snapadmin\.([EWI]\d+)"', text))

    def _documented_ids(self, filename: str) -> set[str]:
        return set(_CHECK_ID_IN_PROSE.findall((REPO_ROOT / filename).read_text(encoding="utf-8")))

    @pytest.mark.parametrize("filename", _DOC_FILES_THAT_NAME_CHECK_IDS)
    def test_every_documented_id_is_registered_or_listed_as_retired(self, filename):
        unknown = sorted(
            self._documented_ids(filename) - self._registered_ids() - set(RETIRED_CHECK_IDS)
        )
        assert not unknown, (
            f"{filename} names check id(s) that no check registers: {unknown}. "
            "Either the check was removed and the prose must go with it, or the id "
            "was retired and belongs in RETIRED_CHECK_IDS with its release."
        )

    def test_a_retired_id_is_never_registered_again(self):
        reused = sorted(set(RETIRED_CHECK_IDS) & self._registered_ids())
        assert not reused, (
            f"retired check id(s) back in checks.py: {reused}. A retired id keeps its "
            "old meaning in an operator's SILENCED_SYSTEM_CHECKS and in old logs."
        )

    @pytest.mark.parametrize("filename", _DOC_FILES_THAT_NAME_CHECK_IDS)
    def test_a_retired_id_is_only_named_alongside_its_retirement(self, filename):
        text = (REPO_ROOT / filename).read_text(encoding="utf-8")
        for check_id, released_in in RETIRED_CHECK_IDS.items():
            for match in re.finditer(rf"\b(?:snapadmin\.)?{check_id}\b", text):
                sentence = text[match.start() : match.start() + 200]
                assert "retired" in sentence and released_in in sentence, (
                    f"{filename} names {check_id} without saying it was retired in "
                    f"{released_in}; a reader would take it for a live check."
                )


class TestExtrasListedEverywhere:
    def _declared_extras(self) -> list[str]:
        # `tomllib` is stdlib only on 3.11+; the suite still runs on 3.10, where
        # skipping is right — this asserts a property of the repo's own files,
        # not of the package, so one interpreter checking it is enough.
        tomllib = pytest.importorskip("tomllib")

        with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
            data = tomllib.load(fh)
        return [name for name in data["tool"]["poetry"]["extras"] if name != "all"]

    @pytest.mark.parametrize("site", ["README.md", "docs/index.html", "THIRD_PARTY_NOTICES.md"])
    def test_every_extra_is_named_on_every_site(self, site):
        text = (REPO_ROOT / site).read_text(encoding="utf-8")
        missing = [
            extra for extra in self._declared_extras()
            if f"`{extra}`" not in text and f"<code>{extra}</code>" not in text
        ]
        assert not missing, f"extra(s) declared in pyproject.toml but missing from {site}: {missing}"


# ─────────────────────────────────────────────────────────────────────────────
# No dead links — internal anchors and (per #BUG1) README links stay absolute
# ─────────────────────────────────────────────────────────────────────────────

class TestNoDeadLinks:
    def test_every_internal_anchor_link_in_docs_index_resolves(self):
        """href="#foo" with no matching id="foo" anywhere on the page (found and
        fixed one real case, #export -> #async-export, while writing this)."""
        html = DOCS_INDEX.read_text(encoding="utf-8")
        anchors = set(re.findall(r'id="([^"]+)"', html))
        hrefs = set(re.findall(r'href="#([^"]+)"', html))
        missing = sorted(hrefs - anchors)
        assert not missing, f"docs/index.html links to an anchor that does not exist: {missing}"

    def test_readme_has_no_relative_links(self):
        """#BUG1: a relative link (e.g. "(LICENSE)") 404s on PyPI, which renders
        README.md with no access to the rest of the repository — every link must
        be absolute, an in-page anchor, or a mailto:. Found and fixed one real
        case (the license badge) while writing this."""
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        links = re.findall(r"\]\(([^)]+)\)", readme)
        relative = [link for link in links if not link.startswith(("http://", "https://", "#", "mailto:"))]
        assert not relative, f"relative (PyPI-broken) link(s) in README.md: {relative}"

    def test_every_github_blob_link_in_docs_index_points_at_a_real_file(self):
        html = DOCS_INDEX.read_text(encoding="utf-8")
        links = re.findall(r'https://github\.com/drofji/django-snapadmin/blob/main/([^"\s]+)', html)
        missing = sorted({path for path in links if not (REPO_ROOT / path).is_file()})
        assert not missing, f"docs/index.html links to a repo file that does not exist: {missing}"


# ─────────────────────────────────────────────────────────────────────────────
# Every env-driven demo setting is discoverable from dist.env
# ─────────────────────────────────────────────────────────────────────────────

class TestDistEnvParity:
    """``demo/dist.env`` is the file a new deployment copies to ``.env``.

    ``TestSettingsDiscoverable`` above already pins that every setting the *package* reads appears
    in ``demo/core/settings.py``. This pins the next hop: a setting the demo settings module reads
    **from the environment** has to be named in ``dist.env`` too, or someone starting from that file
    has no way to discover it exists. A commented-out example line counts — several settings are
    genuinely optional and documenting them switched off is the right shape; the failure this
    catches is the setting mentioned nowhere at all.
    """

    #: Settings whose value is a dict or a list of dicts. They are configured in
    #: ``settings.py`` directly because an env var cannot express them, so they read nothing from
    #: the environment and never reach the check below — listed here so the exclusion is a stated
    #: decision rather than an accident of the regex.
    STRUCTURED_ONLY = {
        "SNAPADMIN_MASKED_FIELDS",
        "SNAPADMIN_MASKING_RULES",
        "SNAPADMIN_EXPORT_SOURCES",
        "SNAPADMIN_NESTED_APPS",
        "SNAPADMIN_HIDDEN_APPS",
        "SNAPADMIN_APP_LABELS",
        "SNAPADMIN_SSO_PROVIDERS",
        "SNAPADMIN_SSO_ALLOWED_HOSTS",
        "SNAPADMIN_ALERT_WEBHOOKS",
        "SNAPADMIN_TENANT_RESOLVER",
        "SNAPADMIN_TENANT_USER_RESOLVER",
    }

    def _env_driven_settings(self) -> set[str]:
        text = (REPO_ROOT / "demo" / "core" / "settings.py").read_text(encoding="utf-8")
        names: set[str] = set()
        for reader in ("os.getenv", "env_bool", "env_int", "env_list"):
            names |= set(
                re.findall(
                    re.escape(reader) + r"\(\s*['\"](SNAPADMIN_[A-Z0-9_]+)['\"]", text
                )
            )
        return names

    def test_every_env_driven_setting_is_named_in_dist_env(self):
        dist_env = (REPO_ROOT / "demo" / "dist.env").read_text(encoding="utf-8")
        declared = set(re.findall(r"^#?\s*(SNAPADMIN_[A-Z0-9_]+)=", dist_env, re.MULTILINE))
        missing = sorted(self._env_driven_settings() - declared - self.STRUCTURED_ONLY)
        assert not missing, (
            "setting(s) read from the environment by demo/core/settings.py but never named in "
            f"demo/dist.env, so a deployment copying that file cannot discover them: {missing}"
        )

    def test_the_structured_exclusions_are_really_not_env_driven(self):
        """Keeps the exclusion list honest — an entry that starts reading the environment
        must move out of it rather than sit there granting a permanent exemption."""
        leaked = sorted(self.STRUCTURED_ONLY & self._env_driven_settings())
        assert not leaked, (
            f"{leaked} now read the environment, so they belong in dist.env — "
            "remove them from STRUCTURED_ONLY"
        )
