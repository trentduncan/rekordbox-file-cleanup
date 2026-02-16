#!/usr/bin/env python3

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import unquote, urlparse
import json
import shutil


DEFAULT_EXTENSIONS = ("mp3", "wav", "aiff", "aif", "flac", "m4a")
DEFAULT_ORPHANS_DIR_NAME = "_Rekordbox_Orphans"
DEFAULT_AUTO_EXCLUDES = (DEFAULT_ORPHANS_DIR_NAME,)
DEFAULT_MANIFEST_NAME = "orphans_manifest.jsonl"

# TODO: At the end update to latest rekordbox version and test the XML parsing
# TODO: Write unit tests
# NOTE: https://github.com/dylanljones/pyrekordbox

@dataclass(frozen=True)
class Config:
    xml_path: Path
    scan_roots: list[Path]
    restore: bool
    check_collection: bool
    dry_run: bool
def parse_command_line_args() -> Config:
    ap = argparse.ArgumentParser(description="Read-only Rekordbox orphan check (by full normalized path).")
    ap.add_argument("--rekordbox-xml", required=True, help="Path to Rekordbox exported collection XML")
    ap.add_argument(
        "--scan-root",
        action="append",
        required=True,
        help="Top-level directory to scan for audio files (repeatable). Example: --scan-root /Users/trent/Music/DJ_MUSIC",
    )
    ap.add_argument(
        "--restore",
        action="store_true",
        help="Restore orphaned files from the manifest"
    )
    ap.add_argument(
    "--check-collection",
    action="store_true",
    help="Verify Rekordbox collection references against disk; optionally also validate a manifest if present.",
)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="When moving/restoring files, only print what would be done without making changes."
    )

    args = ap.parse_args()

    xml_path = Path(args.rekordbox_xml).expanduser()
    if not xml_path.exists():
        raise SystemExit(f"XML not found: {xml_path}")
    
    scan_roots = [Path(p).expanduser() for p in args.scan_root]

    return Config(
        xml_path=xml_path,
        scan_roots=scan_roots,
        restore=args.restore,
        check_collection=args.check_collection,
        dry_run=args.dry_run,
    )

def parse_rekordbox_xml(xml_path: Path) -> set[Path]:
    tree = ET.parse(xml_path)
    root = tree.getroot()

    paths: set[Path] = set()
    for track in root.iter("TRACK"):
        loc = track.attrib.get("Location")
        p = _convert_rekordbox_location_to_path(loc) if loc else None
        if p:
            paths.add(_normalize_path(p))

    return paths

def flatten_raw_files(scan_roots: list[Path]) -> list[Path]:
    exts = {("." + e.lower().lstrip(".")) for e in (DEFAULT_EXTENSIONS)}
    scan_roots_norm = [_normalize_path(r) for r in scan_roots]

    results: list[Path] = []

    for root in scan_roots_norm:
        if not root.exists() or not root.is_dir():
            continue

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if d not in DEFAULT_AUTO_EXCLUDES
            ]

            for name in filenames:
                # Ignore macOS AppleDouble metadata files
                if name.startswith("._"):
                    continue

                path = _normalize_path((Path(dirpath) / name))
                if path.suffix.lower() in exts:
                    results.append(path)
    return results

def flag_orphans(rekordbox_locations_set: set[Path], raw_files: list[Path]) -> list[Path]:
    orphans = [f for f in raw_files if f not in rekordbox_locations_set]
    return orphans

def move_orphans_flat(
    orphans: Iterable[Path],
    *,
    orphans_dir: Path,
    dry_run: bool = True,
) -> dict[str, int]:
    """
    Move orphan files into a flat orphans directory and append a JSONL manifest mapping
    original path -> new path for restore.

    Manifest lines look like:
      {"ts": ..., "src": "...", "dst": "...", "size_bytes": ..., "mtime": ..., "dev": ..., "ino": ...}
    """
    orphans_dir = _normalize_path(orphans_dir)
    orphans_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = orphans_dir / DEFAULT_MANIFEST_NAME

    moved = 0
    skipped_missing = 0
    errors = 0

    with open(manifest_path, "a", encoding="utf-8") as mf:
        for src in orphans:
            src = _normalize_path(src)

            if not src.exists():
                skipped_missing += 1
                continue

            try:
                st = src.stat()
                dev = getattr(st, "st_dev", None)
                ino = getattr(st, "st_ino", None)

                # Flat folder: start with original basename
                dst = orphans_dir / src.name
                dst = _unique_destination_path(dst)

                record = {
                    "ts": int(time.time()),
                    "src": str(src),
                    "dst": str(dst),
                    "size_bytes": int(st.st_size),
                    "mtime": float(st.st_mtime),
                    "dev": int(dev) if dev is not None else None,
                    "ino": int(ino) if ino is not None else None,
                }

                if dry_run:
                    print("[DRY RUN] MOVE", src, "->", dst)
                    continue

                shutil.move(str(src), str(dst))

                # Only record after a successful move
                mf.write(json.dumps(record, ensure_ascii=False) + "\n")
                mf.flush()

                moved += 1

            except Exception as e:
                errors += 1
                print("[ERROR] Failed to move:", src)
                print("        ", repr(e))

    return {"moved": moved, "skipped_missing": skipped_missing, "errors": errors}

def restore_from_manifest(
    *,
    manifest_path: Path,
    dry_run: bool = True,
) -> dict[str, int]:
    """
    Restore files from a JSONL manifest created by move_orphans_flat().
    Moves each record["dst"] back to record["src"].
    """
    manifest_path = _normalize_path(manifest_path)
    if not manifest_path.exists():
        raise SystemExit(f"Manifest not found: {manifest_path}")

    restored = 0
    skipped_missing = 0
    errors = 0

    remaining_records: list[str] = []


    with open(manifest_path, "r", encoding="utf-8") as mf:
        for line in mf:
            line = line.strip()
            if not line:
                continue

            rec = json.loads(line)
            src = _normalize_path(Path(rec["src"]))
            dst = _normalize_path(Path(rec["dst"]))

            if not dst.exists():
                skipped_missing += 1
                print("[SKIP] dst missing:", dst)
                continue

            try:
                src.parent.mkdir(parents=True, exist_ok=True)

                if dry_run:
                    print("[DRY RUN] RESTORE", dst, "->", src)
                    continue

                shutil.move(str(dst), str(src))
                restored += 1

            except Exception as e:
                errors += 1
                print("[ERROR] Failed to restore:", dst)
                print("        ", repr(e))
                remaining_records.append(line)

        if not dry_run:
            tmp_path = manifest_path.with_suffix(".tmp")

            with open(tmp_path, "w", encoding="utf-8") as mf:
                for line in remaining_records:
                    mf.write(line.rstrip() + "\n")

            tmp_path.replace(manifest_path)

            # Optional: if manifest is empty, delete it
            if not remaining_records:
                manifest_path.unlink(missing_ok=True)


    return {"restored": restored, "skipped_missing": skipped_missing, "errors": errors}


def _convert_rekordbox_location_to_path(loc: str) -> Optional[Path]:
    loc = (loc or "").strip()
    if not loc:
        return None

    # Already a normal POSIX path
    if loc.startswith("/"):
        return Path(loc)

    # Rekordbox often stores file URIs
    if loc.lower().startswith("file:"):
        # Handle odd "file:/localhost/..." by normalizing to "file://localhost/..."
        if loc.lower().startswith("file:/") and not loc.lower().startswith("file://"):
            loc = "file://" + loc[len("file:"):]

        parsed = urlparse(loc)
        if parsed.scheme.lower() == "file":
            path_str = unquote(parsed.path or "")
            if path_str:
                return Path(path_str)

    return Path(unquote(loc))


def _normalize_path(p: Path) -> Path:
    # Expand ~ first
    p = p.expanduser()

    # Normalize unicode consistently (macOS commonly uses NFD on disk)
    s = unicodedata.normalize("NFD", str(p))
    p = Path(s)

    # Canonicalize absolute/symlinks best-effort
    return p.resolve(strict=False)


def _unique_destination_path(dest: Path) -> Path:
    """
    If dest exists, add " (1)", " (2)" etc before suffix.
    """
    if not dest.exists():
        return dest

    stem = dest.stem
    suffix = dest.suffix
    parent = dest.parent

    i = 1
    while True:
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def find_manifest_entries_that_break_collection(
    *,
    referenced: set[Path],
    manifest_path: Path,
) -> list[tuple[Path, Path]]:
    bad: list[tuple[Path, Path]] = []
    manifest_path = _normalize_path(manifest_path)

    with open(manifest_path, "r", encoding="utf-8") as mf:
        for line in mf:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            src = _normalize_path(Path(rec["src"]))
            dst = _normalize_path(Path(rec["dst"]))

            # if src was referenced and now missing, this entry likely caused it
            if src in referenced and not src.exists() and dst.exists():
                bad.append((src, dst))
    return bad


def main() -> int:
    config = parse_command_line_args()
    xml_path = config.xml_path
    scan_roots = config.scan_roots
    restore = config.restore
    check_collection = config.check_collection
    dry_run = config.dry_run
    manifest_path = _normalize_path(scan_roots[0]) / DEFAULT_ORPHANS_DIR_NAME / DEFAULT_MANIFEST_NAME

    if restore:
        stats = restore_from_manifest(manifest_path=manifest_path, dry_run=dry_run)
        print("Restore stats:", stats)
        return 0    

    orphans_dir = _normalize_path(scan_roots[0]) / DEFAULT_ORPHANS_DIR_NAME

    rekordbox_locations_set = parse_rekordbox_xml(xml_path)
    if check_collection:
        missing_on_disk = [p for p in rekordbox_locations_set if not p.exists()]
        print("Collection references missing on disk:", len(missing_on_disk))
        for p in missing_on_disk[:25]:
            print("  MISSING:", p)

        bad = []
        if manifest_path.exists():
            bad = find_manifest_entries_that_break_collection(referenced=missing_on_disk, manifest_path=manifest_path)

        print("Manifest entries that appear to break collection:", len(bad))
        for src, dst in bad[:25]:
            print("  MOVED REFERENCED:", src)
            print("    ->", dst)
        return 0

    raw_files = flatten_raw_files(scan_roots)

    orphans = flag_orphans(rekordbox_locations_set, raw_files)
    orphans = orphans

    print("Orphaned files found:", len(orphans))

    stats = move_orphans_flat(
        orphans,
        orphans_dir=orphans_dir,
        dry_run=dry_run,
    )
    print("Move stats:", stats)



if __name__ == "__main__":
    raise SystemExit(main())
