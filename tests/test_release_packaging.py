import gzip
import importlib.util
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "package_release", ROOT / "scripts/package_release.py"
)
package_release = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(package_release)


def git(repository, *arguments):
    subprocess.run(["git", *arguments], cwd=repository, check=True, capture_output=True)


def make_repository(path, *, include_notices=True):
    path.mkdir()
    (path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n')
    (path / "LICENSE").write_text("PolyForm Noncommercial License 1.0.0\n")
    if include_notices:
        (path / "THIRD_PARTY_NOTICES.md").write_text("third-party notices\n")
    (path / ".gitignore").write_text("frontend/public/THIRD_PARTY_LICENSES.txt\n")
    (path / "README.md").write_text("source\n")
    git(path, "init", "-q")
    git(path, "config", "user.email", "test@example.test")
    git(path, "config", "user.name", "Release Test")
    git(path, "add", ".")
    git(path, "commit", "-qm", "fixture")
    return path


def archive_members(path):
    with tarfile.open(path) as archive:
        return {member.name.removeprefix("./") for member in archive.getmembers()}


def test_release_archive_includes_committed_licenses_but_not_generated_frontend_outputs(tmp_path):
    repository = make_repository(tmp_path / "repository")
    generated = repository / "frontend/public/THIRD_PARTY_LICENSES.txt"
    generated.parent.mkdir(parents=True)
    generated.write_text("generated at build time\n")
    assert (
        subprocess.check_output(["git", "status", "--porcelain"], cwd=repository, text=True).strip()
        == ""
    )

    target = package_release.build_release(repository, tmp_path / "release")
    assert target.name == "hnefatafl-1.2.3.tar.gz"
    assert gzip.decompress(target.read_bytes())
    members = archive_members(target)
    assert {"LICENSE", "THIRD_PARTY_NOTICES.md"} <= members
    assert "frontend/public/THIRD_PARTY_LICENSES.txt" not in members
    assert not any(member.startswith("frontend/dist/") for member in members)


def test_release_packaging_refuses_a_missing_committed_notice(tmp_path):
    repository = make_repository(tmp_path / "repository", include_notices=False)
    with pytest.raises(SystemExit, match="Commit LICENSE and THIRD_PARTY_NOTICES.md"):
        package_release.build_release(repository, tmp_path / "release")


def test_release_packaging_refuses_a_dirty_checkout(tmp_path):
    repository = make_repository(tmp_path / "repository")
    (repository / "uncommitted.txt").write_text("dirty\n")
    with pytest.raises(SystemExit, match="Commit and validate"):
        package_release.build_release(repository, tmp_path / "release")
