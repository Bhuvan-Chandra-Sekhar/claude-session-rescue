"""``backup`` -- zip the whole session store with a manifest and checksums.

The intended moment for this is five minutes before you reinstall, upgrade, or
"just try deleting the config folder". It reads the store and writes one archive
somewhere else; it never touches the original.

The manifest records a SHA-256 for every file, so a restore can be verified
rather than hoped at.
"""

from __future__ import annotations

import hashlib
import json
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from claude_session_rescue import __version__, store
from claude_session_rescue.safety import assert_write_allowed
from claude_session_rescue.commands.scan import human_bytes

CHUNK = 1024 * 1024


def sha256_of(path: Path) -> str:
    """Streaming checksum -- these files are far too big to read whole."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def collect_files(projects_dir: Path) -> Tuple[List[Path], List[str]]:
    """Every real file under the store, plus the symlinks we refused to follow.

    A backup that only keeps the file types we recognise is not a backup, so we
    take everything -- but *not* through a symlink.

    Following symlinks here would mean the archive silently contains whatever
    they point at. Anything that can write into the session store could then
    place a link to, say, an SSH key or a password database, and it would be
    copied into an archive the user believes contains only their transcripts,
    and may well pass on to somebody else. The user asked to back up this
    directory; that is what they get. Links are reported so nothing vanishes
    without being mentioned.
    """
    files: List[Path] = []
    skipped_links: List[str] = []
    for path in sorted(projects_dir.rglob("*")):
        try:
            if path.is_symlink():
                skipped_links.append(str(path.relative_to(projects_dir)).replace("\\", "/"))
                continue
            if path.is_file():
                files.append(path)
        except OSError:
            continue
    return files, skipped_links


def run(args) -> int:
    projects_dir = Path(args.projects_dir)
    usable, message = store.store_status(projects_dir)
    if not usable:
        print(message)
        return 1

    default_name = "claude-projects-backup-{0}.zip".format(time.strftime("%Y%m%d-%H%M%S"))
    archive = Path(args.out).expanduser() if args.out else Path.cwd() / default_name
    if archive.is_dir():
        archive = archive / default_name

    try:
        assert_write_allowed(archive, projects_dir)
    except Exception as exc:  # WriteRefused
        print(str(exc))
        return 2

    files, skipped_links = collect_files(projects_dir)
    if not files:
        print("Nothing to back up: {0} contains no files.".format(projects_dir))
        return 1

    manifest: Dict[str, Any] = {
        "tool": "claude-session-rescue",
        "toolVersion": __version__,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sourceDir": str(projects_dir),
        "fileCount": len(files),
        "files": [],
    }

    total = 0
    skipped: List[str] = []
    for path in files:
        try:
            size = path.stat().st_size
            checksum = sha256_of(path)
        except OSError as exc:
            skipped.append("{0}: {1}".format(path.name, exc))
            continue
        total += size
        manifest["files"].append({
            "path": str(path.relative_to(projects_dir)).replace("\\", "/"),
            "bytes": size,
            "sha256": checksum,
        })
    manifest["totalBytes"] = total
    manifest["skipped"] = skipped
    manifest["skippedSymlinks"] = skipped_links

    if args.dry_run:
        print("[dry run] would archive {0} files ({1}) from {2}".format(
            len(manifest["files"]), human_bytes(total), projects_dir))
        print("[dry run] archive would be written to {0}".format(archive))
        if skipped:
            print("[dry run] {0} file(s) unreadable and would be skipped".format(len(skipped)))
        if skipped_links:
            print("[dry run] {0} symlink(s) would be skipped, not followed".format(
                len(skipped_links)))
        return 0

    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        zf.writestr("MANIFEST.json", json.dumps(manifest, indent=2))
        zf.writestr("README-RESTORE.txt", RESTORE_NOTE.format(source=projects_dir))
        for entry in manifest["files"]:
            source = projects_dir / entry["path"]
            try:
                zf.write(source, arcname="projects/" + entry["path"])
            except OSError as exc:
                skipped.append("{0}: {1}".format(entry["path"], exc))

    print("Backed up {0} files ({1}) from {2}".format(
        len(manifest["files"]), human_bytes(total), projects_dir))
    print("Archive: {0} ({1})".format(archive, human_bytes(archive.stat().st_size)))
    print("MANIFEST.json inside the archive lists a SHA-256 for every file.")
    if skipped:
        print("{0} file(s) could not be read and were skipped:".format(len(skipped)))
        for note in skipped[:10]:
            print("  {0}".format(note))
    if skipped_links:
        print("\n{0} symlink(s) were skipped rather than followed, so the archive "
              "contains only files that really live in this directory:".format(
                  len(skipped_links)))
        for link in skipped_links[:10]:
            print("  {0}".format(link))
        if len(skipped_links) > 10:
            print("  ... and {0} more (all listed in MANIFEST.json)".format(
                len(skipped_links) - 10))
    print("\nThe source directory was not modified.")
    return 0


RESTORE_NOTE = """claude-session-rescue backup
============================

This archive contains a copy of a Claude Code session store.

Layout
------
  MANIFEST.json     every file, its size and its SHA-256
  projects/...      an exact copy of {source}

To restore
----------
1. Close Claude Code.
2. Copy the contents of projects/ back into your session store directory
   (by default ~/.claude/projects).
3. Verify with the checksums in MANIFEST.json before relying on the restore.

Restoring is a manual step on purpose: this tool never writes into a Claude
data directory.
"""
