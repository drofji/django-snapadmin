"""Tests for :mod:`snapadmin.quickstart.extract` (#CLI3c)."""

from __future__ import annotations

import io
import tarfile

import pytest

from snapadmin.quickstart import QuickstartError, extract, stamp

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
            extract.extract_demo(archive, dest, confirm=lambda replaced, removed: False)
        assert (dest / "demo" / "manage.py").read_text() == "old"

    def test_overwrite_confirmed(self, tmp_path):
        archive = _make_tarball(tmp_path / "src.tar.gz", files={"demo/manage.py": "new"})
        dest = tmp_path / "out"
        (dest / "demo").mkdir(parents=True)
        (dest / "demo" / "manage.py").write_text("old")
        extract.extract_demo(archive, dest, confirm=lambda replaced, removed: True)
        assert (dest / "demo" / "manage.py").read_text() == "new"


class TestVersionStamp:
    def test_stamps_the_extracted_tree(self, tmp_path):
        archive = _make_tarball(
            tmp_path / "src.tar.gz",
            files={"demo/manage.py": "m", "demo/core/settings.py": "s"},
        )
        demo = extract.extract_demo(archive, tmp_path / "out", version="0.1.0b6", assume_yes=True)
        assert stamp.stamped_version(demo) == "0.1.0b6"
        assert stamp.read_stamp(demo)["files"] == ["core/settings.py", "manage.py"]

    def test_unversioned_extraction_writes_no_stamp(self, tmp_path):
        archive = _make_tarball(tmp_path / "src.tar.gz", files={"demo/manage.py": "m"})
        demo = extract.extract_demo(archive, tmp_path / "out", assume_yes=True)
        assert stamp.read_stamp(demo) is None


class TestRefreshPruning:
    def _first_release(self, tmp_path):
        archive = _make_tarball(
            tmp_path / "v1.tar.gz",
            files={"demo/manage.py": "m", "demo/templates/dashboard.html": "old panel"},
        )
        return extract.extract_demo(archive, tmp_path / "out", version="1.0", assume_yes=True)

    def test_removes_a_file_the_new_release_dropped(self, tmp_path, capsys):
        demo = self._first_release(tmp_path)
        newer = _make_tarball(tmp_path / "v2.tar.gz", files={"demo/manage.py": "m2"})
        extract.extract_demo(newer, tmp_path / "out", version="2.0", assume_yes=True)
        assert not (demo / "templates" / "dashboard.html").exists()
        assert (demo / "manage.py").read_text() == "m2"
        assert stamp.stamped_version(demo) == "2.0"
        assert "Removed 1 file" in capsys.readouterr().out

    def test_keeps_files_the_user_added(self, tmp_path):
        demo = self._first_release(tmp_path)
        (demo / ".env").write_text("SECRET=1")
        newer = _make_tarball(tmp_path / "v2.tar.gz", files={"demo/manage.py": "m2"})
        extract.extract_demo(newer, tmp_path / "out", version="2.0", assume_yes=True)
        assert (demo / ".env").read_text() == "SECRET=1"

    def test_pruning_needs_the_same_confirmation_as_an_overwrite(self, tmp_path):
        demo = self._first_release(tmp_path)
        newer = _make_tarball(tmp_path / "v2.tar.gz", files={"demo/other.py": "x"})
        seen: dict = {}
        with pytest.raises(QuickstartError, match="not replaced"):
            extract.extract_demo(
                newer,
                tmp_path / "out",
                version="2.0",
                confirm=lambda replaced, removed: seen.update(replaced=replaced, removed=removed) or False,
            )
        assert seen["replaced"] == []
        assert seen["removed"] == [demo / "manage.py", demo / "templates" / "dashboard.html"]
        assert (demo / "templates" / "dashboard.html").exists()

    def test_a_file_replaced_by_a_directory_upstream(self, tmp_path):
        """The orphan goes before the new tree is written, or the mkdir would collide."""
        demo = self._first_release(tmp_path)
        newer = _make_tarball(
            tmp_path / "v2.tar.gz",
            files={"demo/manage.py": "m2", "demo/templates/dashboard.html/index.html": "panel"},
            dirs=["demo/templates/dashboard.html"],
        )
        extract.extract_demo(newer, tmp_path / "out", version="2.0", assume_yes=True)
        assert (demo / "templates" / "dashboard.html" / "index.html").read_text() == "panel"

    def test_an_unstamped_tree_is_never_pruned(self, tmp_path):
        dest = tmp_path / "out"
        (dest / "demo").mkdir(parents=True)
        (dest / "demo" / "leftover.py").write_text("not ours")
        archive = _make_tarball(tmp_path / "src.tar.gz", files={"demo/manage.py": "m"})
        extract.extract_demo(archive, dest, version="2.0", assume_yes=True)
        assert (dest / "demo" / "leftover.py").exists()


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

    def test_lists_deletions_separately(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr("builtins.input", lambda prompt="": "y")
        assert extract._prompt_overwrite([], [tmp_path / "gone.html"]) is True
        out = capsys.readouterr().out
        assert "older demo release" in out
        assert "gone.html" in out
        assert "already exist" not in out
