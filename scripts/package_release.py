"""Build the clean-commit asset required by the production release updater."""

from __future__ import annotations

import argparse
import gzip
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path

REQUIRED_LICENSE_FILES = ("LICENSE", "THIRD_PARTY_NOTICES.md")


def _git(root: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=root, text=True)


def validate_clean_checkout(root: Path) -> None:
    if _git(root, "status", "--porcelain"):
        raise SystemExit("Commit and validate all release files before packaging")
    tracked = set(_git(root, "ls-files", "--", *REQUIRED_LICENSE_FILES).splitlines())
    if tracked != set(REQUIRED_LICENSE_FILES):
        raise SystemExit("Commit LICENSE and THIRD_PARTY_NOTICES.md before packaging")


def build_release(root: Path, output_dir: Path) -> Path:
    """Package the committed tree and verify the required license members exist."""
    validate_clean_checkout(root)
    tag = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / "source.tar"
        subprocess.run(
            ["git", "archive", "--format=tar", "--output", str(archive), "HEAD"],
            cwd=root,
            check=True,
        )
        with tarfile.open(archive) as source:
            members = {member.name.removeprefix("./") for member in source.getmembers()}
        missing = set(REQUIRED_LICENSE_FILES) - members
        if missing:
            raise SystemExit(
                "Release archive is missing committed license files: " + ", ".join(sorted(missing))
            )
        target = output_dir / f"hnefatafl-{tag}.tar.gz"
        target.write_bytes(gzip.compress(archive.read_bytes(), mtime=0))
    return target


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("work/releases"))
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    print(build_release(root, args.output_dir))


if __name__ == "__main__":
    main()
