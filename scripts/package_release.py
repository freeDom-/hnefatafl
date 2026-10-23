"""Build the clean-commit asset required by the production release updater."""
import argparse
import gzip
import subprocess
import tempfile
import tomllib
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output-dir", type=Path, default=Path("work/releases"))
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
if subprocess.check_output(["git", "status", "--porcelain"], cwd=root):
    raise SystemExit("Commit and validate all release files before packaging")
tag = "v" + tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
args.output_dir.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as temporary:
    archive = Path(temporary) / "source.tar"
    subprocess.run(["git", "archive", "--format=tar", "--output", str(archive), "HEAD"], cwd=root, check=True)
    target = args.output_dir / f"hnefatafl-{tag}.tar.gz"
    target.write_bytes(gzip.compress(archive.read_bytes(), mtime=0))
print(target)
