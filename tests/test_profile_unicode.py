"""A shared multilingual/control corpus exercises both frontend and API policy."""

import json
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from hnefatafl.profile_api import ProfileUpdate
from test_profile import app as app, template as template, login

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
