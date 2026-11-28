import hashlib
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# SHA-256 of the canonical PolyForm Noncommercial License 1.0.0 body, from the
# first heading through the final definition. The Required Notice above it is
# project-specific and deliberately excluded from this comparison.
OFFICIAL_POLYFORM_BODY_SHA256 = "c0ea4a896d2c8c394b29f9427589996db826cd501c512279ff0ed3ef48fabbe5"
EMAIL_ADDRESS_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})(?![A-Za-z0-9.-])"
)


def published_email_addresses(text: str) -> list[str]:
    """Find email-shaped values without treating URLs or GitHub handles as addresses."""
    without_urls = re.sub(r"https?://\S+", "", text)
    return EMAIL_ADDRESS_PATTERN.findall(without_urls)


def test_root_license_is_the_unmodified_official_text_with_a_verified_notice():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith(
        "Required Notice: Copyright (c) 2026 freeDom- "
        "(the Hnefatafl project owner, https://github.com/freeDom-)\n"
    )
    body = text[text.index("# PolyForm Noncommercial License 1.0.0") :]
    assert hashlib.sha256(body.encode()).hexdigest() == OFFICIAL_POLYFORM_BODY_SHA256
    assert "<https://polyformproject.org/licenses/noncommercial/1.0.0>" in body


def test_licensing_audit_does_not_republish_personal_contact_details():
    audit = (ROOT / "docs/LICENSING.md").read_text(encoding="utf-8")
    assert published_email_addresses(audit) == []
    assert "multiple author identities" in audit
    assert "does not assume" in audit
    assert "owner must confirm" in audit


def test_email_detection_uses_synthetic_addresses_and_ignores_urls_and_handles():
    assert published_email_addresses("Contact person@example.test") == ["person@example.test"]
    assert published_email_addresses("https://example.test/path?user@example.test") == []
    assert published_email_addresses("GitHub handle @freeDom- and release URL") == []


def test_contributor_documentation_keeps_broader_rights_deferred_and_optional():
    contributing = " ".join((ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8").split())
    proposal = " ".join(
        (ROOT / "docs/development/contributor-licensing.md").read_text(encoding="utf-8").split()
    )
    audit = " ".join((ROOT / "docs/LICENSING.md").read_text(encoding="utf-8").split())
    assert "does not currently intend to commercially exploit" in contributing
    assert "Contributors retain copyright" in contributing
    assert "deferred" in contributing.lower()
    assert "No CLA, copyright assignment, DCO enforcement" in proposal
    assert "deferred proposal only" in proposal
    assert "does not currently intend to commercially exploit" in proposal
    assert "ordinary noncommercial contributions do not need" in contributing.lower()
    assert "No CLA, copyright assignment or DCO enforcement is active" in audit
    assert "separately authorized commercial versions" not in contributing


def test_release_readiness_license_decision_is_current_and_still_blocks_deployment():
    readiness = " ".join((ROOT / "docs/releases/readiness.md").read_text(encoding="utf-8").split())
    assert "must still choose a distribution license" not in readiness
    assert "selected distribution license is the PolyForm Noncommercial License 1.0.0" in readiness
    assert "does not itself authorize deployment" in readiness
    assert "Initial public deployment remains unauthorized" in readiness
    assert (
        "Copyright ownership and generated-artwork provenance still require owner confirmation"
        in readiness
    )


def test_python_and_frontend_metadata_use_the_spdx_identifier():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["license"] == "PolyForm-Noncommercial-1.0.0"
    assert "LICENSE" in project["project"]["license-files"]
    assert "THIRD_PARTY_NOTICES.md" in project["project"]["license-files"]

    frontend = (ROOT / "frontend/package.json").read_text(encoding="utf-8")
    assert '"license": "PolyForm-Noncommercial-1.0.0"' in frontend


def test_public_licensing_references_are_consistent_and_keep_third_party_notices():
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").lower().split())
    assert "source-available" in readme
    assert "polyform noncommercial license 1.0.0" in readme
    assert "osi-approved open-source software" in readme
    assert "**not** osi-approved" in readme
    assert "does not offer a commercial licensing program" in readme
    assert "requires separate explicit authorization" in readme
    assert "no authorization is offered or implied through github issues" in readme

    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "react" in notices
    assert "PolyForm Noncommercial License 1.0.0" in notices
    assert "does **not** relicense" in notices

    frontend_notice = (ROOT / "frontend/src/LicenseNotice.tsx").read_text(encoding="utf-8")
    assert 'href="/LICENSE.txt"' in frontend_notice
    assert 'href="/THIRD_PARTY_NOTICES.txt"' in frontend_notice
    assert "Commercial licensing" not in frontend_notice
    assert "COMMERCIAL_LICENSING_URL" not in frontend_notice
