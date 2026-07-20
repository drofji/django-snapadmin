"""
Licence data behind ``snapadmin_license_check`` — the runtime counterpart of
``THIRD_PARTY_NOTICES.md``.

A curated map records, for every dependency SnapAdmin declares (core + each optional extra), its
SPDX licence, the 🟢/🟡/🔴 tier, whether it is core or an extra, any *bundled* sub-licence caveat
(e.g. CKEditor 5 shipping GPL/commercial JS behind a BSD wrapper), and a short factual note. The
command overlays this on what is *actually installed* (via :mod:`importlib.metadata`) so a project
can see at a glance whether its install is safe for commercial/proprietary use.

This is an informational summary, **not legal advice** — licences change between versions; verify
against what you install and consult counsel for commercial use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from importlib import metadata


class Tier(Enum):
    """Commercial-usability tier of a licence (mirrors the THIRD_PARTY_NOTICES legend)."""

    PERMISSIVE = ("permissive", "🟢")
    WEAK_COPYLEFT = ("weak copyleft", "🟡")
    RESTRICTED = ("copyleft / commercial", "🔴")
    UNKNOWN = ("unknown", "⚪")

    @property
    def label(self) -> str:
        return self.value[0]

    @property
    def icon(self) -> str:
        return self.value[1]


_PERMISSIVE_TOKENS = ("MIT", "BSD", "APACHE", "ISC", "PSF", "PYTHON", "0BSD", "UNLICENSE", "ZLIB")


def classify(spdx: str) -> tuple[Tier, bool | None]:
    """Map an SPDX-ish licence string to ``(tier, commercial_ok)``.

    ``commercial_ok`` is ``True`` when the licence permits use in a closed-source/commercial product
    with only attribution-style obligations, ``False`` when it imposes copyleft obligations that
    conflict with proprietary distribution, and ``None`` when the string could not be classified.
    Order matters: AGPL/SSPL and LGPL are checked before the bare ``GPL`` substring.
    """
    text = (spdx or "").upper()
    if not text.strip():
        return Tier.UNKNOWN, None
    if "AGPL" in text or "SSPL" in text:
        return Tier.RESTRICTED, False
    if "LGPL" in text:
        return Tier.WEAK_COPYLEFT, True
    if "GPL" in text:
        return Tier.RESTRICTED, False
    if "MPL" in text:
        return Tier.WEAK_COPYLEFT, True
    if any(token in text for token in _PERMISSIVE_TOKENS):
        return Tier.PERMISSIVE, True
    return Tier.UNKNOWN, None


def _normalize(name: str) -> str:
    """PEP 503 distribution-name normalisation."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


@dataclass(frozen=True)
class LicenseInfo:
    """Curated licence record for one declared dependency."""

    package: str
    spdx: str
    extra: str | None = None  # None => core dependency; otherwise the extra's name
    bundles: str = ""  # note about a bundled sub-licence (e.g. CKEditor GPL JS)
    guidance: str = ""  # short factual note for the 🟡/🔴 ones

    @property
    def normalized(self) -> str:
        return _normalize(self.package)

    @property
    def tier(self) -> Tier:
        return classify(self.spdx)[0]

    @property
    def commercial_ok(self) -> bool | None:
        return classify(self.spdx)[1]

    @property
    def is_core(self) -> bool:
        return self.extra is None


_LGPL_NOTE = (
    "Weak copyleft — fine for proprietary use as an unmodified, dynamically-imported dependency."
)

# Mirrors THIRD_PARTY_NOTICES.md. A drift-guard test pins this set against pyproject.toml.
_CURATED: tuple[LicenseInfo, ...] = (
    # ── Core (base install — all permissive) ──
    LicenseInfo("Django", "BSD-3-Clause"),
    LicenseInfo("djangorestframework", "BSD-3-Clause"),
    LicenseInfo("drf-spectacular", "BSD-3-Clause"),
    LicenseInfo("django-filter", "BSD-3-Clause"),
    LicenseInfo("graphene-django", "MIT"),
    LicenseInfo("django-unfold", "MIT"),
    LicenseInfo("django-admin-rangefilter", "MIT"),
    LicenseInfo("structlog", "MIT OR Apache-2.0"),
    LicenseInfo("colorama", "BSD-3-Clause"),
    LicenseInfo("nh3", "MIT"),
    # ── Optional extras ──
    LicenseInfo("elasticsearch", "Apache-2.0", extra="elasticsearch"),
    LicenseInfo("celery", "BSD-3-Clause", extra="celery"),
    LicenseInfo("django-celery-beat", "BSD-3-Clause", extra="celery"),
    LicenseInfo("django-celery-results", "BSD-3-Clause", extra="celery"),
    LicenseInfo("django-extra-settings", "MIT", extra="extra-settings"),
    LicenseInfo(
        "django-admin-autocomplete-filter", "LGPL-3.0", extra="autocomplete-filter", guidance=_LGPL_NOTE
    ),
    LicenseInfo("paramiko", "LGPL-2.1", extra="backup", guidance=_LGPL_NOTE),
    LicenseInfo(
        "django-ckeditor-5",
        "GPL-2.0-or-later OR Commercial",
        extra="wysiwyg",
        bundles="The BSD Python wrapper ships CKEditor 5, which is GPL/commercial.",
        guidance="For a commercial product, obtain a CKEditor licence (free tier available) "
        "or supply your own widget.",
    ),
)

#: Curated records keyed by normalised package name.
CURATED: dict[str, LicenseInfo] = {info.normalized: info for info in _CURATED}


@dataclass(frozen=True)
class PackageStatus:
    """A curated package paired with whether it is installed and at what version."""

    info: LicenseInfo
    installed: bool
    version: str | None


def _installed_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def scan_curated() -> list[PackageStatus]:
    """Every curated package with its installed status/version, in declaration order."""
    return [
        PackageStatus(info=info, installed=(version := _installed_version(info.package)) is not None, version=version)
        for info in _CURATED
    ]


def _license_from_metadata(name: str) -> str:
    """Best-effort licence string for an installed distribution's own metadata."""
    try:
        meta = metadata.metadata(name)
    except metadata.PackageNotFoundError:
        return ""
    expression = meta.get("License-Expression")
    if expression:
        return expression
    for classifier in meta.get_all("Classifier") or []:
        if classifier.startswith("License ::"):
            return classifier.split("::")[-1].strip()
    return (meta.get("License") or "").strip()


def _requirement_name(requirement: str) -> str:
    """``"celery (>=5.3) ; extra == 'celery'"`` → ``"celery"``."""
    return re.split(r"[\s;\[<>=!~()]", requirement.strip(), maxsplit=1)[0]


def audit_uncurated() -> list[tuple[str, str, Tier]]:
    """SnapAdmin's declared requirements that are *not* in the curated map (best-effort).

    Empty in a source checkout (no installed ``django-snapadmin`` dist metadata); in a real install
    it surfaces any dependency added to packaging but not yet recorded here.
    """
    try:
        requires = metadata.requires("django-snapadmin") or []
    except metadata.PackageNotFoundError:
        return []
    seen: set[str] = set()
    out: list[tuple[str, str, Tier]] = []
    for requirement in requires:
        name = _requirement_name(requirement)
        norm = _normalize(name)
        if not norm or norm in CURATED or norm in seen:
            continue
        seen.add(norm)
        license_str = _license_from_metadata(name)
        out.append((name, license_str, classify(license_str)[0]))
    return out


def commercial_verdict(statuses: list[PackageStatus]) -> dict:
    """Overall commercial-compatibility verdict for the *installed* curated packages."""
    installed = [status for status in statuses if status.installed]
    concerns = [status for status in installed if status.info.commercial_ok is not True]
    core_all_permissive = all(
        status.info.tier is Tier.PERMISSIVE for status in installed if status.info.is_core
    )
    return {
        "commercial_ok": not concerns,
        "core_all_permissive": core_all_permissive,
        "concerns": [status.info.package for status in concerns],
    }


def is_compatible_with(info: LicenseInfo, target_spdx: str) -> bool | None:
    """Advisory: may ``info``'s package ship inside a project licensed ``target_spdx``?

    ``True`` = generally yes, ``False`` = generally no, ``None`` = review (case-by-case). This is a
    coarse tier rule, **not legal advice**: a permissive dependency fits any project; a copyleft
    project can absorb weak/strong copyleft too; a permissive project cannot ship strong copyleft.
    """
    target_tier, _ = classify(target_spdx)
    dep_tier = info.tier
    if dep_tier is Tier.PERMISSIVE:
        return True
    if dep_tier is Tier.UNKNOWN or target_tier is Tier.UNKNOWN:
        return None
    if target_tier is Tier.RESTRICTED:
        return True
    if dep_tier is Tier.WEAK_COPYLEFT:
        return None
    return False
