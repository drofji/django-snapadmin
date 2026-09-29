"""
tests/test_security_regressions.py

Keeps the security regression registry (``tests/security_regressions.py``) true.

Three ways it could stop being true, one guard each:

* **A security fix ships with no named test.** Every entry of every ``Security``
  section in ``docs/releases/*.txt`` must map to exactly one registry entry. The
  release notes are the generated half: nobody has to remember to update a list
  for this check to see a new entry.
* **A named test disappears or stops running.** Every test the registry names is
  found in the source with ``ast``, and none carries a ``skip``/``skipif``/
  ``xfail`` mark — on itself, on its class or on its module.
* **The registry shrinks.** Its size never drops below ``REGISTRY_FLOOR``.
"""

from __future__ import annotations

import ast
import re
from functools import cache
from pathlib import Path

import pytest

from tests.security_regressions import (
    REGISTRY,
    REGISTRY_FLOOR,
    is_security_regression,
    node_ids,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASES = REPO_ROOT / "docs" / "releases"
_UNDERLINE = re.compile(r"[=\-~]{3,}")
_SKIPPING_MARKS = {"skip", "skipif", "xfail"}


@cache
def _security_note_titles() -> tuple[tuple[str, str], ...]:
    """``(release file, first line)`` of every entry in a ``Security`` section.

    Three layouts have been used over the years, all read here: an unindented
    title line with an indented body (0.1.0b1, 0.1.0b3), a ``- `` bullet
    (0.1.0b2) and a ``* **bold**`` bullet (0.1.0b7 on). An unindented line ending
    in ``:`` introduces a group of bullets and is not an entry itself.
    """
    titles = []
    for path in sorted(RELEASES.glob("*.txt")):
        lines = path.read_text(encoding="utf-8").splitlines()
        inside = False
        for index, line in enumerate(lines):
            following = lines[index + 1] if index + 1 < len(lines) else ""
            if line.strip() and _UNDERLINE.fullmatch(following):
                inside = line.strip() == "Security"
                continue
            if not inside or not line or line[0].isspace() or _UNDERLINE.fullmatch(line):
                continue
            is_bullet = line.startswith(("* ", "- "))
            if line.endswith(":") and not is_bullet:
                continue
            titles.append((path.name, re.sub(r"^(\* \*\*|- )", "", line)))
    return tuple(titles)


def _entries_for(title: str) -> list[str]:
    return [fix.title for fix in REGISTRY if fix.in_release_notes and title.startswith(fix.title)]


# ─────────────────────────────────────────────────────────────────────────────
# The release notes and the registry agree
# ─────────────────────────────────────────────────────────────────────────────

def test_the_notes_parser_finds_every_layout():
    """The parser must see all three layouts, or the backstop below is blind."""
    titles = _security_note_titles()
    releases = {release for release, _ in titles}

    assert {"0.1.0b1.txt", "0.1.0b2.txt", "0.1.0b3.txt", "0.1.0b7.txt"} <= releases
    assert len(titles) >= 26


@pytest.mark.parametrize(
    "release, title", _security_note_titles(), ids=lambda value: str(value)[:60]
)
def test_every_published_security_fix_has_a_registry_entry(release, title):
    entries = _entries_for(title)

    assert len(entries) == 1, (
        f"{release}: the Security entry {title!r} matches {len(entries)} registry entries. "
        f"Add exactly one SecurityFix to tests/security_regressions.py naming the tests "
        f"that fail if this fix regresses."
    )


@pytest.mark.parametrize("fix", [fix for fix in REGISTRY if fix.in_release_notes],
                         ids=lambda fix: fix.title[:60])
def test_every_registry_entry_is_a_published_security_fix(fix):
    matches = [
        release for release, title in _security_note_titles() if title.startswith(fix.title)
    ]

    assert len(matches) == 1, (
        f"{fix.title!r} matches {len(matches)} Security entries in docs/releases/ — a title "
        f"was reworded, or the entry belongs under in_release_notes=False with an origin."
    )


@pytest.mark.parametrize("fix", [fix for fix in REGISTRY if not fix.in_release_notes],
                         ids=lambda fix: fix.title[:60])
def test_a_fix_outside_the_notes_says_where_it_came_from(fix):
    assert fix.origin.strip()


def test_every_entry_names_at_least_one_test():
    assert [fix.title for fix in REGISTRY if not fix.tests] == []


def test_the_registry_never_shrinks():
    assert len(REGISTRY) >= REGISTRY_FLOOR
    assert len({fix.title for fix in REGISTRY}) == len(REGISTRY)


# ─────────────────────────────────────────────────────────────────────────────
# Every named test exists and runs
# ─────────────────────────────────────────────────────────────────────────────

@cache
def _module(path: str) -> ast.Module:
    return ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))


def _mark_names(node: ast.AST) -> set[str]:
    """The ``pytest.mark.<name>`` names on a decorated def/class or in ``pytestmark``."""
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Attribute):
            if sub.value.attr == "mark":
                names.add(sub.attr)
    return names


def _resolve(node_id: str) -> list[ast.AST]:
    """The chain of AST nodes a node id names: module, then class(es), then function."""
    path, *parts = node_id.split("::")
    module = _module(path)
    chain: list[ast.AST] = [module]
    scope: list[ast.stmt] = module.body
    for name in parts:
        found = next(
            (
                node
                for node in scope
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ),
            None,
        )
        assert found is not None, f"{node_id}: {name!r} is not defined where the id says"
        chain.append(found)
        scope = found.body if isinstance(found, ast.ClassDef) else []
    return chain


def _skipping_aliases(module: ast.Module) -> set[str]:
    """Module-level names bound to a skipping mark: ``needs_x = pytest.mark.skipif(…)``."""
    aliases = set()
    for statement in module.body:
        if isinstance(statement, ast.Assign) and _mark_names(statement.value) & _SKIPPING_MARKS:
            aliases |= {target.id for target in statement.targets if isinstance(target, ast.Name)}
    return aliases


def _skips(node: ast.AST, aliases: set[str]) -> list[str]:
    """Every way ``node`` — a module, class or test, and everything inside it —
    can end up not running: a skipping mark (direct, via ``pytestmark`` or via an
    alias) or a runtime ``pytest.skip``/``xfail``/``importorskip`` call."""
    found = []
    for sub in ast.walk(node):
        decorators = getattr(sub, "decorator_list", [])
        for decorator in decorators:
            if _mark_names(decorator) & _SKIPPING_MARKS:
                found.append(f"line {decorator.lineno}: a skipping mark")
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(target, ast.Name) and target.id in aliases:
                found.append(f"line {decorator.lineno}: @{target.id}")
        if isinstance(sub, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in sub.targets
        ) and _mark_names(sub.value) & _SKIPPING_MARKS:
            found.append(f"line {sub.lineno}: pytestmark")
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr in {"skip", "xfail", "importorskip"}
            and isinstance(sub.func.value, ast.Name)
            and sub.func.value.id == "pytest"
        ):
            found.append(f"line {sub.lineno}: pytest.{sub.func.attr}()")
    return found


@pytest.mark.parametrize("node_id", sorted(node_ids()))
def test_every_named_test_exists_and_is_not_skipped(node_id):
    path = node_id.split("::")[0]
    assert (REPO_ROOT / path).is_file(), f"{node_id}: no such test module"

    chain = _resolve(node_id)

    module, *defined = chain
    aliases = _skipping_aliases(module)
    if not defined:  # a whole module: everything in it
        found = _skips(module, aliases)
    else:
        found = [
            problem
            for statement in module.body
            if isinstance(statement, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in statement.targets)
            for problem in _skips(statement, aliases)
        ]
        for enclosing in defined[:-1]:  # the class a test sits in, not its siblings
            found += _skips(ast.ClassDef(**{**enclosing.__dict__, "body": []}), aliases)
        found += _skips(defined[-1], aliases)

    assert found == [], f"{node_id} can end up not running — {found}"


# ─────────────────────────────────────────────────────────────────────────────
# The marker selects exactly what the registry names
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "nodeid, expected",
    [
        ("tests/a.py::TestX", True),
        ("tests/a.py::TestX::test_one", True),
        ("tests/a.py::TestX::test_one[param-1]", True),
        ("tests/a.py::TestXOther::test_one", False),
        ("tests/b.py::test_solo[2]", True),
        ("tests/b.py::test_solo_other", False),
        ("tests/c.py::TestAnything::test_any", True),
        ("tests/cc.py::test_any", False),
    ],
)
def test_the_marker_covers_a_registered_id_and_only_what_is_inside_it(nodeid, expected):
    registered = frozenset({"tests/a.py::TestX", "tests/b.py::test_solo", "tests/c.py"})

    assert is_security_regression(nodeid, registered) is expected


def test_this_run_marked_every_collected_registered_test(request):
    """In this very session, every collected test the registry names carries the
    marker, and no other test does — so ``-m security_regression`` is the suite."""
    registered = node_ids()
    for item in request.session.items:
        named = is_security_regression(item.nodeid, registered)
        marked = item.get_closest_marker("security_regression") is not None
        assert named is marked, item.nodeid
