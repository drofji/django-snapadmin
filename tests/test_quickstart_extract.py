"""Tests for :mod:`snapadmin.quickstart.extract` (#CLI3c)."""

from __future__ import annotations

import io
import tarfile

import pytest

from snapadmin.quickstart import QuickstartError, extract

TOP = "django-snapadmin-1.0"


def _make_tarball(path, files=None, dirs=(), symlinks=()):
    """Build a GitHub-style source tarball under ``TOP/``."""
    with tarfile.open(path, "w:gz") as tar:
        for rel in dirs:
            info = tarfile.TarInfo(f"{TOP}/{rel}")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
        for rel, content in (files or {}).items():
            data = content.encode()
            info = tarfile.TarInfo(f"{TOP}/{rel}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for rel, target in symlinks:
            info = tarfile.TarInfo(f"{TOP}/{rel}")
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)
    return path


class TestExtractDemo:
    def test_extracts_only_demo(self, tmp_path):
        archive = _make_tarball(
            tmp_path / "src.tar.gz",
            files={"demo/manage.py": "print('hi')", "README.md": "ignore me"},
        )
        dest = tmp_path / "out"
        result = extract.extract_demo(archive, dest, assume_yes=True)
        assert result == (dest.resolve() / "demo")
        assert (dest / "demo" / "manage.py").read_text() == "print('hi')"
        assert not (dest / "README.md").exists()

    def test_no_demo_dir(self, tmp_path):
        archive = _make_tarball(tmp_path / "src.tar.gz", files={"README.md": "x"})
        with pytest.raises(QuickstartError, match="no demo/"):
            extract.extract_demo(archive, tmp_path / "out", assume_yes=True)

    def test_creates_nested_dirs(self, tmp_path):
        archive = _make_tarball(
            tmp_path / "src.tar.gz",
            files={"demo/core/settings.py": "S"},
            dirs=["demo/core"],
        )
        dest = tmp_path / "out"
        extract.extract_demo(archive, dest, assume_yes=True)
        assert (dest / "demo" / "core" / "settings.py").read_text() == "S"

    def test_symlinks_are_skipped(self, tmp_path):
        archive = _make_tarball(
            tmp_path / "src.tar.gz",
            files={"demo/real.py": "ok"},
            symlinks=[("demo/evil", "/etc/passwd")],
        )
        dest = tmp_path / "out"
        extract.extract_demo(archive, dest, assume_yes=True)
        assert (dest / "demo" / "real.py").exists()
        assert not (dest / "demo" / "evil").exists()

    def test_rejects_path_traversal(self, tmp_path):
        archive = _make_tarball(tmp_path / "src.tar.gz", files={"demo/../../evil": "x"})
        with pytest.raises(QuickstartError, match="Unsafe path"):
            extract.extract_demo(archive, tmp_path / "out", assume_yes=True)

    def test_overwrite_declined(self, tmp_path):
        archive = _make_tarball(tmp_path / "src.tar.gz", files={"demo/manage.py": "new"})
        dest = tmp_path / "out"
        (dest / "demo").mkdir(parents=True)
        (dest / "demo" / "manage.py").write_text("old")
        with pytest.raises(QuickstartError, match="not replaced"):
            extract.extract_demo(archive, dest, confirm=lambda paths: False)
        assert (dest / "demo" / "manage.py").read_text() == "old"

    def test_overwrite_confirmed(self, tmp_path):
        archive = _make_tarball(tmp_path / "src.tar.gz", files={"demo/manage.py": "new"})
        dest = tmp_path / "out"
        (dest / "demo").mkdir(parents=True)
        (dest / "demo" / "manage.py").write_text("old")
        extract.extract_demo(archive, dest, confirm=lambda paths: True)
        assert (dest / "demo" / "manage.py").read_text() == "new"


class TestPromptOverwrite:
    def test_yes(self, monkeypatch, tmp_path):
        monkeypatch.setattr("builtins.input", lambda prompt="": "y")
        assert extract._prompt_overwrite([tmp_path / "a"]) is True

    def test_no(self, monkeypatch, tmp_path):
        monkeypatch.setattr("builtins.input", lambda prompt="": "")
        assert extract._prompt_overwrite([tmp_path / "a"]) is False

    def test_truncates_long_list(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")
        extract._prompt_overwrite([tmp_path / f"f{i}" for i in range(25)])
        assert "and 5 more" in capsys.readouterr().out
