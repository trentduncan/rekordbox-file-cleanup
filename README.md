# Rekordbox File Management

CLI tool for reconciling Rekordbox XML collections with disk files. Identifies orphaned audio files (on disk but not in Rekordbox) and missing files (in Rekordbox but not on disk).

## Commands

- **preview** - Show orphan counts and collection statistics without making changes
- **move** - Move orphaned files to `_Rekordbox_Orphans/` directory with JSONL manifest
- **restore** - Restore previously moved files back to original locations

## Usage

```bash
# Preview orphans and statistics
python file-cleanup.py preview --rekordbox-xml ~/Music/rekordbox.xml --scan-root ~/Music/DJ_MUSIC

# Move orphans (with dry-run first)
python file-cleanup.py move --dry-run --rekordbox-xml ~/Music/rekordbox.xml --scan-root ~/Music/DJ_MUSIC
python file-cleanup.py move --rekordbox-xml ~/Music/rekordbox.xml --scan-root ~/Music/DJ_MUSIC

# Restore moved files
python file-cleanup.py restore --rekordbox-xml ~/Music/rekordbox.xml --scan-root ~/Music/DJ_MUSIC
```

## Development

### Install dev dependencies

```bash
pip install -e .[dev]
```

### Run tests

```bash
pytest
```

Or with coverage:

```bash
pytest --cov=. --cov-report=term-missing
```

### Test structure

Tests are subprocess-based integration tests located in `tests/test_integration.py`. Each test:

- Invokes the CLI via `subprocess.run()` (not by importing `main()`)
- Uses pytest's `tmp_path` fixture for complete filesystem isolation
- Creates minimal Rekordbox XML files and real audio files in temporary directories
- Asserts on exit codes, stdout content, filesystem state, and manifest contents

This approach tests the tool exactly as a user would run it from the command line.

---

Other tool to build is to look at hot cues of everything in a playlist and approximate the length of the set
