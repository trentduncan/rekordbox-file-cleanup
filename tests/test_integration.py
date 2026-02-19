"""
Subprocess-based integration tests for file-cleanup.py CLI.

These tests invoke the CLI as a subprocess to ensure real-world behavior.
Each test uses pytest's tmp_path fixture for complete isolation.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

REPO_ROOT = Path(__file__).parent.parent.resolve()
CLI_SCRIPT = REPO_ROOT / "file-cleanup.py"


def run_cli(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """
    Run file-cleanup.py with the given arguments.
    Returns CompletedProcess with stdout/stderr captured as strings.
    """
    cmd = [sys.executable, str(CLI_SCRIPT)] + args
    return subprocess.run(
        cmd,
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
    )


def write_xml(xml_path: Path, locations: list[str]) -> None:
    """
    Write a minimal valid Rekordbox XML containing TRACK elements
    with Location attributes.
    """
    tracks = "\n".join(
        f'    <TRACK TrackID="{i}" Location="{loc}" />'
        for i, loc in enumerate(locations, start=1)
    )
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="6.0.0" />
  <COLLECTION Entries="{len(locations)}">
{tracks}
  </COLLECTION>
</DJ_PLAYLISTS>
"""
    xml_path.write_text(content, encoding="utf-8")


def touch_audio(path: Path, size: int = 16) -> None:
    """
    Create a file with some bytes so file size > 0.
    Creates parent directories as needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size)


def path_to_file_uri(path: Path) -> str:
    """
    Convert a Path to a file://localhost/... URI with percent encoding.
    Spaces become %20, & becomes %26, etc.
    """
    abs_path = str(path.resolve())
    encoded = quote(abs_path, safe="/")
    return f"file://localhost{encoded}"


class TestPreviewCountsBasic:
    """
    Test A: preview_counts_basic
    Verify preview command correctly counts orphans, referenced files, and ignores macOS metadata.
    """

    def test_preview_counts_basic(self, tmp_path: Path) -> None:
        scan_root = tmp_path / "Music"
        scan_root.mkdir()

        referenced_1 = scan_root / "referenced_1.mp3"
        referenced_2 = scan_root / "referenced_2.wav"
        orphan_1 = scan_root / "orphan_1.mp3"
        orphan_2 = scan_root / "orphan_2.flac"
        ignored_1 = scan_root / "._junk.mp3"
        ignored_2 = scan_root / ".DS_Store"

        for f in [referenced_1, referenced_2, orphan_1, orphan_2, ignored_1, ignored_2]:
            touch_audio(f)

        xml_path = tmp_path / "rekordbox.xml"
        write_xml(xml_path, [str(referenced_1), str(referenced_2)])

        result = run_cli([
            "preview",
            "--rekordbox-xml", str(xml_path),
            "--scan-root", str(scan_root),
        ])

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        stdout = result.stdout

        assert re.search(r"Orphans.*2", stdout), f"Expected 'Orphans...2' in output:\n{stdout}"
        assert "Rekordbox collection records (XML): 2" in stdout, f"Expected XML count 2:\n{stdout}"
        assert "Scanned disk files: 4" in stdout, f"Expected scanned files 4 (ignored files not counted):\n{stdout}"


class TestMoveAndManifestCreated:
    """
    Test B: move_and_manifest_created
    Verify move command moves orphans and creates manifest.
    """

    def test_move_dry_run_does_not_move(self, tmp_path: Path) -> None:
        scan_root = tmp_path / "Music"
        scan_root.mkdir()

        referenced = scan_root / "referenced.mp3"
        orphan = scan_root / "orphan.mp3"
        touch_audio(referenced)
        touch_audio(orphan)

        xml_path = tmp_path / "rekordbox.xml"
        write_xml(xml_path, [str(referenced)])

        result = run_cli([
            "move", "--dry-run",
            "--rekordbox-xml", str(xml_path),
            "--scan-root", str(scan_root),
        ])

        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        assert orphan.exists(), "Orphan should NOT be moved in dry-run mode"

        orphans_dir = scan_root / "_Rekordbox_Orphans"
        manifest = orphans_dir / "orphans_manifest.jsonl"
        if manifest.exists():
            content = manifest.read_text().strip()
            assert content == "", "Manifest should be empty in dry-run"

    def test_move_creates_manifest_and_moves_files(self, tmp_path: Path) -> None:
        scan_root = tmp_path / "Music"
        scan_root.mkdir()

        referenced_1 = scan_root / "referenced_1.mp3"
        referenced_2 = scan_root / "referenced_2.wav"
        orphan_1 = scan_root / "orphan_1.mp3"
        orphan_2 = scan_root / "orphan_2.flac"

        for f in [referenced_1, referenced_2, orphan_1, orphan_2]:
            touch_audio(f)

        xml_path = tmp_path / "rekordbox.xml"
        write_xml(xml_path, [str(referenced_1), str(referenced_2)])

        result = run_cli([
            "move",
            "--rekordbox-xml", str(xml_path),
            "--scan-root", str(scan_root),
        ])

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        assert not orphan_1.exists(), "orphan_1 should be moved"
        assert not orphan_2.exists(), "orphan_2 should be moved"

        orphans_dir = scan_root / "_Rekordbox_Orphans"
        assert orphans_dir.exists(), "Orphans directory should exist"

        moved_files = list(orphans_dir.glob("*"))
        moved_names = [f.name for f in moved_files if f.name != "orphans_manifest.jsonl"]
        assert "orphan_1.mp3" in moved_names, f"orphan_1.mp3 should be in orphans dir: {moved_names}"
        assert "orphan_2.flac" in moved_names, f"orphan_2.flac should be in orphans dir: {moved_names}"

        manifest = orphans_dir / "orphans_manifest.jsonl"
        assert manifest.exists(), "Manifest should exist"

        lines = [line for line in manifest.read_text().splitlines() if line.strip()]
        assert len(lines) == 2, f"Manifest should have 2 lines, got {len(lines)}"

        required_keys = {"ts", "src", "dst", "size_bytes", "mtime", "dev", "ino"}
        for line in lines:
            record = json.loads(line)
            assert required_keys <= set(record.keys()), f"Missing keys in record: {record.keys()}"
            assert "_Rekordbox_Orphans" in record["dst"], f"dst should point to orphans dir: {record['dst']}"


class TestRestoreRoundTrip:
    """
    Test C: restore_round_trip
    Verify restore command moves files back and handles manifest correctly.
    """

    def test_restore_round_trip(self, tmp_path: Path) -> None:
        scan_root = tmp_path / "Music"
        scan_root.mkdir()

        referenced = scan_root / "referenced.mp3"
        orphan_1 = scan_root / "orphan_1.mp3"
        orphan_2 = scan_root / "orphan_2.flac"

        touch_audio(referenced)
        touch_audio(orphan_1)
        touch_audio(orphan_2)

        original_orphan_1 = orphan_1.resolve()
        original_orphan_2 = orphan_2.resolve()

        xml_path = tmp_path / "rekordbox.xml"
        write_xml(xml_path, [str(referenced)])

        move_result = run_cli([
            "move",
            "--rekordbox-xml", str(xml_path),
            "--scan-root", str(scan_root),
        ])
        assert move_result.returncode == 0, f"Move failed: {move_result.stderr}"

        assert not orphan_1.exists(), "orphan_1 should be moved"
        assert not orphan_2.exists(), "orphan_2 should be moved"

        restore_result = run_cli([
            "restore",
            "--rekordbox-xml", str(xml_path),
            "--scan-root", str(scan_root),
        ])
        assert restore_result.returncode == 0, f"Restore failed: {restore_result.stderr}"

        assert original_orphan_1.exists(), "orphan_1 should be restored"
        assert original_orphan_2.exists(), "orphan_2 should be restored"

        orphans_dir = scan_root / "_Rekordbox_Orphans"
        if orphans_dir.exists():
            orphan_files = [f for f in orphans_dir.iterdir() if f.name != "orphans_manifest.jsonl"]
            assert len(orphan_files) == 0, f"Orphans dir should be empty after restore: {orphan_files}"

        manifest = orphans_dir / "orphans_manifest.jsonl"
        if manifest.exists():
            content = manifest.read_text().strip()
            assert content == "", f"Manifest should be empty or deleted: {content}"

        preview_result = run_cli([
            "preview",
            "--rekordbox-xml", str(xml_path),
            "--scan-root", str(scan_root),
        ])
        assert preview_result.returncode == 0

        assert re.search(r"Orphans.*2", preview_result.stdout), \
            f"After restore, orphan count should be 2 again:\n{preview_result.stdout}"


class TestFilenameCollisionFlatMove:
    """
    Test D: filename_collision_flat_move
    Verify collision handling when multiple files have the same basename.
    """

    def test_filename_collision_flat_move(self, tmp_path: Path) -> None:
        scan_root = tmp_path / "Music"

        subdir_a = scan_root / "A"
        subdir_b = scan_root / "B"
        subdir_a.mkdir(parents=True)
        subdir_b.mkdir(parents=True)

        dup_a = subdir_a / "dup.mp3"
        dup_b = subdir_b / "dup.mp3"
        touch_audio(dup_a, size=100)
        touch_audio(dup_b, size=200)

        xml_path = tmp_path / "rekordbox.xml"
        write_xml(xml_path, [])

        result = run_cli([
            "move",
            "--rekordbox-xml", str(xml_path),
            "--scan-root", str(scan_root),
        ])
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        orphans_dir = scan_root / "_Rekordbox_Orphans"
        assert orphans_dir.exists()

        moved_files = [f for f in orphans_dir.iterdir() if f.name != "orphans_manifest.jsonl"]
        moved_names = sorted(f.name for f in moved_files)

        assert len(moved_names) == 2, f"Should have 2 moved files: {moved_names}"
        assert "dup.mp3" in moved_names, f"Original dup.mp3 should exist: {moved_names}"

        collision_pattern = re.compile(r"dup \(\d+\)\.mp3")
        collision_files = [n for n in moved_names if collision_pattern.match(n)]
        assert len(collision_files) == 1, f"Should have one collision-renamed file: {moved_names}"

        manifest = orphans_dir / "orphans_manifest.jsonl"
        lines = [line for line in manifest.read_text().splitlines() if line.strip()]
        assert len(lines) == 2, f"Manifest should have 2 lines: {len(lines)}"

        dst_values = [json.loads(line)["dst"] for line in lines]
        dst_names = [Path(d).name for d in dst_values]
        assert sorted(dst_names) == sorted(moved_names), \
            f"Manifest dst values should match actual files: {dst_names} vs {moved_names}"


class TestFileURIAndPercentDecode:
    """
    Test E: file_uri_and_percent_decode
    Verify handling of file:// URIs with percent-encoded special characters.
    """

    def test_file_uri_and_percent_decode(self, tmp_path: Path) -> None:
        scan_root = tmp_path / "Music"

        special_dir = scan_root / "House & Techno"
        special_dir.mkdir(parents=True)

        special_file = special_dir / "My Track 01.mp3"
        touch_audio(special_file)

        file_uri = path_to_file_uri(special_file)
        assert "%20" in file_uri, f"URI should have encoded spaces: {file_uri}"
        assert "%26" in file_uri, f"URI should have encoded ampersand: {file_uri}"

        xml_path = tmp_path / "rekordbox.xml"
        write_xml(xml_path, [file_uri])

        result = run_cli([
            "preview",
            "--rekordbox-xml", str(xml_path),
            "--scan-root", str(scan_root),
        ])

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        stdout = result.stdout
        assert re.search(r"Orphans.*0", stdout), \
            f"Orphan count should be 0 (file recognized as referenced):\n{stdout}"
        assert "Collection references missing on disk: 0" in stdout, \
            f"Missing on disk should be 0:\n{stdout}"


class TestEdgeCases:
    """Additional edge case tests for robustness."""

    def test_empty_collection(self, tmp_path: Path) -> None:
        """Test with no tracks in XML."""
        scan_root = tmp_path / "Music"
        scan_root.mkdir()

        orphan = scan_root / "orphan.mp3"
        touch_audio(orphan)

        xml_path = tmp_path / "rekordbox.xml"
        write_xml(xml_path, [])

        result = run_cli([
            "preview",
            "--rekordbox-xml", str(xml_path),
            "--scan-root", str(scan_root),
        ])

        assert result.returncode == 0
        assert "Rekordbox collection records (XML): 0" in result.stdout
        assert re.search(r"Orphans.*1", result.stdout)

    def test_no_orphans(self, tmp_path: Path) -> None:
        """Test when all files are referenced."""
        scan_root = tmp_path / "Music"
        scan_root.mkdir()

        referenced = scan_root / "referenced.mp3"
        touch_audio(referenced)

        xml_path = tmp_path / "rekordbox.xml"
        write_xml(xml_path, [str(referenced)])

        result = run_cli([
            "move",
            "--rekordbox-xml", str(xml_path),
            "--scan-root", str(scan_root),
        ])

        assert result.returncode == 0
        assert "no orphans found" in result.stdout.lower()

    def test_nested_directories(self, tmp_path: Path) -> None:
        """Test files in deeply nested directories."""
        scan_root = tmp_path / "Music"

        deep_dir = scan_root / "Genre" / "Artist" / "Album"
        deep_dir.mkdir(parents=True)

        referenced = deep_dir / "track.mp3"
        orphan = deep_dir / "bonus.flac"
        touch_audio(referenced)
        touch_audio(orphan)

        xml_path = tmp_path / "rekordbox.xml"
        write_xml(xml_path, [str(referenced)])

        result = run_cli([
            "move",
            "--rekordbox-xml", str(xml_path),
            "--scan-root", str(scan_root),
        ])

        assert result.returncode == 0
        assert not orphan.exists()

        orphans_dir = scan_root / "_Rekordbox_Orphans"
        assert (orphans_dir / "bonus.flac").exists()

    def test_multiple_scan_roots(self, tmp_path: Path) -> None:
        """Test with multiple scan roots."""
        root1 = tmp_path / "Music1"
        root2 = tmp_path / "Music2"
        root1.mkdir()
        root2.mkdir()

        ref1 = root1 / "track1.mp3"
        ref2 = root2 / "track2.mp3"
        orphan1 = root1 / "orphan1.mp3"
        orphan2 = root2 / "orphan2.mp3"

        for f in [ref1, ref2, orphan1, orphan2]:
            touch_audio(f)

        xml_path = tmp_path / "rekordbox.xml"
        write_xml(xml_path, [str(ref1), str(ref2)])

        result = run_cli([
            "preview",
            "--rekordbox-xml", str(xml_path),
            "--scan-root", str(root1),
            "--scan-root", str(root2),
        ])

        assert result.returncode == 0
        assert "Scanned disk files: 4" in result.stdout
        assert re.search(r"Orphans.*2", result.stdout)

    def test_all_audio_extensions(self, tmp_path: Path) -> None:
        """Test all supported audio extensions are scanned."""
        scan_root = tmp_path / "Music"
        scan_root.mkdir()

        extensions = ["mp3", "wav", "aiff", "aif", "flac", "m4a"]
        files = []
        for ext in extensions:
            f = scan_root / f"track.{ext}"
            touch_audio(f)
            files.append(f)

        unsupported = scan_root / "track.ogg"
        touch_audio(unsupported)

        xml_path = tmp_path / "rekordbox.xml"
        write_xml(xml_path, [])

        result = run_cli([
            "preview",
            "--rekordbox-xml", str(xml_path),
            "--scan-root", str(scan_root),
        ])

        assert result.returncode == 0
        assert f"Scanned disk files: {len(extensions)}" in result.stdout
