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

MOVE_COMMAND = "move"
PREVIEW_COMMAND = "preview"
RESTORE_COMMAND = "restore"

DEFAULT_EXTENSIONS = ("mp3", "wav", "aiff", "aif", "flac", "m4a")
DEFAULT_ORPHANS_DIR_NAME = "_Rekordbox_Orphans"
DEFAULT_EXCLUDED_DIRNAMES = (DEFAULT_ORPHANS_DIR_NAME,)
DEFAULT_MANIFEST_NAME = "orphans_manifest.jsonl"

# TODO: At the end update to latest rekordbox version and test the XML parsing
# NOTE: https://github.com/dylanljones/pyrekordbox

@dataclass(frozen=True)
class Config:
    cmd: str
    xml_path: Path
    scan_roots: list[Path]
    dry_run: bool
    sample: int
    orphans_dir: Path
    manifest_path: Path

def parse_command_line_args() -> Config:
    ap = argparse.ArgumentParser(description="Rekordbox orphan file cleaner (XML vs disk paths)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_move = sub.add_parser(MOVE_COMMAND, help="Move orphaned files into _Rekordbox_Orphans")
    _add_common_args(p_move)
    p_move.add_argument("--dry-run", action="store_true", help="Print move operations without changing anything")

    p_preview = sub.add_parser(PREVIEW_COMMAND, help="Preview orphans + validate collection + manifest tripwires (no changes)")
    _add_common_args(p_preview)

    p_restore = sub.add_parser(RESTORE_COMMAND, help="Restore files from manifest (moves files back to original locations)")
    _add_common_args(p_restore)
    p_restore.add_argument("--dry-run", action="store_true", help="Print restore operations without changing anything")

    args = ap.parse_args()

    xml_path = Path(args.rekordbox_xml).expanduser()
    if not xml_path.exists():
        raise SystemExit(f"XML not found: {xml_path}")
    
    scan_roots = [Path(p).expanduser() for p in args.scan_root]
    for r in scan_roots:
        if not r.exists() or not r.is_dir():
            raise SystemExit(f"Scan root not a directory: {r}")


    # TODO: support custom orphans dir and manifest path via CLI args if needed
    base = _normalize_path(scan_roots[0])
    orphans_dir = base / DEFAULT_ORPHANS_DIR_NAME
    manifest_path = base / DEFAULT_ORPHANS_DIR_NAME / DEFAULT_MANIFEST_NAME

    return Config(
        cmd=args.cmd,
        xml_path=xml_path,
        scan_roots=scan_roots,
        dry_run=getattr(args, "dry_run", False),
        sample=args.sample,
        orphans_dir=orphans_dir,
        manifest_path=manifest_path,
    )

def scan_rekordbox_xml(xml_path: Path) -> set[Path]:
    tree = ET.parse(xml_path)
    root = tree.getroot()

    paths: set[Path] = set()
    for track in root.iter("TRACK"):
        loc = track.attrib.get("Location")
        p = _convert_rekordbox_location_to_path(loc) if loc else None
        if p:
            paths.add(_normalize_path(p))

    return paths

def scan_disk_files(scan_roots: list[Path]) -> set[Path]:
    '''
    Scan the given directories for audio files.
    Returns a set of normalized absolute Paths for files with the specified extensions.
    '''
    exts = {("." + e.lower().lstrip(".")) for e in (DEFAULT_EXTENSIONS)}
    scan_roots_norm = [_normalize_path(r) for r in scan_roots]

    results: set[Path] = set()

    for root in scan_roots_norm:
        if not root.exists() or not root.is_dir():
            continue

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if d not in DEFAULT_EXCLUDED_DIRNAMES
            ]

            for name in filenames:
                # Ignore macOS AppleDouble metadata files
                if _should_ignore_filename(name):
                    continue

                path = _normalize_path((Path(dirpath) / name))
                if path.suffix.lower() in exts:
                    results.add(path)
    return results

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
    manifest_path = _normalize_path(manifest_path)
    if not manifest_path.exists():
        raise SystemExit(f"Nothing found in the orphans manifest to restore")

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

            #  Anything skipped here can be cleared out of the manifest since it can't be restored
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

def find_broken_moves_from_manifest(
    *,
    referenced: set[Path],
    manifest_path: Path,
) -> list[tuple[Path, Path]]:
    bad: list[tuple[Path, Path]] = []
    manifest_path = _normalize_path(manifest_path)

    if manifest_path.exists():
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

@dataclass(frozen=True)
class ReconciledMetadata:
    orphans: list[Path]
    missing: list[Path]
    missing_on_disk: list[Path]
    exists_not_scanned: list[Path]

def reconcile(referenced: set[Path], scanned: set[Path]) -> ReconciledMetadata:
    orphans = sorted(scanned - referenced, key=str)
    missing = sorted(referenced - scanned, key=str)
    missing_on_disk = [p for p in missing if not p.exists()]
    exists_not_scanned = [p for p in missing if p.exists()]
    return ReconciledMetadata(orphans, missing, missing_on_disk, exists_not_scanned)

def print_paths_sample(paths: Iterable[Path], label: str, sample: int = 25) -> None:
    print(f"{label} ({len(paths)}):")
    for p in sorted(paths, key=str)[:sample]:
        print(f"  ", p)

def log_preview(reconciled_meta: ReconciledMetadata, config: Config, referenced_paths: set[Path], scanned_paths: set[Path]) -> None:
    print("=== Preview ===")
    print("Orphans (disk files that are not in rekordbox collection that can be removed):", len(reconciled_meta.orphans))
    if reconciled_meta.orphans:
        print_paths_sample(reconciled_meta.orphans, "\nOrphans", sample=config.sample)

    print("\n=== Additional Information ===")
    print("Rekordbox collection records (XML):", len(referenced_paths))
    print("Scanned disk files:", len(scanned_paths))
    print("Rekordbox collection records not found in the provided scan_roots:", len(reconciled_meta.missing))
    print("  Collection references missing on disk:", len(reconciled_meta.missing_on_disk))
    print("  Exists but not scanned:", len(reconciled_meta.exists_not_scanned))
    print("Orphans dir:", config.orphans_dir)
    print("Manifest:", config.manifest_path)

    if reconciled_meta.missing_on_disk:
        print_paths_sample(reconciled_meta.missing_on_disk, "\nReferenced missing on disk", sample=config.sample)

def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--rekordbox-xml", required=True, help="Path to Rekordbox exported collection XML")
    p.add_argument(
        "--scan-root",
        action="append",
        required=True,
        help="Top-level directory to scan for audio files (repeatable). Example: --scan-root /Users/trent/Music/DJ_MUSIC",
    )
    p.add_argument("--sample", type=int, default=25, help="How many example paths to print in preview (default: 25)")

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

def _should_ignore_filename(name: str) -> bool:
    return name.startswith("._") or name == ".DS_Store"

def main() -> int:
    config = parse_command_line_args()

    if config.cmd == RESTORE_COMMAND:
        stats = restore_from_manifest(manifest_path=config.manifest_path, dry_run=config.dry_run)
        print("Restore stats:", stats)
        return 0    

    referenced_paths = scan_rekordbox_xml(config.xml_path)
    scanned_paths = scan_disk_files(config.scan_roots)

    reconciled_meta = reconcile(referenced_paths, scanned_paths)

    if config.cmd == PREVIEW_COMMAND:
        log_preview(reconciled_meta, config, referenced_paths, scanned_paths)
        bad = find_broken_moves_from_manifest(referenced=referenced_paths, manifest_path=config.manifest_path)
        if bad:
            print_paths_sample([f"  MOVED REFERENCED: {src}\n    -> {dst}" for src, dst in bad], "\nManifest entries that appear to break collection", sample=config.sample)
        return 0


    if config.cmd == MOVE_COMMAND:
        if not reconciled_meta.orphans:
                print("Great news: no orphans found! Your collection and disk are perfectly in sync. :)")
                return 0
        
        print("Orphaned files found:", len(reconciled_meta.orphans))
        stats = move_orphans_flat(
            reconciled_meta.orphans,
            orphans_dir=config.orphans_dir,
            dry_run=config.dry_run,
        )
        print("Move stats:", stats)
        bad = find_broken_moves_from_manifest(referenced=referenced_paths, manifest_path=config.manifest_path)
        if bad:
            print("\nWARNING: Found files that have been moved but are still referenced in the collection. This can cause broken links in rekordbox. Consider restoring these files from the manifest or manually moving them back to their original locations. If these are songs you care about, you may want to restore them instead of deleting. If they are truly unwanted, you can safely delete the moved files and then remove the corresponding entries from the manifest.")
            print_paths_sample([f"  MOVED REFERENCED: {src}\n    -> {dst}" for src, dst in bad], "\nManifest entries that appear to break collection", sample=config.sample)
        return 0
    
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
