# Rekordbox Orphan File Cleaner

A safe, reversible tool to clean up unused audio files from your DJ library.

## ⚠️ WARNING — Experimental

This script performs file system operations (moving files on disk).

While it has worked correctly on my own library, it is still experimental.

**Use at your own risk.**

If you are not comfortable working with file systems, command line tools, and backups, wait until the tool has been more widely tested.

**Before using:**

- Back up your music library.
- Test using `preview` and `--dry-run` first.

## What Problem Does This Solve?

Most DJs use Rekordbox to manage their music collections.

When you remove a song from Rekordbox using:

> "Remove from Collection"

Rekordbox does **NOT** delete the underlying audio file.

Over time this leads to:

- Thousands of unused files on disk
- Massive storage waste (30–60MB per track adds up)
- A messy music directory
- No reliable way to know what is safe to delete

**This tool solves that problem.**

It identifies:

> "Files that exist on disk but are no longer referenced by Rekordbox."

These files are called **orphans**.

## What This Tool Does

- Reads a Rekordbox XML export
- Scans your music directory
- Compares the two
- Identifies orphaned files
- Moves them into a quarantine folder
- Writes a restore manifest
- Allows full recovery if needed

**It does NOT:**

- Modify the Rekordbox database
- Delete files permanently
- Deduplicate by metadata
- Reorganize folders

It only answers:

> Which audio files exist on disk but are not referenced in Rekordbox?

## Installation

### 1. Clone the repository

```bash
git clone <repo-url>
cd rekordbox-file-management
```

### 2. (Optional but Recommended) Create a Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dev Dependencies (for tests)

```bash
pip install -e .[dev]
```

You do not need any external runtime dependencies to use the CLI.

## How To Use (For DJs)

### Step 1 — Export Rekordbox XML

In Rekordbox:

> File → Export Collection in XML Format

Save the file somewhere accessible.

⚠️ You must re-export the XML any time you modify your collection.

### Step 2 — Identify Your Scan Root

This is the top-level folder containing your music files.

Example:

```
/Volumes/Samsung_T5/Music
```

### Step 3 — Preview (Safe)

Always start with preview:

```bash
python file-cleanup.py preview \
  --rekordbox-xml /path/to/rekordbox.xml \
  --scan-root /path/to/Music
```

This shows:

- Number of orphan files
- Files missing from disk
- Collection statistics
- Manifest warnings (if applicable)

**No files are moved.**

### Step 4 — Dry Run Move

```bash
python file-cleanup.py move --dry-run \
  --rekordbox-xml /path/to/rekordbox.xml \
  --scan-root /path/to/Music
```

Shows what would be moved, but makes no changes. If everything looks correct then proceed to Step 5

### Step 5 — Move Orphans

```bash
python file-cleanup.py move \
  --rekordbox-xml /path/to/rekordbox.xml \
  --scan-root /path/to/Music
```

Orphaned files are moved to:

```
<scan-root>/_Rekordbox_Orphans/
```

A manifest file is written:

```
orphans_manifest.jsonl
```

### Step 6 — Verify everything is working

Run preview one more time to verify the number of missing files from rekordbox hasn't gone up
```bash
python file-cleanup.py preview \
  --rekordbox-xml /path/to/rekordbox.xml \
  --scan-root /path/to/Music
```

### Step 7 — Restore (If Needed)

```bash
python file-cleanup.py restore \
  --rekordbox-xml /path/to/rekordbox.xml \
  --scan-root /path/to/Music
```

Files are moved back to their original locations.

### Step 8 — Delete the files

Assuming everything is verified to be correct, and your Rekordbox Library is unaffected. Then you can go ahead and delete the `_Rekordbox_Orphans` directory.
Congrats your unused music is no longer taking up space on your hard drive 🤝

## How It Works (Technical Overview)

```
              Rekordbox XML Export
                     │
                     ▼
        Parse <TRACK Location="...">
                     │
                     ▼
     Normalize + Percent Decode + URI Parse
                     │
                     ▼
            referenced_paths (set[Path])
                     │
                     │
            Scan Disk Under scan_roots
                     │
                     ▼
            scanned_paths (set[Path])
                     │
                     ▼
            Reconciliation Step
                     │
         ┌───────────┼───────────┐
         ▼           ▼           ▼
     Orphans       Missing    In-Sync
  (scanned - ref) (ref - scanned)
                     │
                     ▼
             move_orphans_flat()
                     │
                     ▼
    _Rekordbox_Orphans + JSONL Manifest
                     │
                     ▼
             restore_from_manifest()
```

## Safety Mechanisms

- Uses absolute normalized paths
- Unicode normalization (NFD for macOS compatibility)
- Percent-decoding for file:// URIs
- Collision-safe flat moves (`file.mp3`, `file (1).mp3`)
- Manifest-based reversible quarantine
- Dry-run support
- Integration-tested via subprocess

## Testing

Tests are subprocess-based integration tests.

Run:

```bash
pip install -e .[dev]
pytest
```

The test suite covers:

- Preview counts
- Dry-run behavior
- Move + manifest creation
- Restore round-trip
- Filename collisions
- `file://localhost` URI decoding
- Nested directories
- Multiple scan roots
- Extension filtering
- Ignored macOS metadata files

## Future Improvements

- Direct Rekordbox database parsing (no XML export needed)
- Command to remove broken Rekordbox collection entries
- Inode-based comparison for even stronger guarantees
- Configurable orphan directory
- Windows support validation
- Optional permanent deletion mode (with extreme caution)
