"""Exact completed years use private full dates and one UTC date per response."""

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from hnefatafl import account_policy
from hnefatafl.account_policy import birth_date, public_profile_fields
from test_profile import app as app, template as template, login


def details(value, today):
    return public_profile_fields(
        {
            "date_of_birth": value,
            "profile_private_fields": "[]",
            "country_code": None,
            "city": None,
        },
        today,
    )


@pytest.mark.parametrize(
    "born,today,expected",
    [
        ("1990-03-15", "2026-03-14", 35),
        ("1990-03-15", "2026-03-15", 36),
        ("1990-03-15", "2026-03-16", 36),
        ("1990-12-31", "2026-12-30", 35),
        ("1990-12-31", "2026-12-31", 36),
        ("1990-12-31", "2027-01-01", 36),
        ("1990-01-01", "2026-12-31", 36),
        ("1990-01-01", "2027-01-01", 37),
        ("2000-02-29", "2023-02-28", 22),
        ("2000-02-29", "2023-03-01", 23),
        ("2000-02-29", "2024-02-28", 23),
        ("2000-02-29", "2024-02-29", 24),
        ("2000-02-29", "2100-02-28", 99),
        ("2000-02-29", "2100-03-01", 100),
        ("1900-01-01", "2026-11-08", 126),
        ("2026-11-08", "2026-11-08", 0),
    ],
)
def test_completed_years_and_march_first_convention(born, today, expected):
    value = details(born, date.fromisoformat(today))
    assert value == {"age": expected}
    assert type(value["age"]) is int


@pytest.mark.parametrize(
    "value",
    [
        "1899-12-31",
        "1900-02-29",
        "2001-02-29",
        "2026-02-30",
        "2026-13-01",
        "2026-00-01",
        "2026-01-00",
        "2026-11-09",
        "1990",
        "1990-1-01",
        "19900101",
        "1990-03-15T00:00:00Z",
        " 1990-03-15",
        "1990-03-15\n",
        "١٩٩٠-٠٣-١٥",
        "unsafe\u202e",
    ],
)
def test_strict_calendar_dates_and_bounds(value):
    with pytest.raises(ValueError, match="Invalid date of birth"):
        birth_date(value, date(2026, 11, 8))
    assert details(value, date(2026, 11, 8)) == {}


@pytest.mark.parametrize("value", [None, ""])
def test_no_date_means_no_age(value):
    assert birth_date(value) is None
    assert details(value, date(2026, 11, 8)) == {}


@pytest.mark.parametrize("value", ["1900-01-01", "2000-02-29", "2026-11-08"])
def test_valid_dates_are_stored_as_iso(value):
    assert birth_date(value, date(2026, 11, 8)) == value


def test_public_entries_and_me_use_one_utc_date_and_never_publish_birth_data(app, monkeypatch):
    with TestClient(app) as client:
        account = login(client)
        with app.state.store.connection() as c:
            c.execute(
                "INSERT INTO player_ratings VALUES(?, 'tablut', 1200, 1, 1, 0, 0, 1, 0)",
                (account["id"],),
            )
        saved = client.patch("/api/profile", json={"date_of_birth": "1990-01-01"})
        assert saved.status_code == 200
        assert saved.json()["date_of_birth"] == "1990-01-01"
        calls = []

        # A second date sample for `me` would cross this UTC birthday boundary.
        def boundary():
            calls.append(True)
            return date(2026, 12, 31) if len(calls) == 1 else date(2027, 1, 1)

        monkeypatch.setattr(account_policy, "utc_today", boundary)
        response = client.get("/api/leaderboard?ruleset=tablut")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        board = response.json()
        assert board["entries"][0] == board["me"] and board["me"]["age"] == 36
        assert len(calls) == 1
        for field in (
            "date_of_birth",
            "birth_year",
            "legacy_birth_year",
            "profile_private_fields",
            "age_range",
            "email",
            "password_hash",
        ):
            assert field not in board["me"]
        monkeypatch.setattr(account_policy, "utc_today", lambda: date(2027, 1, 1))
        assert client.get("/api/leaderboard?ruleset=tablut").json()["me"]["age"] == 37
        assert client.patch("/api/profile", json={"date_of_birth": None}).status_code == 200
        assert "age" not in client.get("/api/leaderboard?ruleset=tablut").json()["me"]


def test_utc_today_uses_utc_even_when_local_midnight_differs(monkeypatch):
    class Clock:
        @staticmethod
        def now(zone):
            assert zone is timezone.utc
            return datetime(2026, 12, 31, 23, 59, 59, tzinfo=zone)

    monkeypatch.setattr(account_policy, "datetime", Clock)
    assert account_policy.utc_today() == date(2026, 12, 31)
    assert details("1990-01-01", account_policy.utc_today())["age"] == 36
