"""Build the flat HACS release archive."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import zipfile


ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "orvibo_lan"
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def build_release(destination: Path) -> None:
    """Write all integration files at the ZIP root."""
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(INTEGRATION.rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(INTEGRATION)
            if "__pycache__" in relative.parts or source.suffix in EXCLUDED_SUFFIXES:
                continue
            archive.write(source, PurePosixPath(*relative.parts).as_posix())
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError(f"release archive was not created: {destination}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="Output ZIP path")
    args = parser.parse_args()
    build_release(args.destination)


if __name__ == "__main__":
    main()
