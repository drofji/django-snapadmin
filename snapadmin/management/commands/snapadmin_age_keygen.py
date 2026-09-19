"""
snapadmin/management/commands/snapadmin_age_keygen.py

Generate an age keypair for backup encryption
(``SNAPADMIN_BACKUP_AGE_RECIPIENTS``) without leaving the project, with a
built-in git-safety net so the private key can never accidentally end up
committed.

    python manage.py snapadmin_age_keygen
    python manage.py snapadmin_age_keygen --backend pyrage

Writes both halves of the keypair into a new ``.age/`` directory at the
project root (``settings.BASE_DIR``, falling back to the current working
directory): ``age-identity-<stamp>.txt`` (the private key — never printed or
logged) and ``age-identity-<stamp>.txt.pub`` (the public key/recipient —
printed to stdout, safe to paste straight into
``SNAPADMIN_BACKUP_AGE_RECIPIENTS``). Timestamped, not a fixed name, so
running the command again — a second recipient, a rotation — never silently
overwrites an earlier keypair.

Before writing anything, checks the project's ``.gitignore`` for a rule that
would already exclude ``.age/`` — recognising not just a literal
``.age``/``.age/`` line but broader patterns that also cover it (a
leading-slash anchor, a ``**/`` prefix or ``/**`` suffix, and a blanket
dotfile pattern like ``.*``). If none is found, one is appended and the
command says so out loud; ``.gitignore`` is created if it does not exist at
all. Full ``.gitignore`` glob semantics (mid-pattern ``**``, negation
ordering) are not implemented — a ``!``-negation line is always treated as
*not* covering ``.age/`` (never subtracts coverage either), which is the
safe direction for a check whose job is "warn when in doubt", not a
byte-perfect gitignore engine.

Always closes with a loud (but deliberately non-blocking, #BKP2a — a hard
block on a prompt here would be worse than the risk it guards against)
reminder: move the private key to a secure location and delete the local
``.age/`` directory — it is a convenience for generation, never a place to
keep a private key long-term.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from snapadmin import crypto
from snapadmin.logging_config import get_logger

logger = get_logger(__name__)

#: The directory a keypair is written into, relative to the project root.
AGE_DIR_NAME = ".age"


def project_root() -> Path:
    """``settings.BASE_DIR`` if the project defines one, else the CWD."""
    base_dir = getattr(settings, "BASE_DIR", None)
    return Path(base_dir) if base_dir else Path.cwd()


def _pattern_covers_age(pattern: str) -> bool:
    """Whether one ``.gitignore`` line, read as a pattern, would exclude ``.age``.

    Not a full gitignore engine (see the module docstring) — a bounded
    ``fnmatch`` check against a handful of the shapes people actually write:
    a bare name, a leading-slash anchor, a ``**/`` prefix, a trailing ``/``
    or ``/**``, and any ``fnmatch`` glob (which is what makes a blanket
    ``.*`` line recognised — ``fnmatch``, unlike a shell glob, has no
    "``*`` skips dotfiles" exception).
    """
    candidate = pattern.strip()
    if not candidate or candidate.startswith("#") or candidate.startswith("!"):
        return False
    candidate = candidate.rstrip("/")
    if candidate.startswith("**/"):
        candidate = candidate[3:]
    if candidate.endswith("/**"):
        candidate = candidate[:-3]
    candidate = candidate.removeprefix("/")
    if not candidate:
        return False
    return fnmatch.fnmatch(AGE_DIR_NAME, candidate)


def gitignore_covers_age(gitignore_path: Path) -> bool:
    """Whether ``gitignore_path`` (if it exists) already excludes ``.age/``."""
    if not gitignore_path.is_file():
        return False
    lines = gitignore_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return any(_pattern_covers_age(line) for line in lines)


def ensure_gitignore_excludes_age(root: Path) -> bool:
    """Append a ``.age/`` rule to ``root/.gitignore`` if nothing already covers it.

    Creates ``.gitignore`` if it does not exist. Returns ``True`` when a rule
    was actually added, ``False`` when the directory was already covered.
    """
    gitignore_path = root / ".gitignore"
    if gitignore_covers_age(gitignore_path):
        return False

    had_content = gitignore_path.is_file() and gitignore_path.stat().st_size > 0
    with open(gitignore_path, "a", encoding="utf-8") as fh:
        if had_content:
            fh.write("\n")
        fh.write("# Added by snapadmin_age_keygen — keep private key material out of git.\n")
        fh.write(f"{AGE_DIR_NAME}/\n")
    return True


def _unique_keypair_paths(age_dir: Path) -> tuple[Path, Path]:
    """A ``(identity_path, recipient_path)`` pair that does not already exist.

    Timestamped to the second, which two runs inside the same second would
    collide on — disambiguated with a ``-2``/``-3``/… suffix rather than
    silently overwriting an earlier keypair (and, with it, access to
    whatever it was used to encrypt).
    """
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    stem = f"age-identity-{stamp}"
    suffix = 1
    while (age_dir / f"{stem}.txt").exists():
        suffix += 1
        stem = f"age-identity-{stamp}-{suffix}"
    return age_dir / f"{stem}.txt", age_dir / f"{stem}.txt.pub"


class Command(BaseCommand):
    help = "Generate an age keypair for backup encryption, with a .gitignore safety net."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--backend",
            choices=crypto.BACKENDS,
            default="auto",
            help="Which age implementation to generate with (default: auto).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        root = project_root()

        if ensure_gitignore_excludes_age(root):
            self.stdout.write(
                self.style.WARNING(
                    f"No .gitignore rule covered {AGE_DIR_NAME}/ — added one at "
                    f"{root / '.gitignore'}."
                )
            )
        else:
            self.stdout.write(f"{AGE_DIR_NAME}/ is already covered by .gitignore.")

        identity, recipient = crypto.generate_keypair(backend=options["backend"])

        age_dir = root / AGE_DIR_NAME
        age_dir.mkdir(exist_ok=True)
        identity_path, recipient_path = _unique_keypair_paths(age_dir)

        identity_path.write_text(identity + "\n", encoding="utf-8")
        try:
            identity_path.chmod(0o600)
        except OSError:
            pass  # best-effort — not every filesystem/platform honours POSIX bits
        recipient_path.write_text(recipient + "\n", encoding="utf-8")

        logger.info("age_keypair_generated", identity_file=str(identity_path))

        self.stdout.write(self.style.SUCCESS(f"Generated a new age keypair in {age_dir}/"))
        self.stdout.write(f"  private key: {identity_path.name}  (never logged, never printed)")
        self.stdout.write(f"  public key : {recipient_path.name}")
        self.stdout.write("")
        self.stdout.write("Add the line below to SNAPADMIN_BACKUP_AGE_RECIPIENTS:")
        self.stdout.write(f"  {recipient}")
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "IMPORTANT: move the private key file to a secure location (a password "
                f"manager, a secrets vault, an encrypted volume) and then delete {age_dir}/ "
                "— it is a convenience for generation, never a place to keep a private key."
            )
        )
