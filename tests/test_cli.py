"""End-to-end tests through the command line interface."""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from claude_session_rescue.cli import main
from claude_session_rescue.safety import WriteRefused, assert_write_allowed


def run(capsys, *argv):
    code = main(list(argv))
    return code, capsys.readouterr().out


# ----------------------------------------------------------------------
# Zero-knowledge entry points
# ----------------------------------------------------------------------


def test_bare_command_prints_an_overview_not_a_usage_error(capsys, store_dir):
    code, out = run(capsys, "--projects-dir", str(store_dir))
    assert code == 0
    assert "Session store" in out
    assert "Next steps" in out


def test_missing_store_explains_itself(capsys, tmp_path):
    code, out = run(capsys, "--projects-dir", str(tmp_path / "nothing-here"))
    assert code == 1
    assert "No session store found" in out
    assert "--projects-dir" in out


def test_empty_store_explains_itself(capsys, tmp_path):
    empty = tmp_path / "projects"
    empty.mkdir()
    code, out = run(capsys, "scan", "--projects-dir", str(empty))
    assert code == 1
    assert "empty" in out


# ----------------------------------------------------------------------
# scan
# ----------------------------------------------------------------------


def test_scan_lists_projects_and_flags_problems(capsys, store_dir):
    code, out = run(capsys, "scan", "--projects-dir", str(store_dir))
    assert code == 0
    assert "/home/example/projects/demo app" in out       # recovered from cwd
    assert "SPLIT into 2 segments" in out
    assert "ORPHANED" in out
    assert "git worktree" in out


def test_scan_json_is_machine_readable(capsys, store_dir):
    code, out = run(capsys, "scan", "--projects-dir", str(store_dir), "--json")
    assert code == 0
    data = json.loads(out)
    slugs = {p["slug"]: p for p in data["projects"]}
    live = [p for p in data["projects"] if p["originConfidence"] == "recorded"]
    assert live, "at least one project should be resolved from recorded cwd values"
    assert any(p["isWorktree"] for p in data["projects"])
    assert any(p["folderExists"] is False for p in data["projects"])


def test_scan_quick_mode_still_works(capsys, store_dir):
    code, out = run(capsys, "scan", "--projects-dir", str(store_dir), "--quick")
    assert code == 0
    assert "session files, not analysed" in out


def test_global_flag_before_subcommand_is_not_lost(capsys, store_dir):
    """argparse subparser defaults must not clobber a value set earlier."""
    code, out = run(capsys, "--projects-dir", str(store_dir), "scan")
    assert code == 0
    assert str(store_dir) in out


# ----------------------------------------------------------------------
# doctor
# ----------------------------------------------------------------------


def test_doctor_explains_the_compaction_split(capsys, store_dir):
    code, out = run(capsys, "doctor", "bbbbbbbb", "--projects-dir", str(store_dir))
    assert code == 0
    assert "split into 2 parts" in out
    assert "logicalParentUuid" in out
    assert "claude-session-rescue export" in out


def test_doctor_explains_an_orphaned_project(capsys, store_dir):
    code, out = run(capsys, "doctor", "--projects-dir", str(store_dir))
    assert code == 0
    assert "no longer there" in out
    assert "/home/example/old_place/demo app" in out


def test_doctor_finds_sessions_by_folder_not_by_slug_string(capsys, store_dir):
    """--project matches on the cwd recorded inside the files."""
    code, out = run(capsys, "doctor", "--project", "/home/example/projects/demo app",
                    "--projects-dir", str(store_dir))
    assert code == 0
    assert "old_place" not in out


def test_doctor_rejects_an_unknown_session_helpfully(capsys, store_dir):
    code, out = run(capsys, "doctor", "zzzzzzzz", "--projects-dir", str(store_dir))
    assert code == 1
    assert "No session matches" in out


def test_doctor_report_is_sanitized(capsys, store_dir, tmp_path):
    report_path = tmp_path / "report.json"
    code, out = run(capsys, "doctor", "--projects-dir", str(store_dir),
                    "--report", str(report_path))
    assert code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))

    blob = json.dumps(report)
    # Nothing identifying may appear: no paths, no project names, no uuids.
    for forbidden in ["/home/example", "demo app", "old_place", "bbbbbbbb",
                      "hello", "carry on", ".jsonl"]:
        assert forbidden not in blob, forbidden
    # But the useful, non-identifying facts must be there.
    assert report["projectCount"] == 4
    assert "topLevelFieldNames" in report
    assert "logicalParentUuid" in report["topLevelFieldNames"]
    assert "2.1.229" in report["versionsSeen"]
    assert any(s["compactBoundaries"] == 1
               for p in report["projects"] for s in p["sessions"])


def test_doctor_report_stays_sanitized_when_a_file_cannot_be_read(capsys, tmp_path):
    """Regression: the failure path must not leak what the happy path hides.

    An unreadable session used to put ``explain_os_error``'s message -- which
    embeds the absolute path -- straight into the report the README calls safe
    to paste into a public issue. Paths carry usernames, client names and
    project names.
    """
    from claude_session_rescue import slugs
    from tests import fixtures

    work_dir = tmp_path / "clients" / "AcmeCorp" / "merger_docs"
    work_dir.mkdir(parents=True)
    # The store sits in its own subtree: safety.py protects the store *and its
    # parent*, so a report written beside the store would be refused (exit 2).
    projects = tmp_path / "store" / "projects"
    project = projects / slugs.slug_for_path(work_dir)
    unreadable = project / "aaaa1111-0000-0000-0000-000000000000.jsonl"
    fixtures.write_jsonl(unreadable, [
        fixtures.user_turn("u1", None, "hi", "2026-01-01T10:00:00.000Z", cwd=str(work_dir)),
    ])
    unreadable.chmod(0o000)
    if os.access(str(unreadable), os.R_OK):
        pytest.skip("cannot make a file unreadable here (running as root?)")

    report_path = tmp_path / "report.json"
    try:
        code, _ = run(capsys, "doctor", "--projects-dir", str(projects),
                      "--report", str(report_path))
        assert code == 0
        blob = report_path.read_text(encoding="utf-8")
        for forbidden in ["AcmeCorp", "merger_docs", "clients", ".jsonl",
                          "aaaa1111", str(tmp_path)]:
            assert forbidden not in blob, forbidden
        # The failure is still described, just without saying where.
        kinds = [s["errorKind"] for p in json.loads(blob)["projects"] for s in p["sessions"]]
        assert "permission-denied" in kinds
    finally:
        unreadable.chmod(0o644)


# ----------------------------------------------------------------------
# example -- the walkthrough, generated from whatever is on this machine
# ----------------------------------------------------------------------


def test_example_narrates_this_machines_own_store(capsys, store_dir):
    code, out = run(capsys, "example", "--projects-dir", str(store_dir))
    assert code == 0
    # It describes the reader's actual data, not a canned story.
    assert str(store_dir) in out
    assert "/home/example/projects/demo app" in out
    assert "git worktree" in out
    # And it names the real problems it found, with runnable commands.
    assert "no longer there" in out
    assert "claude-session-rescue export" in out
    assert "claude-session-rescue backup" in out


def test_example_explains_the_split_it_actually_found(capsys, store_dir):
    code, out = run(capsys, "example", "--projects-dir", str(store_dir))
    assert "Compacted session" in out
    assert "logicalParentUuid" in out
    # The honest limit is stated, not implied.
    assert "cannot patch the desktop app" in out


def test_example_is_still_useful_on_a_healthy_store(capsys, tmp_path):
    """No problems is a valid outcome and must not read like an error."""
    from claude_session_rescue import slugs
    from tests import fixtures

    # A project folder that really exists, so nothing is flagged as orphaned.
    # It lives under tmp_path: tests never write outside their own directory.
    work_dir = tmp_path / "code" / "a project"
    work_dir.mkdir(parents=True)

    projects = tmp_path / "projects"
    healthy = projects / slugs.slug_for_path(work_dir)
    fixtures.write_jsonl(
        healthy / "aaaaaaaa-0000-0000-0000-000000000001.jsonl",
        [
            fixtures.user_turn("u1", None, "hello", "2026-01-01T10:00:00.000Z",
                               cwd=str(work_dir)),
            fixtures.assistant_turn("a1", "u1", "hi", "2026-01-01T10:00:01.000Z",
                                    cwd=str(work_dir)),
        ],
    )

    code, out = run(capsys, "example", "--projects-dir", str(projects))
    assert code == 0
    assert "None here." in out
    assert "Nothing on this machine currently needs rescuing" in out


def test_example_on_a_missing_store_explains_itself(capsys, tmp_path):
    code, out = run(capsys, "example", "--projects-dir", str(tmp_path / "gone"))
    assert code == 1
    assert "No session store found" in out


def test_example_contains_no_hardcoded_project_names(capsys, store_dir):
    """Regression guard: the walkthrough must come from data, not from a script."""
    code, out = run(capsys, "example", "--projects-dir", str(store_dir))
    for leaked in ["my-project", "alex", "Refactoring the importer"]:
        assert leaked not in out, leaked


# ----------------------------------------------------------------------
# export
# ----------------------------------------------------------------------


def test_export_includes_the_segment_the_app_cannot_show(capsys, store_dir, tmp_path):
    out_dir = tmp_path / "rescued"
    code, out = run(capsys, "export", "bbbbbbbb", "--projects-dir", str(store_dir),
                    "--out", str(out_dir))
    assert code == 0
    text = (out_dir / "bbbbbbbb-0000-0000-0000-000000000002.md").read_text(encoding="utf-8")

    # Content from before and after the seam, and the seam itself.
    assert "start the long job" in text
    assert "Context compaction (auto)" in text
    assert "carry on from where we were" in text
    assert "Continuing. Here is the rest." in text
    # Ordering: segment 2 comes after segment 1.
    assert text.index("start the long job") < text.index("carry on from where we were")


def test_export_defaults_to_a_dry_run_when_no_destination_is_given(capsys, store_dir, tmp_path,
                                                                  monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out = run(capsys, "export", "bbbbbbbb", "--projects-dir", str(store_dir))
    assert code == 0
    assert "[dry run]" in out
    assert not (tmp_path / "rescued-sessions").exists()


def test_export_splits_into_parts_with_an_index(capsys, store_dir, tmp_path):
    out_dir = tmp_path / "split"
    code, _ = run(capsys, "export", "bbbbbbbb", "--projects-dir", str(store_dir),
                  "--out", str(out_dir), "--split-bytes", "900")
    assert code == 0
    parts = sorted(p.name for p in out_dir.glob("*part*.md"))
    assert len(parts) >= 2
    index = (out_dir / "bbbbbbbb-0000-0000-0000-000000000002-INDEX.md").read_text(encoding="utf-8")
    for part in parts:
        assert part in index


def test_export_json_round_trips(capsys, store_dir, tmp_path):
    out_dir = tmp_path / "json"
    code, _ = run(capsys, "export", "bbbbbbbb", "--projects-dir", str(store_dir),
                  "--out", str(out_dir), "--format", "json")
    assert code == 0
    data = json.loads((out_dir / "bbbbbbbb-0000-0000-0000-000000000002.json").read_text("utf-8"))
    assert data["meta"]["segments"][1]["startedByCompaction"] is True
    assert len(data["entries"]) > 0
    assert {e["segment"] for e in data["entries"]} == {0, 1}


def test_export_txt_has_no_markdown_markup(capsys, store_dir, tmp_path):
    out_dir = tmp_path / "txt"
    code, _ = run(capsys, "export", "bbbbbbbb", "--projects-dir", str(store_dir),
                  "--out", str(out_dir), "--format", "txt")
    assert code == 0
    text = (out_dir / "bbbbbbbb-0000-0000-0000-000000000002.txt").read_text("utf-8")
    assert "**" not in text
    assert "```" not in text


def test_export_by_project_folder(capsys, store_dir, tmp_path):
    out_dir = tmp_path / "byproject"
    code, out = run(capsys, "export", "--project", "/home/example/old_place/demo app",
                    "--projects-dir", str(store_dir), "--out", str(out_dir))
    assert code == 0
    assert list(out_dir.glob("*.md"))


def test_export_of_an_empty_session_does_not_crash(capsys, store_dir, tmp_path):
    code, out = run(capsys, "export", "eeeeeeee", "--projects-dir", str(store_dir),
                    "--out", str(tmp_path / "empty"))
    assert code == 0


# ----------------------------------------------------------------------
# backup and the read-only invariant
# ----------------------------------------------------------------------


def test_backup_writes_an_archive_with_a_manifest(capsys, store_dir, tmp_path):
    archive = tmp_path / "backup.zip"
    code, out = run(capsys, "backup", "--projects-dir", str(store_dir), "--out", str(archive))
    assert code == 0
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("MANIFEST.json"))
    assert "MANIFEST.json" in names
    assert any(n.startswith("projects/") for n in names)
    assert manifest["fileCount"] == len(manifest["files"])
    assert all(len(entry["sha256"]) == 64 for entry in manifest["files"])


def test_backup_does_not_follow_symlinks_out_of_the_store(capsys, tmp_path):
    """A link inside the store must not pull outside content into the archive.

    Anything able to write into the session store could otherwise plant a link
    to an SSH key or a password database and have it copied into an archive the
    user believes holds only transcripts -- and may pass on to someone else.
    """
    from tests import fixtures

    secret = tmp_path / "outside" / "id_rsa"
    secret.parent.mkdir(parents=True)
    secret.write_text("PRIVATE-KEY-MATERIAL", encoding="utf-8")

    projects = tmp_path / "store" / "projects"
    project = projects / "-p"
    fixtures.simple_session(project / "aaaa0000-0000-0000-0000-000000000000.jsonl")
    try:
        (project / "notes.txt").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("this platform will not let the test create a symlink")

    archive = tmp_path / "backup.zip"
    code, out = run(capsys, "backup", "--projects-dir", str(projects), "--out", str(archive))
    assert code == 0

    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        blob = b"".join(zf.read(n) for n in names)
    assert not any(n.endswith("notes.txt") for n in names)
    assert b"PRIVATE-KEY-MATERIAL" not in blob
    # Skipped, but reported -- nothing disappears silently.
    assert "symlink" in out
    with zipfile.ZipFile(archive) as zf:
        manifest = json.loads(zf.read("MANIFEST.json"))
    # Recorded store-relative, e.g. "-p/notes.txt".
    assert any(link.endswith("notes.txt") for link in manifest["skippedSymlinks"])


def test_backup_defaults_to_a_dry_run(capsys, store_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out = run(capsys, "backup", "--projects-dir", str(store_dir))
    assert code == 0
    assert "[dry run]" in out
    assert not list(tmp_path.glob("*.zip"))


def test_writing_into_the_session_store_is_refused(store_dir):
    with pytest.raises(WriteRefused):
        assert_write_allowed(store_dir / "anything.md", store_dir)
    with pytest.raises(WriteRefused):
        assert_write_allowed(store_dir.parent / "sneaky.zip", store_dir)


def test_writing_into_claude_home_is_refused(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    (fake_home / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    with pytest.raises(WriteRefused):
        assert_write_allowed(fake_home / ".claude" / "out.md")


def test_export_refuses_to_write_into_the_store(capsys, store_dir):
    code = main(["export", "bbbbbbbb", "--projects-dir", str(store_dir),
                 "--out", str(store_dir / "out")])
    assert code == 2


def test_the_store_is_never_modified(capsys, store_dir, tmp_path):
    """The strongest form of the invariant: byte-for-byte, nothing changed."""
    def snapshot():
        return {
            str(p.relative_to(store_dir)): p.read_bytes()
            for p in sorted(store_dir.rglob("*")) if p.is_file()
        }

    before = snapshot()
    run(capsys, "scan", "--projects-dir", str(store_dir))
    run(capsys, "doctor", "--projects-dir", str(store_dir))
    run(capsys, "example", "--projects-dir", str(store_dir))
    run(capsys, "export", "bbbbbbbb", "--projects-dir", str(store_dir),
        "--out", str(tmp_path / "o"))
    run(capsys, "backup", "--projects-dir", str(store_dir), "--out", str(tmp_path / "b.zip"))
    assert snapshot() == before
