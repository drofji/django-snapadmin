"""
tests/test_testing_docs_truth.py

The testing documentation, asserted rather than trusted (#DOC10e).

``README.md`` and ``docs/index.html`` both describe how this suite is built:
which layers exist, which files carry them, how many tests each group holds,
which checks CI runs — and, just as deliberately, which checks it does **not**
run yet. Every one of those statements rots silently. A renamed test file leaves
a dangling name on a page nobody re-reads; a count quoted once becomes wrong the
next time a suite grows; and the most damaging direction of all is a check that
gets *added* to CI while the page still lists it under "not in place yet", or —
worse — a page that claims a check nobody wired up.

So this module pins all four directions:

1. every ``tests/test_*.py`` named in the docs exists;
2. every quoted count is a **floor** that a real ``pytest --collect-only -q``
   run still meets — the counts are read from that run, never from arithmetic
   (#DOC8a's rule), and never from each other;
3. every check the docs claim is running really is wired up — the coverage gate,
   the random-order plugin, the six-way matrix, the real-services job, the
   marker that deselects the live-Elasticsearch tests;
4. every check the docs call absent really is absent. This is the assertion with
   the most leverage: the day somebody adds Ruff or mutation testing to CI, this
   test fails and points at the paragraph that has to change with it.

Floors are deliberately round and deliberately below the measured value. When a
suite grows past the next round number, raising the floor here and in both
documents is the change — never lowering it to match a shrinking suite, which
would mean tests were deleted and the page quietly relabelled.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
DOCS_INDEX = REPO_ROOT / "docs" / "index.html"
PYPROJECT = REPO_ROOT / "pyproject.toml"
PYTEST_INI = REPO_ROOT / "pytest.ini"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


# ─────────────────────────────────────────────────────────────────────────────
# One real collection run, shared by every test below
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def collected_per_file() -> Counter:
    """``{"tests/test_x.py": n}`` from a real ``--collect-only`` run.

    ``--override-ini=addopts=`` drops the ``-m "not real_es"`` default so the
    live-Elasticsearch tests are counted too: the documented total is what the
    suite *contains*, and the deselection is described separately.
    ``-p no:randomly`` keeps the nested run's output stable and skips the
    reseeding a shuffle would do inside an already-running suite.
    """
    completed = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "--collect-only", "-q",
            "--override-ini=addopts=",
            "-p", "no:randomly",
            "-p", "no:cacheprovider",
            str(REPO_ROOT / "tests"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, (
        f"collection failed ({completed.returncode}):\n{completed.stdout[-4000:]}"
    )
    counts: Counter = Counter()
    for line in completed.stdout.splitlines():
        match = re.match(r"^(tests/test_[A-Za-z0-9_]+\.py)::", line.strip())
        if match:
            counts[match.group(1)] += 1
    assert counts, f"parsed no test ids out of:\n{completed.stdout[:2000]}"
    return counts


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


#: A four-figure count stated *about tests* — the number, an optional "+", then
#: the word itself. Deliberately requires the word to follow rather than merely
#: sit nearby: the first version of this used a 40-character proximity window
#: and flagged the Elasticsearch port in "localhost:9200" because "Each test
#: owns its index" happened to be in the same sentence. A check that cries wolf
#: is a check somebody eventually loosens.
_TEST_COUNT_CLAIM = re.compile(r"\b(\d{1,3}(?:,\d{3})+|\d{4,})\+?\s+tests?\b", re.IGNORECASE)


def _suite_wide_test_claims(text: str) -> set[int]:
    """Every four-figure number either document states *about tests*.

    Tags and emphasis are stripped first so that the docs site's
    ``<strong>4,964 tests</strong>`` and the README's ``**4,900+ tests**`` are
    both read the way a reader sees them rather than the way the markup nests
    them.
    """
    plain = re.sub(r"<[^>]+>", " ", text).replace("*", " ")
    plain = re.sub(r"\s+", " ", plain)
    return {int(raw.replace(",", "")) for raw in _TEST_COUNT_CLAIM.findall(plain)}


# ─────────────────────────────────────────────────────────────────────────────
# 1. Every test file the docs name must exist
# ─────────────────────────────────────────────────────────────────────────────

_NAMED_FILE = re.compile(r"tests/test_[A-Za-z0-9_]+\.py")


class TestNamedTestFilesExist:
    """A docs page that names a file which was renamed teaches a lie."""

    @pytest.mark.parametrize("document", ["README.md", "docs/index.html"])
    def test_every_named_test_file_exists(self, document):
        named = sorted(set(_NAMED_FILE.findall(_read(REPO_ROOT / document))))
        assert named, f"{document} names no test file at all — did the section move?"
        missing = [name for name in named if not (REPO_ROOT / name).is_file()]
        assert not missing, f"{document} names test file(s) that no longer exist: {missing}"

    def test_both_documents_name_the_end_to_end_smoke_path(self):
        """The one file whose disappearance would be least noticeable and most
        expensive: nothing else walks admin -> DB -> audit -> REST in one go."""
        for document in ("README.md", "docs/index.html"):
            assert "tests/test_critical_path_smoke.py" in _read(REPO_ROOT / document), (
                f"{document} no longer names the end-to-end smoke test"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Every quoted count is a floor a real collection run still meets
# ─────────────────────────────────────────────────────────────────────────────

#: ``name -> (files, floor)``. The floor is what the documents quote; the files
#: are what has to add up to it. Measured 2026-09-15 at 4,964 collected.
_GROUP_FLOORS: dict[str, tuple[tuple[str, ...], int]] = {
    "contract suites": (
        (
            "tests/test_public_contract.py",
            "tests/test_public_surface_snapshot.py",
            "tests/test_api_surface_defaults.py",
        ),
        340,
    ),
    "hand-curated public contract": (("tests/test_public_contract.py",), 260),
    "documentation-truth": (
        (
            "tests/test_ai_entry_points.py",
            "tests/test_docs_site.py",
            "tests/test_docs_completeness.py",
            "tests/test_version_sync.py",
        ),
        140,
    ),
    "AI entry points": (("tests/test_ai_entry_points.py",), 90),
    "diagnostics": (
        (
            "tests/test_diagnostics.py",
            "tests/test_diagnostics_api.py",
            "tests/test_diagnostics_backups.py",
            "tests/test_diagnostics_checks.py",
            "tests/test_diagnostics_database.py",
            "tests/test_diagnostics_elasticsearch.py",
            "tests/test_diagnostics_features.py",
            "tests/test_diagnostics_graphql.py",
            "tests/test_diagnostics_inventory.py",
            "tests/test_diagnostics_render.py",
            "tests/test_diagnostics_runtime.py",
        ),
        220,
    ),
    "feature-adoption audit": (("tests/test_diagnostics_features.py",), 80),
    "startup system checks": (
        (
            "tests/test_checks.py",
            "tests/test_check_tenant_scoping.py",
            "tests/test_check_fetch_by_max_values.py",
        ),
        200,
    ),
    "licence audit": (
        ("tests/test_licensing.py", "tests/test_license_check_command.py"),
        60,
    ),
    "REST surface": (("tests/test_model_api.py",), 190),
    "field encryption end to end": (
        (
            "tests/test_encryption_cipher.py",
            "tests/test_encryption_keys.py",
            "tests/test_encryption_blind_index.py",
            "tests/test_encryption_leak_surfaces.py",
            "tests/test_encrypted_fields.py",
            "tests/test_encrypt_fields_command.py",
        ),
        400,
    ),
    "field behaviour and encrypted fields": (
        ("tests/test_fields.py", "tests/test_encrypted_fields.py"),
        250,
    ),
    "backups": (("tests/test_backup.py", "tests/test_backup_sharding.py"), 170),
    "restore": (("tests/test_restore.py", "tests/test_snapshot.py"), 90),
    "export": (("tests/test_export.py",), 130),
    "PII masking": (("tests/test_pii_masking.py",), 120),
    "internationalisation": (("tests/test_i18n.py", "tests/test_demo_i18n.py"), 110),
    "API tokens": (
        (
            "tests/test_api_token.py",
            "tests/test_api_token_hashing.py",
            "tests/test_api_token_validation.py",
        ),
        100,
    ),
    "settings resolution": (("tests/test_conf.py",), 90),
    "data retention": (
        ("tests/test_data_retention.py", "tests/test_retention_per_row_date.py"),
        80,
    ),
    "bulk import": (("tests/test_importing.py",), 70),
    "alert channels": (("tests/test_alert_channels.py",), 70),
    "audit trail": (("tests/test_audit_trail.py",), 60),
    "offline mode": (("tests/test_offline.py",), 60),
    "multi-tenancy": (
        (
            "tests/test_tenancy.py",
            "tests/test_tenancy_es.py",
            "tests/test_tenancy_audit.py",
            "tests/test_tenancy_admin.py",
        ),
        60,
    ),
}

#: ``module -> how many ``# pragma: no cover`` lines it carries``. Frozen at the
#: twelve both documents describe (down from eighteen: the five "defensive" guards
#: and the urlsplit branch turned out reachable and are now tested — #RM1a); see
#: the test below for why it is a ceiling rather than a ban.
_COVERAGE_PRAGMAS: dict[str, int] = {
    "snapadmin/admin_gen.py": 2,          # the Unfold-absent import branch
    "snapadmin/alerts.py": 2,             # two abstract methods
    "snapadmin/api/exceptions.py": 1,     # if TYPE_CHECKING
    "snapadmin/auth_admin.py": 1,         # unreachable once is_installed() passed
    "snapadmin/backup.py": 2,             # optional-dependency guards
    "snapadmin/checks.py": 1,             # celery is the [celery] extra
    "snapadmin/fields.py": 1,             # abstract; both subclasses define it
    "snapadmin/masking.py": 1,            # if TYPE_CHECKING
    "snapadmin/tasks.py": 1,              # covered by importing this file with celery hidden
}
_DOCUMENTED_PRAGMA_TOTAL = 12

#: The suite-wide floors both documents quote.
_SUITE_FLOOR = 5_000
_FILE_FLOOR = 153
#: The live-Elasticsearch tests, deselected by default and described as "twelve".
_REAL_ES_FLOOR = 12


class TestQuotedCountsAreMetFloors:
    """Every number on the page is a floor a collection run still clears."""

    @pytest.mark.parametrize("group", sorted(_GROUP_FLOORS))
    def test_group_still_meets_its_documented_floor(self, group, collected_per_file):
        files, floor = _GROUP_FLOORS[group]
        for name in files:
            assert (REPO_ROOT / name).is_file(), f"{group}: {name} no longer exists"
            assert name in collected_per_file, f"{group}: {name} collected nothing"
        actual = sum(collected_per_file[name] for name in files)
        assert actual >= floor, (
            f"{group} collects {actual} tests but the docs quote a floor of {floor}+. "
            f"Tests were removed, or a file was renamed out of this group — "
            f"diagnose that before touching the floor."
        )

    def test_the_whole_suite_still_meets_its_documented_floor(self, collected_per_file):
        actual = sum(collected_per_file.values())
        assert actual >= _SUITE_FLOOR, (
            f"the suite collects {actual} tests, below the documented floor of "
            f"{_SUITE_FLOOR}+ in README.md and docs/index.html"
        )

    def test_the_file_count_still_meets_its_documented_floor(self, collected_per_file):
        actual = len(collected_per_file)
        assert actual >= _FILE_FLOOR, (
            f"{actual} test files collect tests, below the documented {_FILE_FLOOR}"
        )

    def test_the_live_elasticsearch_suite_is_the_documented_size(self, collected_per_file):
        """Both pages say "twelve" rather than a floor, because the number is
        small enough that a reader would notice it being wrong."""
        actual = collected_per_file["tests/test_elasticsearch_live.py"]
        assert actual >= _REAL_ES_FLOOR, (
            f"the live-Elasticsearch suite holds {actual} tests; both documents say twelve"
        )

    @pytest.mark.parametrize("document", ["README.md", "docs/index.html"])
    def test_no_suite_wide_claim_anywhere_exceeds_what_is_collected(self, document, collected_per_file):
        """The inverse of the floors above, and the hole that writing them left.

        Pinning ``"4,900+ tests" in README.md`` proves the phrase is still
        somewhere on the page — it cannot notice a *second* claim inflating to
        "9,900+ tests" elsewhere in the same document, which is precisely how a
        count drifts upward: someone refreshes one occurrence and guesses. So
        this sweeps every four-figure count either document states *about
        tests*, and holds each one to what a real collection run returns.

        Deliberately narrow: the first version of this swept every four-figure
        ``N+`` on the page and flagged "11,000+ statements" and "3,300+
        branches", which are legitimately larger than the test count and say
        nothing about it. A check that cries wolf gets loosened, so it only
        looks at numbers standing next to the word "test".
        """
        actual = sum(collected_per_file.values())
        claims = _suite_wide_test_claims(_read(REPO_ROOT / document))
        assert claims, f"{document} makes no four-figure test count at all — did the section move?"
        overstated = sorted(claim for claim in claims if claim > actual)
        assert not overstated, (
            f"{document} claims {overstated} test(s) but only {actual} are collected. Docs counts "
            f"are floors read off `pytest --collect-only -q`, never arithmetic and never rounded up."
        )

    @pytest.mark.parametrize(
        "quoted, document",
        [
            ("5,000+ tests", "README.md"),
            ("153 files", "README.md"),
            ("340+", "README.md"),
            ("260+ checks", "README.md"),
            ("140+ tests", "README.md"),
            ("220+ tests", "README.md"),
            ("90+ checks", "README.md"),
            ("Twelve lines across nine modules", "README.md"),
            ("twelve lines across nine modules", "docs/index.html"),
            ("5,364", "docs/index.html"),
            ("160 files", "docs/index.html"),
            ("11,702", "docs/index.html"),
            ("3,464", "docs/index.html"),
        ],
    )
    def test_the_document_still_carries_the_number_this_module_pins(self, quoted, document):
        """The floors above only mean something while the page still quotes
        them; a rewrite that drops the number must come here too."""
        assert quoted in _read(REPO_ROOT / document), (
            f"{document} no longer quotes {quoted!r}, which this module pins as a floor"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Every check the docs claim is running really is wired up
# ─────────────────────────────────────────────────────────────────────────────

class TestClaimedChecksReallyRun:
    """A documented gate that nobody wired up is the false green this project
    bans everywhere else."""

    def test_the_line_coverage_gate_is_in_the_workflow(self):
        workflow = _read(WORKFLOWS / "test.yml")
        assert "--cov=snapadmin" in workflow and "--cov-fail-under=100" in workflow, (
            "both documents promise a 100% line-coverage gate in CI"
        )

    def test_the_coverage_pragmas_are_exactly_the_documented_twelve(self):
        """Both pages say twelve lines across nine modules carry one.

        A frozen list rather than a ban, because a ban would have been a lie:
        writing this module is what found the README claiming there were none
        while eighteen shipped. The list may **shrink** freely — closing one is
        always progress — but it may not grow without a deliberate edit here and
        a matching edit to the number on both pages, which is the whole point.
        """
        found = {
            str(path.relative_to(REPO_ROOT)): path.read_text(encoding="utf-8").count(
                "pragma: no cover"
            )
            for path in sorted((REPO_ROOT / "snapadmin").rglob("*.py"))
            if "pragma: no cover" in path.read_text(encoding="utf-8")
        }
        added = {
            name: count
            for name, count in found.items()
            if count > _COVERAGE_PRAGMAS.get(name, 0)
        }
        assert not added, (
            f"new # pragma: no cover line(s) in {added}. A pragma hides a line from the only "
            f"gate this project enforces — write the test instead. If the exclusion is genuinely "
            f"right, say why on the line, raise the count here, and update the figure in "
            f"README.md and docs/index.html in the same commit."
        )
        assert sum(found.values()) <= _DOCUMENTED_PRAGMA_TOTAL, (
            f"{sum(found.values())} coverage pragmas ship, above the documented "
            f"{_DOCUMENTED_PRAGMA_TOTAL}"
        )

    def test_every_coverage_pragma_states_a_reason(self):
        """Both pages say "each with a written reason". Two had none until this
        module went looking, which is exactly the drift a bare pragma invites."""
        reasonless = [
            f"{path.relative_to(REPO_ROOT)}:{number}"
            for path in sorted((REPO_ROOT / "snapadmin").rglob("*.py"))
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if "pragma: no cover" in line and "pragma: no cover - " not in line
        ]
        assert not reasonless, (
            f"coverage pragma(s) with no stated reason: {reasonless}. Write "
            f"`# pragma: no cover - why` so the next reader can judge it."
        )

    def test_random_order_is_the_default_and_needs_no_flag(self):
        assert "pytest-randomly" in _read(PYPROJECT), (
            "both documents say every run is randomly ordered"
        )
        assert "no:randomly" not in _read(PYTEST_INI), (
            "pytest.ini disables the random ordering the docs promise"
        )

    def test_the_matrix_is_the_six_jobs_the_docs_describe(self):
        workflow = _read(WORKFLOWS / "test.yml")
        for version in ("3.10", "3.11", "3.12", "3.13"):
            assert f'"{version}"' in workflow, f"the docs promise Python {version} in the matrix"
        for django in ("5.2", "6.0"):
            assert f'"{django}"' in workflow, f"the docs promise Django {django} in the matrix"

    def test_the_real_services_job_runs_the_versions_the_docs_name(self):
        workflow = _read(WORKFLOWS / "test.yml")
        assert "postgres:16" in workflow, "both documents name PostgreSQL 16"
        assert "elasticsearch:8.13.0" in workflow, "both documents name Elasticsearch 8.13.0"
        assert "-m real_es" in workflow, (
            "the docs say the live-Elasticsearch suite runs in that job"
        )

    def test_the_live_elasticsearch_suite_is_deselected_by_default(self):
        assert 'not real_es' in _read(PYTEST_INI), (
            "the docs promise that a plain `pytest` starts no container"
        )

    def test_the_empty_assertion_guard_exists(self):
        assert (REPO_ROOT / "tests" / "test_assertions_can_fail.py").is_file(), (
            "both documents describe a guard that fails on an assertion no outcome could falsify"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Every check the docs call absent really is absent
# ─────────────────────────────────────────────────────────────────────────────

#: ``tool -> the paragraph that has to change when it arrives``. Listed under
#: "What is not in place yet" in both documents.
_DOCUMENTED_AS_ABSENT = {
    "mutmut": "Mutation testing",
    "cosmic-ray": "Mutation testing",
    "mutatest": "Mutation testing",
    "playwright": "Browser E2E",
    "cypress": "Browser E2E",
}


class TestChecksDocumentedAsAbsentReallyAreAbsent:
    """The direction that actually rots. Adding a tool is a happy event; adding
    it while two documents still say it does not exist is how a page starts
    lying. This fails the moment one arrives, and names the paragraph to fix.
    """

    @pytest.mark.parametrize("tool", sorted(_DOCUMENTED_AS_ABSENT))
    def test_the_tool_is_not_a_dependency(self, tool):
        declared = _read(PYPROJECT).lower()
        assert f'\n{tool} = ' not in declared, (
            f"{tool!r} is now a declared dependency, but both documents still list it under "
            f"{_DOCUMENTED_AS_ABSENT[tool]!r} as not in place. Move it into the table of checks "
            f"that run — with the command that runs it — in the same change."
        )

    @pytest.mark.parametrize("tool", sorted(_DOCUMENTED_AS_ABSENT))
    def test_no_ci_job_runs_the_tool(self, tool):
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            assert tool not in _read(workflow).lower(), (
                f"{workflow.name} now runs {tool!r}, but both documents still list it under "
                f"{_DOCUMENTED_AS_ABSENT[tool]!r} as not in place. Document the job in the same "
                f"change — a gate nobody documented is a gate nobody runs."
            )

    def test_static_analysis_runs_as_the_docs_describe(self):
        """#QA1c — the pages say Ruff lint + format are blocking and mypy is
        advisory. Pin both halves: a Ruff step marked continue-on-error, or a
        mypy step that silently became blocking, makes those sentences false."""
        workflow = _read(WORKFLOWS / "test.yml")
        job = workflow.split("  static-analysis:", 1)[1].split("\n  real-services:", 1)[0]
        assert "ruff check snapadmin" in job
        assert "ruff format --check snapadmin" in job
        mypy_step = job.split("- name: mypy", 1)[1]
        assert "continue-on-error: true" in mypy_step, "the docs call mypy advisory"
        ruff_steps = job.split("- name: mypy", 1)[0]
        assert "continue-on-error" not in ruff_steps, "the docs call Ruff blocking"
        for tool in ("ruff", "mypy", "django-stubs"):
            assert f"\n{tool} = " in _read(PYPROJECT), f"{tool} is not a declared dev dependency"

    def test_property_and_fuzz_tests_run_as_the_docs_describe(self):
        """#QA1d (3)/(4) — all three pages say ``hypothesis`` runs 100 examples
        per test locally and 300 in CI, inside the ordinary run. Pin every
        half of that sentence: the dependency, both profiles and their budgets,
        the CI flag on both jobs that run the suite, and the files."""
        assert "\nhypothesis = " in _read(PYPROJECT), "hypothesis is not a declared dev dependency"
        assert "hypothesis>=" in _read(REPO_ROOT / "demo" / "requirements.txt"), (
            "CI installs from demo/requirements.txt, which does not carry hypothesis"
        )
        conftest = _read(REPO_ROOT / "tests" / "conftest.py")
        assert 'register_profile("default", max_examples=100' in conftest
        assert 'register_profile("ci", max_examples=300' in conftest
        workflow = _read(WORKFLOWS / "test.yml")
        matrix_job = workflow.split("  static-analysis:", 1)[0]
        services_job = workflow.split("  real-services:", 1)[1]
        assert "--hypothesis-profile=ci" in matrix_job, "the matrix does not run the CI profile"
        assert "--hypothesis-profile=ci" in services_job, "the PostgreSQL job does not run the CI profile"
        tests_dir = REPO_ROOT / "tests"
        assert sorted(tests_dir.glob("test_properties_*.py")), "no property-based test file"
        assert sorted(tests_dir.glob("test_fuzz_*.py")), "no fuzz test file"
        for document in ("README.md", "docs/index.html", "llms.txt"):
            text = _read(REPO_ROOT / document)
            assert "hypothesis" in text, f"{document} does not name the property/fuzz tool"
            assert "300 in CI" in text, f"{document} does not state the CI example budget"

    @pytest.mark.parametrize("document", ["README.md", "docs/index.html"])
    def test_a_running_check_is_not_listed_as_absent(self, document):
        """The inverse of the gap list: once a layer runs, the "not in place
        yet" section must stop listing it, or the page contradicts itself."""
        text = _read(REPO_ROOT / document)
        gap_section = text.split("What is not in place yet", 1)[1][:6000]
        assert "Property-based" not in gap_section, f"{document} still lists property-based tests as absent"

    def test_branch_coverage_is_gated_as_the_docs_say(self):
        """#QA1d — all three pages now say branch coverage is gated at 100%. The
        day the flag leaves CI, that wording is wrong in three places."""
        workflow = _read(WORKFLOWS / "test.yml")
        assert "--cov-branch" in workflow and "--cov-fail-under=100" in workflow, (
            "CI no longer gates on branch coverage, but README.md, docs/index.html and "
            "llms.txt all say it does"
        )
        for document in ("README.md", "docs/index.html", "llms.txt"):
            assert "measured, not gated" not in _read(REPO_ROOT / document).lower(), document

    @pytest.mark.parametrize(
        "phrase, document",
        [
            ("What is not in place yet", "README.md"),
            ("Mutation testing", "README.md"),
            ("Browser E2E", "README.md"),
            ("What is not in place yet", "docs/index.html"),
            ("Mutation testing", "docs/index.html"),
            ("Browser E2E", "docs/index.html"),
        ],
    )
    def test_the_honest_gap_list_is_still_on_the_page(self, phrase, document):
        """Deleting the "not yet" list would make both pages read as if every
        layer were covered — the failure mode this whole module exists for."""
        assert phrase in _read(REPO_ROOT / document), (
            f"{document} no longer carries {phrase!r} in its list of absent checks"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 5. The four documentation layers agree with each other
# ─────────────────────────────────────────────────────────────────────────────

class TestTheFourLayersAgree:
    def test_the_docs_site_has_a_testing_section_with_one_sidebar_link(self):
        html = _read(DOCS_INDEX)
        assert '<h2 id="testing">' in html, "docs/index.html has no #testing section"
        nav = re.search(r"<nav>.*?</nav>", html, re.DOTALL)
        assert nav, "docs/index.html has no <nav>"
        assert nav.group(0).count('href="#testing"') == 1, (
            "the #testing section needs exactly one sidebar link"
        )

    @pytest.mark.parametrize("copy", ["llms.txt", "docs/llms.txt"])
    def test_llms_txt_links_the_testing_section(self, copy):
        assert "django-snapadmin/#testing" in _read(REPO_ROOT / copy), (
            f"{copy} does not link the #testing section, so an assistant answering "
            f'"is this tested, and how?" never reaches it'
        )

    @pytest.mark.parametrize("copy", ["llms.txt", "docs/llms.txt"])
    def test_llms_txt_states_both_gates(self, copy):
        """#QA1d — an assistant must be able to say *which* coverage is enforced:
        line and branch, both at 100%, and by which command."""
        text = _read(REPO_ROOT / copy)
        assert "100% line and 100% branch" in text, (
            f"{copy} must say both line and branch coverage are gated at 100%"
        )
        assert "--cov-branch --cov-fail-under=100" in text, (
            f"{copy} must name the command that enforces them"
        )
