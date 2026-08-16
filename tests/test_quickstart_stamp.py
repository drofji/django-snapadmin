"""Tests for :mod:`snapadmin.quickstart.stamp` — the extracted demo's version stamp (#CHK2b)."""

from __future__ import annotations

import json

from snapadmin.quickstart import stamp


def _stamped(demo_dir, version="1.0.0", files=("manage.py",)):
    demo_dir.mkdir(parents=True, exist_ok=True)
    return stamp.write_stamp(demo_dir, version, files)


class TestWriteStamp:
    def test_writes_version_and_sorted_files(self, tmp_path):
        demo = tmp_path / "demo"
        demo.mkdir()
        path = stamp.write_stamp(demo, "0.1.0b6", ["b.py", "a.py"])
        assert path == demo / stamp.STAMP_NAME
        data = json.loads(path.read_text())
        assert data["version"] == "0.1.0b6"
        assert data["files"] == ["a.py", "b.py"]

    def test_strips_leading_v_and_normalises_separators(self, tmp_path):
        demo = tmp_path / "demo"
        demo.mkdir()
        stamp.write_stamp(demo, "v2.0", ["core\\settings.py"])
        data = json.loads((demo / stamp.STAMP_NAME).read_text())
        assert data["version"] == "2.0"
        assert data["files"] == ["core/settings.py"]

    def test_never_records_itself(self, tmp_path):
        demo = tmp_path / "demo"
        demo.mkdir()
        stamp.write_stamp(demo, "1.0", ["manage.py", stamp.STAMP_NAME])
        assert json.loads((demo / stamp.STAMP_NAME).read_text())["files"] == ["manage.py"]

    def test_overwrites_a_previous_stamp(self, tmp_path):
        demo = tmp_path / "demo"
        _stamped(demo, "1.0", ["old.py"])
        stamp.write_stamp(demo, "2.0", ["new.py"])
        assert stamp.read_stamp(demo) == {"version": "2.0", "files": ["new.py"]}


class TestReadStamp:
    def test_missing_tree_or_stamp(self, tmp_path):
        assert stamp.read_stamp(tmp_path / "nope") is None
        (tmp_path / "demo").mkdir()
        assert stamp.read_stamp(tmp_path / "demo") is None

    def test_corrupt_json_is_not_an_error(self, tmp_path):
        demo = tmp_path / "demo"
        demo.mkdir()
        (demo / stamp.STAMP_NAME).write_text("{not json")
        assert stamp.read_stamp(demo) is None

    def test_non_mapping_payload_is_ignored(self, tmp_path):
        demo = tmp_path / "demo"
        demo.mkdir()
        (demo / stamp.STAMP_NAME).write_text("[1, 2]")
        assert stamp.read_stamp(demo) is None


class TestStampedVersion:
    def test_returns_version(self, tmp_path):
        demo = tmp_path / "demo"
        _stamped(demo, "0.1.0b6")
        assert stamp.stamped_version(demo) == "0.1.0b6"

    def test_none_without_a_stamp(self, tmp_path):
        assert stamp.stamped_version(tmp_path / "demo") is None

    def test_none_when_the_stamp_has_no_version(self, tmp_path):
        demo = tmp_path / "demo"
        demo.mkdir()
        (demo / stamp.STAMP_NAME).write_text(json.dumps({"files": []}))
        assert stamp.stamped_version(demo) is None


class TestOrphanedFiles:
    def test_lists_files_the_new_release_dropped(self, tmp_path):
        demo = tmp_path / "demo"
        demo.mkdir()
        (demo / "gone.html").write_text("old")
        (demo / "kept.py").write_text("old")
        stamp.write_stamp(demo, "1.0", ["gone.html", "kept.py"])
        assert stamp.orphaned_files(demo, ["kept.py"]) == [demo / "gone.html"]

    def test_ignores_files_already_deleted(self, tmp_path):
        demo = tmp_path / "demo"
        _stamped(demo, "1.0", ["gone.html"])
        assert stamp.orphaned_files(demo, []) == []

    def test_never_touches_user_files(self, tmp_path):
        """A file the user added was never in the manifest, so it is not ours to delete."""
        demo = tmp_path / "demo"
        demo.mkdir()
        (demo / "my_notes.md").write_text("mine")
        stamp.write_stamp(demo, "1.0", ["manage.py"])
        assert stamp.orphaned_files(demo, []) == []

    def test_without_a_stamp_nothing_is_orphaned(self, tmp_path):
        demo = tmp_path / "demo"
        demo.mkdir()
        (demo / "whatever.py").write_text("x")
        assert stamp.orphaned_files(demo, []) == []

    def test_ignores_a_manifest_entry_escaping_the_tree(self, tmp_path):
        """A hand-edited manifest must not turn into a delete-anything primitive."""
        demo = tmp_path / "demo"
        demo.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("keep me")
        (demo / stamp.STAMP_NAME).write_text(json.dumps({"version": "1.0", "files": ["../outside.txt"]}))
        assert stamp.orphaned_files(demo, []) == []
        assert outside.exists()


class TestFindDemoTree:
    def test_finds_the_tree_it_is_run_from(self, tmp_path, monkeypatch):
        demo = tmp_path / "demo"
        _stamped(demo)
        monkeypatch.chdir(demo)
        assert stamp.find_demo_tree() == demo.resolve()

    def test_finds_a_demo_subdirectory(self, tmp_path, monkeypatch):
        demo = tmp_path / "demo"
        _stamped(demo)
        monkeypatch.chdir(tmp_path)
        assert stamp.find_demo_tree() == demo.resolve()

    def test_walks_up_to_the_parent(self, tmp_path, monkeypatch):
        demo = tmp_path / "demo"
        _stamped(demo)
        nested = demo / "apps" / "shop"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)
        assert stamp.find_demo_tree() == demo.resolve()

    def test_stops_after_max_parents(self, tmp_path, monkeypatch):
        demo = tmp_path / "demo"
        _stamped(demo)
        deep = demo / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)
        assert stamp.find_demo_tree(max_parents=2) is None

    def test_none_when_there_is_no_stamp(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert stamp.find_demo_tree() is None


class TestDriftNotice:
    def test_none_without_a_stamp(self, tmp_path):
        assert stamp.drift_notice(tmp_path / "demo", "0.1.0b6") is None

    def test_none_when_the_versions_match(self, tmp_path):
        demo = tmp_path / "demo"
        _stamped(demo, "0.1.0b6")
        assert stamp.drift_notice(demo, "0.1.0b6") is None

    def test_none_when_the_installed_version_is_unknown(self, tmp_path):
        demo = tmp_path / "demo"
        _stamped(demo, "0.1.0b6")
        assert stamp.drift_notice(demo, None) is None

    def test_names_both_versions_and_the_fix(self, tmp_path):
        demo = tmp_path / "demo"
        _stamped(demo, "0.1.0b5")
        notice = stamp.drift_notice(demo, "0.1.0b6")
        assert "0.1.0b5" in notice
        assert "0.1.0b6" in notice
        assert "snapadmin-demo" in notice
