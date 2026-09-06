"""
tests/test_snapadmin_age_keygen_command.py — #BKP2

``manage.py snapadmin_age_keygen`` generates an age keypair into a
``.age/`` directory at the project root, with a ``.gitignore`` safety net
that recognises more than a literal ``.age``/``.age/`` line (including the
user's specific ask — a bare ``.*`` blanket-dotfile rule) — and never
prints or logs the private key.
"""

from io import StringIO

import pytest
from django.core.management import call_command
from django.test import override_settings

from snapadmin import crypto
from snapadmin.management.commands import snapadmin_age_keygen as keygen_module
from snapadmin.management.commands.snapadmin_age_keygen import (
    AGE_DIR_NAME,
    _pattern_covers_age,
    _unique_keypair_paths,
    ensure_gitignore_excludes_age,
    gitignore_covers_age,
)


# ── _pattern_covers_age — the bounded gitignore pattern matcher ─────────────

class TestPatternCoversAge:
    @pytest.mark.parametrize("pattern", [
        ".age",
        ".age/",
        "/.age",
        "/.age/",
        "**/.age",
        "**/.age/**",
        ".*",           # the user's specific ask: a blanket dotfile rule
        ".a??",
        ".age*",
    ])
    def test_covering_patterns(self, pattern):
        assert _pattern_covers_age(pattern) is True

    @pytest.mark.parametrize("pattern", [
        "",
        "# .age is a comment, not a rule",
        "!.age/",       # negation never counts as coverage
        "node_modules/",
        "*.log",
        ".env",
        "/",
    ])
    def test_non_covering_patterns(self, pattern):
        assert _pattern_covers_age(pattern) is False


class TestGitignoreCoversAge:
    def test_missing_file_is_not_covered(self, tmp_path):
        assert gitignore_covers_age(tmp_path / ".gitignore") is False

    def test_unrelated_rules_are_not_covered(self, tmp_path):
        path = tmp_path / ".gitignore"
        path.write_text("*.pyc\nnode_modules/\n")
        assert gitignore_covers_age(path) is False

    def test_literal_rule_is_covered(self, tmp_path):
        path = tmp_path / ".gitignore"
        path.write_text("*.pyc\n.age/\n")
        assert gitignore_covers_age(path) is True

    def test_blanket_dotfile_rule_is_covered(self, tmp_path):
        path = tmp_path / ".gitignore"
        path.write_text(".*\n")
        assert gitignore_covers_age(path) is True


# ── ensure_gitignore_excludes_age ───────────────────────────────────────────

class TestEnsureGitignoreExcludesAge:
    def test_creates_gitignore_when_missing(self, tmp_path):
        added = ensure_gitignore_excludes_age(tmp_path)
        assert added is True
        content = (tmp_path / ".gitignore").read_text()
        assert f"{AGE_DIR_NAME}/" in content

    def test_appends_to_an_existing_nonempty_gitignore(self, tmp_path):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("*.pyc\n")
        added = ensure_gitignore_excludes_age(tmp_path)
        assert added is True
        content = gitignore.read_text()
        assert "*.pyc" in content
        assert f"{AGE_DIR_NAME}/" in content

    def test_does_not_duplicate_when_already_covered(self, tmp_path):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(".age/\n")
        added = ensure_gitignore_excludes_age(tmp_path)
        assert added is False
        assert gitignore.read_text().count(".age/") == 1

    def test_a_blanket_dotfile_rule_is_left_untouched(self, tmp_path):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(".*\n")
        added = ensure_gitignore_excludes_age(tmp_path)
        assert added is False
        assert gitignore.read_text() == ".*\n"


# ── _unique_keypair_paths — same-second collision disambiguation ───────────

class TestUniqueKeypairPaths:
    def test_first_call_uses_the_bare_timestamp(self, tmp_path):
        import re

        identity_path, recipient_path = _unique_keypair_paths(tmp_path)
        assert re.fullmatch(r"age-identity-\d{8}-\d{6}\.txt", identity_path.name)
        assert recipient_path.name == identity_path.name + ".pub"

    def test_a_colliding_stamp_gets_disambiguated(self, tmp_path, monkeypatch):
        fixed_stamp = "20260905-120000"
        monkeypatch.setattr(
            keygen_module.timezone, "now",
            lambda: __import__("datetime").datetime(2026, 9, 5, 12, 0, 0),
        )
        first_identity, _ = _unique_keypair_paths(tmp_path)
        first_identity.write_text("occupied")

        second_identity, second_recipient = _unique_keypair_paths(tmp_path)
        assert second_identity != first_identity
        assert second_identity.name == f"age-identity-{fixed_stamp}-2.txt"
        assert second_recipient.name == f"age-identity-{fixed_stamp}-2.txt.pub"


# ── the command itself ──────────────────────────────────────────────────────

class TestCommand:
    def test_generates_a_keypair_and_writes_files(self, tmp_path):
        with override_settings(BASE_DIR=tmp_path):
            out = StringIO()
            call_command("snapadmin_age_keygen", stdout=out)

        age_dir = tmp_path / AGE_DIR_NAME
        identity_files = list(age_dir.glob("age-identity-*.txt"))
        pub_files = list(age_dir.glob("age-identity-*.txt.pub"))
        assert len(identity_files) == 1
        assert len(pub_files) == 1

        identity_text = identity_files[0].read_text()
        assert identity_text.strip().startswith(crypto._AGE_IDENTITY_PREFIX)
        recipient_text = pub_files[0].read_text().strip()
        assert crypto.looks_like_recipient(recipient_text)

    def test_private_key_is_never_printed(self, tmp_path):
        with override_settings(BASE_DIR=tmp_path):
            out = StringIO()
            call_command("snapadmin_age_keygen", stdout=out)

        age_dir = tmp_path / AGE_DIR_NAME
        identity_text = next(age_dir.glob("age-identity-*.txt")).read_text().strip()
        assert identity_text not in out.getvalue()

    def test_recipient_is_printed_for_pasting_into_settings(self, tmp_path):
        with override_settings(BASE_DIR=tmp_path):
            out = StringIO()
            call_command("snapadmin_age_keygen", stdout=out)

        age_dir = tmp_path / AGE_DIR_NAME
        recipient_text = next(age_dir.glob("*.pub")).read_text().strip()
        assert recipient_text in out.getvalue()

    def test_missing_gitignore_rule_is_added_and_reported(self, tmp_path):
        with override_settings(BASE_DIR=tmp_path):
            out = StringIO()
            call_command("snapadmin_age_keygen", stdout=out)

        assert "added one" in out.getvalue()
        assert f"{AGE_DIR_NAME}/" in (tmp_path / ".gitignore").read_text()

    def test_already_covered_gitignore_is_reported_without_duplicating(self, tmp_path):
        (tmp_path / ".gitignore").write_text(".age/\n")
        with override_settings(BASE_DIR=tmp_path):
            out = StringIO()
            call_command("snapadmin_age_keygen", stdout=out)

        assert "already covered" in out.getvalue()
        assert (tmp_path / ".gitignore").read_text().count(".age/") == 1

    def test_closing_reminder_mentions_deleting_the_directory(self, tmp_path):
        with override_settings(BASE_DIR=tmp_path):
            out = StringIO()
            call_command("snapadmin_age_keygen", stdout=out)

        assert "delete" in out.getvalue().lower()
        assert "secure" in out.getvalue().lower()

    def test_running_twice_does_not_overwrite_the_first_keypair(self, tmp_path):
        with override_settings(BASE_DIR=tmp_path):
            call_command("snapadmin_age_keygen", stdout=StringIO())
            call_command("snapadmin_age_keygen", stdout=StringIO())

        age_dir = tmp_path / AGE_DIR_NAME
        assert len(list(age_dir.glob("age-identity-*.txt"))) == 2

    def test_falls_back_to_cwd_without_base_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delattr(keygen_module.settings, "BASE_DIR", raising=False)
        out = StringIO()
        call_command("snapadmin_age_keygen", stdout=out)
        assert (tmp_path / AGE_DIR_NAME).is_dir()

    def test_chmod_failure_is_non_fatal(self, tmp_path, monkeypatch):
        """Not every filesystem/platform honours POSIX permission bits — the
        keypair must still be written even if chmod() itself fails."""

        def failing_chmod(self, mode):
            raise OSError("chmod not supported (simulated)")

        monkeypatch.setattr(keygen_module.Path, "chmod", failing_chmod)
        with override_settings(BASE_DIR=tmp_path):
            call_command("snapadmin_age_keygen", stdout=StringIO())

        assert list((tmp_path / AGE_DIR_NAME).glob("age-identity-*.txt"))

    def test_backend_option_is_forwarded(self, tmp_path, monkeypatch):
        seen = {}
        original_generate = crypto.generate_keypair

        def fake_generate(*, backend):
            seen["backend"] = backend
            return original_generate(backend=backend)

        monkeypatch.setattr(keygen_module.crypto, "generate_keypair", fake_generate)
        with override_settings(BASE_DIR=tmp_path):
            call_command("snapadmin_age_keygen", "--backend", "pyrage", stdout=StringIO())
        assert seen["backend"] == "pyrage"
