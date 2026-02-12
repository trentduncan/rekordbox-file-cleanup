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


DEFAULT_EXTENSIONS = ("mp3", "wav", "aiff", "aif", "flac", "m4a")
DEFAULT_AUTO_EXCLUDES = ("_Rekordbox_Orphans",)

# Parse cmd line Args x
# Parse xml rekordbox collection x
# loop through collection normalize the path and set it into a data structure for easy lookups (set or map) x
# Recursively parse through all directories, to flatten the list of files, normalize the urls x
# loop through the raw files and compare them to what exists in the rekordbox_collection_keyed_by_url output orphaned files x
# Loop through files and move all orphaned files into orphans directory, when moving to the orphaned directory, output a file of the path of the file before moving it to allow for a restore functionality function
# write restore method

# TODO: At the end update to latest rekordbox version and test the XML parsing
# TODO: Write unit tests
# NOTE: https://github.com/dylanljones/pyrekordbox

@dataclass(frozen=True)
class Config:
    xml_path: Path
    scan_roots: list[Path]

def parse_command_line_args() -> Config:
    ap = argparse.ArgumentParser(description="Read-only Rekordbox orphan check (by full normalized path).")
    ap.add_argument("--rekordbox-xml", required=True, help="Path to Rekordbox exported collection XML")
    ap.add_argument(
        "--scan-root",
        action="append",
        required=True,
        help="Top-level directory to scan for audio files (repeatable). Example: --scan-root /Users/trent/Music/DJ_MUSIC",
    )

    args = ap.parse_args()

    xml_path = Path(args.rekordbox_xml).expanduser()
    if not xml_path.exists():
        raise SystemExit(f"XML not found: {xml_path}")
    
    scan_roots = [Path(p).expanduser() for p in args.scan_root]

    return Config(
        xml_path=xml_path,
        scan_roots=scan_roots,
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

        for dirpath, _, filenames in os.walk(root):
            for name in filenames:
                path = _normalize_path((Path(dirpath) / name))
                if path.suffix.lower() in exts:
                    results.append(path)
    return results

def flag_orphans(rekordbox_locations_set: set[Path], raw_files: list[Path]) -> list[Path]:
    orphans = [f for f in raw_files if f not in rekordbox_locations_set]
    return orphans

def move_orphans(orphans: list[Path], target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for orphan in orphans:
        target_path = target_dir / orphan.name
        orphan.rename(target_path)

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


def main() -> int:
    config = parse_command_line_args()
    xml_path = config.xml_path
    scan_roots = config.scan_roots

    rekordbox_locations_set = parse_rekordbox_xml(xml_path)
    raw_files = flatten_raw_files(scan_roots)

    orphans = flag_orphans(rekordbox_locations_set, raw_files)
    orphans = orphans[:5]




    
    # raw_set = set(raw_files)
    # missing = sorted(rekordbox_locations_set - raw_set, key=str)

    # missing_on_disk = [p for p in missing if not p.exists()]
    # exists_not_scanned = [p for p in missing if p.exists()]

    # print("Referenced not in raw (total):", len(missing))
    # print("  Missing on disk:", len(missing_on_disk))
    # print("  Exists but not scanned:", len(exists_not_scanned))

    # print("\nFirst 10 missing on disk:")
    # for p in missing_on_disk[:10]:
    #     print("  ", p)

    # print("\nFirst 10 exists but not scanned:")
    # for p in exists_not_scanned[:10]:
    #     print("  ", p)






if __name__ == "__main__":
    raise SystemExit(main())
