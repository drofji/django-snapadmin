"""
Audit the licences of the SnapAdmin dependencies actually installed in this environment.

    python manage.py snapadmin_license_check                     # full report
    python manage.py snapadmin_license_check --json              # machine-readable (CI)
    python manage.py snapadmin_license_check --critical-only     # only 🟡/🔴 licences
    python manage.py snapadmin_license_check --compatible-with MIT   # per-package compatibility
    python manage.py snapadmin_license_check --verbose           # + uncurated deps + notes

It is the runtime counterpart of ``THIRD_PARTY_NOTICES.md``: a curated map overlaid on what is
actually installed. **Informational, not legal advice.** No vulnerability database is bundled — the
report points at ``pip-audit`` for a CVE scan and never claims "no known vulnerabilities".
"""

from __future__ import annotations

import importlib.util
import json

from django.core.management.base import BaseCommand

from snapadmin.licensing import (
    PackageStatus,
    Tier,
    audit_uncurated,
    commercial_verdict,
    is_compatible_with,
    scan_curated,
)


def _label(status: PackageStatus) -> str:
    info = status.info
    return f"[{info.extra}] {info.package}" if info.extra else info.package


def _installed_text(status: PackageStatus) -> str:
    return f"installed {status.version}" if status.installed else "not installed"


def _cve_note() -> dict:
    """A truthful vulnerability-scan line — never a fabricated 'no known vulnerabilities'."""
    for module, tool in (("pip_audit", "pip-audit"), ("safety", "safety")):
        if importlib.util.find_spec(module) is not None:
            return {"scanner": tool, "ran": False, "note": f"{tool} is installed — run `{tool}` to scan for CVEs."}
    return {
        "scanner": None,
        "ran": False,
        "note": "no vulnerability scanner installed — `pip install pip-audit` then run `pip-audit`.",
    }


class Command(BaseCommand):
    help = "Audit the licences of installed SnapAdmin dependencies for commercial usability."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON (for CI).")
        parser.add_argument(
            "--critical-only",
            action="store_true",
            dest="critical_only",
            help="Show only non-permissive (🟡/🔴) licences.",
        )
        parser.add_argument("--verbose", action="store_true", help="Include uncurated deps and notes.")
        parser.add_argument(
            "--compatible-with",
            dest="compatible_with",
            metavar="SPDX",
            help="Report each package's compatibility with a project under this licence (advisory).",
        )

    def handle(self, *args, **options):
        statuses = scan_curated()

        if options["compatible_with"]:
            self._render_compatibility(statuses, options["compatible_with"], options["as_json"])
            return

        shown = [s for s in statuses if not options["critical_only"] or s.info.tier is not Tier.PERMISSIVE]

        if options["as_json"]:
            self.stdout.write(json.dumps(self._payload(shown, statuses, options["verbose"]), indent=2))
            return

        self._render_text(shown, statuses, options["verbose"])

    # ── payload / renderers ──────────────────────────────────────────────────

    def _payload(self, shown, statuses, verbose) -> dict:
        payload = {
            "packages": [self._pkg_dict(s) for s in shown],
            "verdict": commercial_verdict(statuses),
            "vulnerability_scan": _cve_note(),
        }
        if verbose:
            payload["uncurated"] = [
                {"package": name, "license": lic, "tier": tier.label}
                for name, lic, tier in audit_uncurated()
            ]
        return payload

    def _pkg_dict(self, status: PackageStatus) -> dict:
        info = status.info
        return {
            "package": info.package,
            "license": info.spdx,
            "tier": info.tier.label,
            "category": "core" if info.is_core else f"extra:{info.extra}",
            "installed": status.installed,
            "version": status.version,
            "commercial_ok": info.commercial_ok,
            "bundles": info.bundles or None,
        }

    def _render_text(self, shown, statuses, verbose):
        self.stdout.write("📋 SnapAdmin licence audit\n")
        core = [s for s in shown if s.info.is_core]
        extras = [s for s in shown if not s.info.is_core]
        width = max((len(_label(s)) for s in shown), default=0)

        if core:
            self.stdout.write("Core dependencies")
            for status in core:
                self._write_row(status, width)
        if extras:
            self.stdout.write("Optional extras")
            for status in extras:
                self._write_row(status, width)
        if not shown:
            self.stdout.write("No packages match the filter.")

        verdict = commercial_verdict(statuses)
        if verdict["commercial_ok"]:
            self.stdout.write(self.style.SUCCESS("\nCommercial compatibility: ✓ OK — installed licences are proprietary-safe"))
        else:
            self.stdout.write(self.style.WARNING(
                "\nCommercial compatibility: ⚠ review — " + ", ".join(verdict["concerns"])
            ))

        if verbose:
            uncurated = audit_uncurated()
            self.stdout.write("\nUncurated declared dependencies:")
            if uncurated:
                for name, lic, tier in uncurated:
                    self.stdout.write(f"  {tier.icon} {name}  {lic or 'unknown'}")
            else:
                self.stdout.write("  none")

        self.stdout.write("\nVulnerability scan: " + _cve_note()["note"])

    def _write_row(self, status: PackageStatus, width: int):
        info = status.info
        self.stdout.write(
            f"  {info.tier.icon} {_label(status).ljust(width)}  "
            f"{info.spdx.ljust(28)}  {_installed_text(status)}"
        )
        if info.bundles:
            self.stdout.write(f"       bundles: {info.bundles}")
        if info.guidance:
            self.stdout.write(f"       {info.guidance}")

    def _render_compatibility(self, statuses, target, as_json):
        rows = []
        for status in statuses:
            verdict = is_compatible_with(status.info, target)
            rows.append((status.info.package, status.info.spdx, verdict))

        if as_json:
            self.stdout.write(json.dumps(
                {"target": target, "packages": [
                    {"package": p, "license": lic, "compatible": v} for p, lic, v in rows
                ]},
                indent=2,
            ))
            return

        self.stdout.write(f"📋 Compatibility with a project licensed {target} (advisory):\n")
        symbol = {True: "✓", False: "✗", None: "?"}
        for package, spdx, verdict in rows:
            self.stdout.write(f"  {symbol[verdict]} {package.ljust(32)} {spdx}")
        self.stdout.write("\n(? = review case-by-case. Advisory only — not legal advice.)")
