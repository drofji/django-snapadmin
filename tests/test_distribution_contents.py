"""What the sdist and the wheel carry (#EXT1g / #RM1e).

The test suite ships in the **sdist only**: a downstream packager or auditor can
unpack it and run ``python -m pytest`` there (verified by hand on 2026-09-18 —
the full suite passes from an unpacked sdist), while the wheel a project installs
stays lean. The suite needs the demo project (its settings module), the docs it
checks for truth and the CI/contributor files it pins, so those go in with it.

Read from ``pyproject.toml`` rather than by building a distribution, which would
make every test run pay for a build.

**Nothing git ignores may ship (#PKG1).** poetry-core lets an explicit ``include``
override ``.gitignore`` *and* ``exclude`` — so the old ``demo/**/*`` pattern packed a
developer's local ``demo/.env`` (``SECRET_KEY``, database and dashboard passwords)
into any sdist built from a working tree. Releases build in CI from a clean
checkout, where no ignored file exists, so CI could never see it. The include
patterns now name what is tracked, and two guards hold the line in both
directions: no git-ignored file is matched, and no tracked file the sdist suite
needs is left out.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Paths the suite needs next to it, sdist-only. Named by what is tracked, never
#: ``<dir>/**/*``: a blanket pattern also packs whatever git ignores in that
#: directory (see the module docstring).
SDIST_ONLY = {
    "tests/**/*.py",
    "conftest.py",
    "pytest.ini",
    "demo/**/*.py",
    "demo/**/*.html",
    "demo/**/*.po",
    "demo/**/*.mo",
    "demo/*.yml",
    "demo/Dockerfile",
    "demo/README.md",
    "demo/dist.env",
    "demo/requirements.txt",
    "demo/scripts/*.sh",
    "docs/index.html",
    "docs/llms.txt",
    "docs/upstream/*.txt",
    "CONTRIBUTING.md",
    ".github/**/*.yml",
}

#: The tracked trees the sdist carries only so the suite can run from it; every
#: tracked file under them must be matched by an include pattern.
SDIST_ONLY_ROOTS = ("demo/", "docs/", ".github/")


@pytest.fixture(scope="module")
def includes() -> dict[str, list[str] | None]:
    tomllib = pytest.importorskip("tomllib")
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    found: dict[str, list[str] | None] = {}
    for entry in pyproject["tool"]["poetry"]["include"]:
        if isinstance(entry, str):
            found[entry] = None  # poetry default: both formats
        else:
            found[entry["path"]] = list(entry.get("format", ["sdist", "wheel"]))
    return found


def test_the_suite_and_what_it_needs_travel_with_the_sdist(includes):
    missing = sorted(path for path in SDIST_ONLY if path not in includes)
    assert not missing, f"not included in the sdist: {missing}"


def test_none_of_it_reaches_the_wheel(includes):
    leaking = sorted(path for path in SDIST_ONLY if includes.get(path) != ["sdist"])
    assert not leaking, f"these must be sdist-only, never in the wheel: {leaking}"


def test_every_included_pattern_matches_a_tracked_file(includes):
    """A renamed directory must not leave a pattern that silently ships nothing."""
    dead = sorted(p for p in SDIST_ONLY if not any(REPO_ROOT.glob(p)))
    assert not dead, f"include patterns that match nothing: {dead}"


def _include_patterns() -> list[tuple[str, list[str]]]:
    tomllib = pytest.importorskip("tomllib")
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = []
    for entry in pyproject["tool"]["poetry"]["include"]:
        if isinstance(entry, str):
            patterns.append((entry, ["sdist", "wheel"]))
        else:
            patterns.append((entry["path"], list(entry.get("format", ["sdist", "wheel"]))))
    return patterns


def files_matched_by(root: Path, patterns: list[str]) -> set[str]:
    """What poetry-core packs for these explicit include patterns: each match, or
    every file under a matched directory — ``__pycache__`` and ``.pyc`` aside,
    which it drops on its own. Explicit includes ignore ``.gitignore``."""
    matched: set[str] = set()
    for pattern in patterns:
        for hit in root.glob(pattern):
            for path in ([hit] if hit.is_file() else [p for p in hit.glob("**/*") if p.is_file()]):
                relative = path.relative_to(root)
                if "__pycache__" not in relative.parts and relative.suffix != ".pyc":
                    matched.add(relative.as_posix())
    return matched


def git_ignored_files(root: Path) -> set[str]:
    out = subprocess.run(  # noqa: S603, S607 - fixed argv, no shell
        ["git", "ls-files", "--others", "--ignored", "--exclude-standard"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout
    return {line for line in out.splitlines() if "__pycache__" not in line and not line.endswith(".pyc")}


def git_tracked_files(root: Path) -> set[str]:
    out = subprocess.run(  # noqa: S603, S607 - fixed argv, no shell
        ["git", "ls-files"], cwd=root, check=True, capture_output=True, text=True
    ).stdout
    return set(out.splitlines())


needs_git_checkout = pytest.mark.skipif(
    not (REPO_ROOT / ".git").exists() or shutil.which("git") is None,
    reason="compares the include patterns with a git working tree (not an unpacked sdist)",
)


class TestNothingGitIgnoresShips:
    @needs_git_checkout
    def test_no_include_pattern_matches_a_git_ignored_file_in_this_tree(self):
        patterns = [pattern for pattern, _formats in _include_patterns()]

        leaked = sorted(files_matched_by(REPO_ROOT, patterns) & git_ignored_files(REPO_ROOT))

        assert not leaked, (
            f"review: a build from this working tree would ship git-ignored file(s) {leaked} — "
            "poetry's explicit include overrides .gitignore and exclude alike; narrow the "
            "pattern in pyproject.toml so it names tracked files only"
        )

    @needs_git_checkout
    def test_every_tracked_file_the_sdist_suite_needs_is_matched(self):
        sdist_patterns = [p for p, formats in _include_patterns() if "sdist" in formats]
        matched = files_matched_by(REPO_ROOT, sdist_patterns)

        left_out = sorted(
            path
            for path in git_tracked_files(REPO_ROOT)
            if path.startswith(SDIST_ONLY_ROOTS) and path not in matched
        )

        assert not left_out, (
            f"review: tracked file(s) {left_out} would not ship in the sdist, but the suite "
            "run from an unpacked sdist may read them — add a pattern naming them to "
            "pyproject.toml's include (sdist format)"
        )

    @pytest.mark.skipif(shutil.which("git") is None, reason="needs the git binary")
    def test_the_guard_catches_the_blanket_pattern_that_shipped_a_local_env_file(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # noqa: S603, S607
        (tmp_path / ".gitignore").write_text(".*\n!.gitignore\n", encoding="utf-8")
        (tmp_path / "demo").mkdir()
        (tmp_path / "demo" / "settings.py").write_text("DEBUG = False\n", encoding="utf-8")
        (tmp_path / "demo" / ".env").write_text("SECRET_KEY=local-only\n", encoding="utf-8")

        blanket = files_matched_by(tmp_path, ["demo/**/*"]) & git_ignored_files(tmp_path)
        named = files_matched_by(tmp_path, ["demo/**/*.py"]) & git_ignored_files(tmp_path)

        assert blanket == {"demo/.env"}
        assert named == set()


class TestPublicFilesNamePrivateToolingNowhere:
    """Tracked files ship to GitHub and in the sdist; the maintainers' local tooling
    directory is git-ignored and must stay invisible there (#PKG2). Five test
    docstrings pointed readers at files they could never open."""

    #: SHA-256 of the private names (the tooling directory and its standards file) —
    #: digests, so this public file does not name them either.
    PRIVATE_NAME_DIGESTS = frozenset(
        {
            "e026f56c08dcdea441a2aa620ab410ce91162062609b2430f5a13bc9b7cf93f1",
            "ae669539d19742429b26622a8ae7210cbb7917ecbf5eb3da4425d255c3cc6de7",
        }
    )
    #: Candidate tokens: a dot-directory name, or a Markdown file name.
    CANDIDATE_RE = re.compile(r"(?<![\w])\.[A-Za-z][\w-]*|[\w-]+\.md")

    @needs_git_checkout
    def test_no_tracked_file_references_the_private_tooling_directory(self):
        import hashlib

        offenders = []
        for relative in sorted(git_tracked_files(REPO_ROOT)):
            path = REPO_ROOT / relative
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
                continue
            tokens = set(self.CANDIDATE_RE.findall(text))
            offenders += [
                f"{relative}: {token}"
                for token in sorted(tokens)
                if hashlib.sha256(token.encode()).hexdigest() in self.PRIVATE_NAME_DIGESTS
            ]

        assert not offenders, (
            f"review: public file(s) reference private tooling {offenders} — point at the public "
            "equivalent (CONTRIBUTING.md, SECURITY.md) or state the rule inline"
        )


#: The documents a reader asks "does the sdist carry the tests?" (#DOC13).
DOCS_THAT_STATE_WHERE_THE_SUITE_SHIPS = ("README.md", "SECURITY.md", "CONTRIBUTING.md", "llms.txt")


def test_no_document_says_the_suite_is_left_out_of_the_sdist(includes):
    """``SECURITY.md`` said the suite shipped in neither the wheel nor the sdist a
    full release after the sdist started carrying it — nothing compared the
    sentence with ``pyproject.toml``. Now the docs must agree with it."""
    assert includes.get("tests/**/*.py") == ["sdist"], "the premise changed — update this guard"
    contradicting = [
        name
        for name in DOCS_THAT_STATE_WHERE_THE_SUITE_SHIPS
        if "not shipped in the wheel or sdist" in (REPO_ROOT / name).read_text(encoding="utf-8")
        or "not shipped in the sdist" in (REPO_ROOT / name).read_text(encoding="utf-8")
    ]
    stating_it = [
        name
        for name in DOCS_THAT_STATE_WHERE_THE_SUITE_SHIPS
        if "sdist" in (REPO_ROOT / name).read_text(encoding="utf-8")
    ]

    assert not contradicting, (
        f"review: {contradicting} say the test suite is not in the sdist, but pyproject.toml "
        "ships tests/**/*.py in it"
    )
    assert set(stating_it) == set(DOCS_THAT_STATE_WHERE_THE_SUITE_SHIPS), (
        f"review: only {sorted(stating_it)} say the sdist carries the suite"
    )
