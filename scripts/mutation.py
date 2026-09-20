#!/usr/bin/env python3
"""
scripts/mutation.py

Run mutation testing the two ways this project runs it (#QA1d):

* ``diff`` — **per push**: mutate only the functions the push actually changed.
  The changed line numbers come from ``git diff``; each one is mapped to the
  function (or method) that encloses it, and only those functions' mutants are
  run. A typical commit is a few dozen mutants, minutes of CI.
* ``modules`` — **weekly**: mutate the modules where a silent wrong answer costs
  most, whole (see :data:`DANGEROUS_MODULES`). This is what covers code nobody
  is touching.

Both are **advisory**: this script exits ``0`` whether or not mutants survived,
and prints a report. A surviving mutant is not automatically a bug — an
equivalent mutant (a log string, an unreachable guard) cannot be killed by any
test — so each one is either killed by a stronger test or accepted in writing.

Usage::

    python scripts/mutation.py diff --base origin/main
    python scripts/mutation.py modules
    python scripts/mutation.py modules snapadmin/masking.py

``--summary-file`` appends a Markdown report (for ``$GITHUB_STEP_SUMMARY``).
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Weekly targets: where a silent wrong answer means leaked data, a lost backup
#: or a row nobody can find again (the developer's list, #QA1a decision 3).
DANGEROUS_MODULES: tuple[str, ...] = (
    "snapadmin/encryption/cipher.py",
    "snapadmin/encryption/keys.py",
    "snapadmin/encryption/blind_index.py",
    "snapadmin/crypto.py",
    "snapadmin/sharding/router.py",
    "snapadmin/masking.py",
    "snapadmin/api/filters.py",
    "snapadmin/backup.py",
    "snapadmin/restore.py",
    # Retention has no module of its own: the purge lives on the model
    # (SnapModel.purge_expired) and in the export-job sweep.
    "snapadmin/tasks.py",
)

#: Upper bound on functions mutated in one diff-scoped run, so an unusually wide
#: commit cannot turn a per-push check into an hour of CI. The report says when
#: it truncates, and the weekly run covers the rest.
MAX_FUNCTIONS_PER_DIFF_RUN = 40

#: ``@@ -old,count +new,count @@`` — the only line of a unified diff this needs.
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

#: mutmut joins a class and its method with this character in a mutant's name.
_METHOD_SEPARATOR = "ǁ"


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    # noqa: S603 - every command here is an argv list this module builds itself
    # (git, mutmut) with no shell, and the only outside value is the --base ref.
    return subprocess.run(command, cwd=REPO_ROOT, text=True, **kwargs)  # noqa: S603


def _changed_lines(base: str) -> dict[Path, set[int]]:
    """``{path: {line numbers touched}}`` for the package's Python files."""
    # Against the working tree, not against HEAD: in CI ``base`` is the commit
    # the push started from, so this is exactly what the push changed — and
    # locally it also picks up what is not committed yet.
    diff = _run(["git", "diff", "--unified=0", base, "--", "snapadmin"], capture_output=True)
    if diff.returncode != 0:
        # A shallow clone, or a base this checkout does not have.
        diff = _run(
            ["git", "diff", "--unified=0", "HEAD~1", "--", "snapadmin"], capture_output=True
        )
        diff.check_returncode()

    changed: dict[Path, set[int]] = {}
    current: Path | None = None
    for line in diff.stdout.splitlines():
        if line.startswith("+++ b/"):
            path = Path(line[6:])
            current = path if path.suffix == ".py" else None
            continue
        if current is None:
            continue
        match = _HUNK.match(line)
        if match:
            start = int(match.group(1))
            count = int(match.group(2) or 1)
            if count:  # count == 0 is a pure deletion: no line to mutate
                changed.setdefault(current, set()).update(range(start, start + count))
    return changed


@dataclass(frozen=True)
class Target:
    """One function whose mutants a run will check."""

    module: str  # "snapadmin.masking"
    qualname: str  # "mask_value" or "APIToken.save"

    @property
    def glob(self) -> str:
        name = self.qualname.replace(".", _METHOD_SEPARATOR)
        prefix = f"x{_METHOD_SEPARATOR}" if _METHOD_SEPARATOR in name else "x_"
        return f"{self.module}.{prefix}{name}__mutmut_*"


def _module_name(path: Path) -> str:
    return str(path.with_suffix("")).replace("/", ".")


def _functions_covering(path: Path, lines: set[int]) -> list[Target]:
    """Every function in ``path`` whose body contains one of ``lines``.

    A change outside any function (an import, a constant, a class attribute)
    selects nothing: mutmut mutates function bodies, so there is nothing to run
    for it.
    """
    source = (REPO_ROOT / path).read_text(encoding="utf-8")
    module = _module_name(path)
    found: list[Target] = []

    def walk(node: ast.AST, scope: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, [*scope, child.name])
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start, end = child.lineno, child.end_lineno or child.lineno
                if any(start <= line <= end for line in lines):
                    found.append(Target(module, ".".join([*scope, child.name])))
                # A nested function is mutated as part of its parent.

    walk(ast.parse(source), [])
    return found


def _mutant_statuses(patterns: list[str]) -> dict[str, str]:
    """``{mutant name: status}`` for every mutant matching ``patterns``."""
    # `--all` or the listing shows *only* survivors, and a report that cannot
    # see the killed ones would say "202 of 202 survived" after a clean run.
    results = _run(["mutmut", "results", "--all", "true"], capture_output=True)
    results.check_returncode()
    statuses: dict[str, str] = {}
    for line in results.stdout.splitlines():
        name, separator, status = line.strip().partition(": ")
        if not separator or status == "not checked":
            continue
        if any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
            statuses[name] = status
    return statuses


class _StringBlanker(ast.NodeTransformer):
    """Replace every string constant with one placeholder, in place."""

    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        if isinstance(node.value, str):
            return ast.copy_location(ast.Constant(value=""), node)
        return node


def _normalised(function: ast.AST) -> str:
    """A function's shape with all its text blanked out."""
    copy = _StringBlanker().visit(ast.parse(ast.unparse(function)))
    for node in ast.walk(copy):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.name = "f"
    return ast.dump(copy)


def _text_only_survivors(names: Iterable[str]) -> set[str]:
    """Survivors whose only change is the wording of a string.

    Most mutants of a function that raises or logs are of this kind: mutmut
    rewrites the message, and a test that asserts the *shape* of an error (its
    type, its key, a phrase in it) legitimately does not notice. Killing them
    all would mean pinning prose word for word, which this project deliberately
    does not do — so they are separated out rather than mixed in with the
    survivors that changed behaviour.

    Decided by comparing the mutant's own generated source against the
    untouched copy mutmut keeps beside it, with every string literal blanked.
    """
    text_only: set[str] = set()
    cache: dict[str, dict[str, ast.AST]] = {}
    for name in names:
        module, _, mutant = name.rpartition(".")
        path = REPO_ROOT / "mutants" / (module.replace(".", "/") + ".py")
        if not path.is_file():
            continue
        if module not in cache:
            cache[module] = {
                node.name: node
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
        functions = cache[module]
        original = functions.get(mutant.rsplit("__mutmut_", 1)[0] + "__mutmut_orig")
        mutated = functions.get(mutant)
        if original is None or mutated is None:
            continue
        if _normalised(original) == _normalised(mutated):
            text_only.add(name)
    return text_only


def _report(patterns: list[str], note: str = "") -> str:
    statuses = _mutant_statuses(patterns)
    counts: dict[str, int] = {}
    for status in statuses.values():
        counts[status] = counts.get(status, 0) + 1
    survivors = sorted(name for name, status in statuses.items() if status == "survived")
    text_only = _text_only_survivors(survivors)
    survivors = [name for name in survivors if name not in text_only]

    lines = ["## Mutation testing (advisory)", ""]
    if note:
        lines += [note, ""]
    if not statuses:
        lines += ["No mutants were run — the change touched no function body.", ""]
        return "\n".join(lines)
    lines += [
        f"**{len(statuses)} mutants checked** — "
        + ", ".join(f"{count} {status}" for status, count in sorted(counts.items())),
        "",
    ]
    if text_only:
        lines += [
            f"{len(text_only)} survivor(s) changed only the wording of a message and are not "
            "listed: a test that asserts an error's type, key or a phrase in it legitimately "
            "does not notice a reworded sentence.",
            "",
        ]
    if survivors:
        lines += [
            f"### {len(survivors)} surviving mutant(s) that changed behaviour",
            "",
            "A survivor is a change to the code that no test noticed. Kill it with a stronger "
            "assertion, or record why the mutation is not worth catching (an equivalent mutant — "
            "an unreachable guard, a value nothing can observe — cannot be killed).",
            "",
            "Inspect one with `mutmut show <name>`:",
            "",
        ]
        lines += [f"- `{name}`" for name in survivors[:50]]
        if len(survivors) > 50:
            lines.append(f"- …and {len(survivors) - 50} more (`mutmut results`)")
    else:
        lines.append("Every mutant that changed behaviour was killed. 🎉")
    lines.append("")
    return "\n".join(lines)


def _mutmut_run(patterns: list[str]) -> None:
    print(f"mutmut run {' '.join(patterns)}", flush=True)
    # mutmut exits non-zero when mutants survive; this check reports, never blocks.
    _run(["mutmut", "run", *patterns])


def _emit(summary: str, summary_file: str | None) -> None:
    print(summary)
    target = summary_file or os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(summary + "\n")


def _diff_command(args: argparse.Namespace) -> int:
    changed = _changed_lines(args.base)
    targets: list[Target] = []
    for path, lines in sorted(changed.items()):
        if not (REPO_ROOT / path).exists():  # deleted in this push
            continue
        targets.extend(_functions_covering(path, lines))

    truncated = len(targets) > MAX_FUNCTIONS_PER_DIFF_RUN
    targets = targets[:MAX_FUNCTIONS_PER_DIFF_RUN]
    if not targets:
        _emit(
            "## Mutation testing (advisory)\n\nThis push changed no function body in "
            "`snapadmin/`, so there was nothing to mutate.\n",
            args.summary_file,
        )
        return 0

    patterns = [target.glob for target in targets]
    _mutmut_run(patterns)
    note = f"Scope: the {len(targets)} function(s) this push changed, against `{args.base}`."
    if truncated:
        note += (
            f" The diff touched more than {MAX_FUNCTIONS_PER_DIFF_RUN} functions, so the rest "
            "are left to the weekly run."
        )
    _emit(_report(patterns, note), args.summary_file)
    return 0


def _modules_command(args: argparse.Namespace) -> int:
    modules = args.modules or list(DANGEROUS_MODULES)
    patterns = [f"{_module_name(Path(module))}.*" for module in modules]
    _mutmut_run(patterns)
    note = "Scope: " + ", ".join(f"`{module}`" for module in modules) + "."
    _emit(_report(patterns, note), args.summary_file)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[2])
    parser.add_argument("--summary-file", help="append the Markdown report here")
    sub = parser.add_subparsers(dest="command", required=True)

    diff = sub.add_parser("diff", help="mutate only what this push changed")
    diff.add_argument("--base", default="origin/main", help="ref to diff against")
    diff.add_argument("--summary-file", help="append the Markdown report here")
    diff.set_defaults(func=_diff_command)

    modules = sub.add_parser("modules", help="mutate whole modules (weekly)")
    modules.add_argument("modules", nargs="*", help="paths; default: the dangerous set")
    modules.add_argument("--summary-file", help="append the Markdown report here")
    modules.set_defaults(func=_modules_command)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
