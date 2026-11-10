"""Package only reproducible submission files and validate required ZIP entries."""

import hashlib
from pathlib import Path
import zipfile


def main():
    """Exclude local caches and databases; keep the required top-level folder."""
    root = Path(__file__).resolve().parents[2]
    destination = root.parent / f"{root.name}.zip"
    excluded = {"__pycache__", ".venv", "build", ".ruff_cache"}
    entries = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(
            part in excluded or part.endswith(".egg-info") for part in relative.parts
        ):
            continue
        if path.is_file() and path.suffix not in {
            ".pyc",
            ".db",
            ".aux",
            ".log",
            ".out",
            ".toc",
        }:
            entries.append(path)
    if not (root / "实验报告.pdf").is_file():
        raise SystemExit("Missing report PDF")
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in entries:
            archive.write(path, str(Path(root.name) / path.relative_to(root)))
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise SystemExit("ZIP integrity failure")
        names = archive.namelist()
        for suffix in (
            "/实验报告.pdf",
            "/UML/UML类图.puml",
            "/UML/UML类图.png",
            "/code/pyproject.toml",
            "/code/src/market/gui.py",
        ):
            if not any(name.endswith(suffix) for name in names):
                raise SystemExit(f"Missing required file: {suffix}")
    print(f"ZIP: {destination}\nFiles: {len(entries)}")
    print(f"SHA256: {hashlib.sha256(destination.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
