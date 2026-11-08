"""A shared multilingual/control corpus exercises both frontend and API policy."""

import json
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from hnefatafl.profile_api import ProfileUpdate
from hnefatafl.account_policy import validate_password
from hnefatafl.profile_api import PasswordChange
from hnefatafl.auth import verify
from test_profile import app as app, template as template, login
from test_profile import PASSWORD

CORPUS = json.loads((Path(__file__).parent / "fixtures/profile_text.json").read_text())


@pytest.mark.parametrize("field", ["display_name", "city"])
@pytest.mark.parametrize("case", CORPUS["valid"])
def test_profile_text_preserves_international_names_and_joined_emoji(field, case):
    assert getattr(ProfileUpdate(**{field: case["input"]}), field) == case["normalized"]


@pytest.mark.parametrize("field", ["display_name", "city"])
@pytest.mark.parametrize("code_point", CORPUS["rejected_code_points"])
def test_profile_text_rejects_control_and_bidi_formatting(field, code_point):
    with pytest.raises(ValidationError):
        ProfileUpdate(**{field: "Name" + chr(code_point) + "City"})


@pytest.mark.parametrize("field", ["display_name", "city"])
def test_profile_api_safely_rejects_unsafe_text_without_committing_other_fields(app, field):
    with TestClient(app) as client:
        account = login(client)
        before = app.state.auth.account(account["id"])
        unsafe = "unsafe-identity-" + chr(0x202E) + "spoof"
        response = client.patch("/api/profile", json={field: unsafe, "country_code": "DE"})
        assert response.status_code == 422
        assert "unsafe-identity" not in response.text and "spoof" not in response.text
        assert app.state.auth.account(account["id"]) == before


@pytest.mark.parametrize("field,limit", [("display_name", 80), ("city", 100)])
@pytest.mark.parametrize("unit", ["京", "😀", "e\u0301", "👩\u200d🚀"])
def test_profile_api_counts_normalized_code_points_without_truncating(app, field, limit, unit):
    import unicodedata

    normalized_unit = unicodedata.normalize("NFC", unit)
    count = limit // len(normalized_unit)
    raw = unit * count + "x" * (limit % len(normalized_unit))
    expected = unicodedata.normalize("NFC", raw)
    assert len(expected) == limit
    with TestClient(app) as client:
        login(client)
        response = client.patch("/api/profile", json={field: raw})
        assert response.status_code == 200
        assert response.json()[field] == expected
        rejected = client.patch("/api/profile", json={field: raw + "x"})
        assert rejected.status_code == 422
        assert client.get("/api/profile").json()[field] == expected


@pytest.mark.parametrize("password", ["😀😁😂😃" * 64, "e\u0301😀🔑" * 64])
def test_unicode_password_boundaries_and_authentication_preserve_original_text(app, password):
    import unicodedata

    assert len(password) == 256
    validate_password(password)
    with pytest.raises(ValueError):
        validate_password(password + "x")
    with pytest.raises(ValueError):
        validate_password("😀😁😂😃😁😂")
    PasswordChange(current_password=password, new_password=password, confirm_password=password)
    with pytest.raises(ValidationError):
        PasswordChange(
            current_password=password + "x", new_password=password, confirm_password=password
        )
    with TestClient(app) as client:
        account = login(client)
        result = client.post(
            "/api/profile/password",
            json={
                "current_password": PASSWORD,
                "new_password": password,
                "confirm_password": password,
            },
        )
        assert result.status_code == 200
        encoded = app.state.auth.account(account["id"])["password_hash"]
        assert verify(password, encoded)
        if unicodedata.normalize("NFC", password) != password:
            assert not verify(unicodedata.normalize("NFC", password), encoded)
        assert (
            client.post(
                "/api/auth/login", json={"identity": "Raven", "password": password}
            ).status_code
            == 200
        )
