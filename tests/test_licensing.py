import hashlib
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# SHA-256 of the canonical PolyForm Noncommercial License 1.0.0 body, from the
# first heading through the final definition. The Required Notice above it is
# project-specific and deliberately excluded from this comparison.
OFFICIAL_POLYFORM_BODY_SHA256 = "c0ea4a896d2c8c394b29f9427589996db826cd501c512279ff0ed3ef48fabbe5"


def test_root_license_is_the_unmodified_official_text_with_a_verified_notice():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith(
        "Required Notice: Copyright (c) 2026 freeDom- "
        "(the Hnefatafl project owner, https://github.com/freeDom-)\n"
    )
    body = text[text.index("# PolyForm Noncommercial License 1.0.0") :]
    assert hashlib.sha256(body.encode()).hexdigest() == OFFICIAL_POLYFORM_BODY_SHA256
    assert "<https://polyformproject.org/licenses/noncommercial/1.0.0>" in body


def test_python_and_frontend_metadata_use_the_spdx_identifier():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["license"] == "PolyForm-Noncommercial-1.0.0"
    assert "LICENSE" in project["project"]["license-files"]
    assert "THIRD_PARTY_NOTICES.md" in project["project"]["license-files"]

    frontend = (ROOT / "frontend/package.json").read_text(encoding="utf-8")
    assert '"license": "PolyForm-Noncommercial-1.0.0"' in frontend


def test_public_licensing_references_are_consistent_and_keep_third_party_notices():
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert "source-available" in readme
    assert "polyform noncommercial license 1.0.0" in readme
    assert "osi-approved open-source software" in readme
    assert "**not** osi-approved" in readme
    assert "commercial licensing" in readme
    assert "github.com/freedom-/hnefatafl/issues" in readme

    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "react" in notices
    assert "PolyForm Noncommercial License 1.0.0" in notices
    assert "does **not** relicense" in notices

    frontend_notice = (ROOT / "frontend/src/LicenseNotice.tsx").read_text(encoding="utf-8")
    assert 'href="/LICENSE.txt"' in frontend_notice
    assert 'href="/THIRD_PARTY_NOTICES.txt"' in frontend_notice
    assert "https://github.com/freeDom-/hnefatafl/issues" in frontend_notice
