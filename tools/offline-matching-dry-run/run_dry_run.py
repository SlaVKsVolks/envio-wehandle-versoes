"""Run with an explicitly selected Python: python -I -B run_dry_run.py --help."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from document_matching.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
