from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPPORT_URL = "https://buymeacoffee.com/free.dom"


def test_github_funding_points_to_the_official_buy_me_a_coffee_account():
    funding = (ROOT / ".github/FUNDING.yml").read_text(encoding="utf-8")
    assert funding.strip() == "buy_me_a_coffee: free.dom"


def test_readme_and_frontend_use_the_same_support_destination():
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert SUPPORT_URL in readme
    assert "completely voluntary" in readme
    assert "not a software license purchase" in readme
    assert "All gameplay and AI features remain free" in readme
    assert "unlock no features" in readme
    assert "non-donors have identical access" in readme
    assert "tax-deductible" in readme

    support = (ROOT / "frontend/src/supportLinks.ts").read_text(encoding="utf-8")
    assert f"'{SUPPORT_URL}'" in support


def test_commercial_licensing_link_and_issue_tracker_promotion_are_removed():
    component = (ROOT / "frontend/src/LicenseNotice.tsx").read_text(encoding="utf-8")
    assert "Commercial licensing" not in component
    assert "COMMERCIAL_LICENSING_URL" not in component
    assert "/LICENSE.txt" in component
    assert "SupportDevelopmentLink" in component

    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "commercial licensing inquiry" not in readme.lower()
    assert "requires separate explicit authorization" in readme
    assert "no authorization is offered or implied through GitHub Issues" in readme

    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"Commercial licensing"' not in project


def test_support_link_is_external_secure_and_the_policy_covers_donor_neutrality():
    component = (ROOT / "frontend/src/SupportDevelopmentLink.tsx").read_text(encoding="utf-8")
    assert "BUY_ME_A_COFFEE_URL" in component
    assert 'target="_blank"' in component
    assert 'rel="noopener noreferrer"' in component
    assert "aria-label" in component

    policy = (ROOT / "docs/DONATIONS.md").read_text(encoding="utf-8")
    for statement in (
        "Donations are voluntary",
        "not software license purchases",
        "remains free regardless of donations",
        "All gameplay and AI features remain free",
        "unlock no features or privileges",
        "no gameplay, ranking, matchmaking or training advantages",
        "identical access",
        "tax-deductible",
        "no embedded widget",
        "no backend donation endpoint",
    ):
        assert statement in policy
