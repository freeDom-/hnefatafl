"""Age brackets must not encode one birth year, including at decade boundaries."""

from collections import defaultdict
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from hnefatafl.account_policy import MIN_BIRTH_YEAR
from hnefatafl.online_ratings import public_age_range
from test_profile import app as app, template as template, login


@pytest.mark.parametrize(
    "birth_year,year,expected",
    [
        (1989, 2026, (36, 46)),
        (1990, 2026, (26, 36)),
        (1991, 2026, (26, 36)),
        (1999, 2026, (26, 36)),
        (2000, 2026, (16, 26)),
        (2009, 2026, (16, 26)),
        (2010, 2026, (6, 16)),
        (2019, 2026, (6, 16)),
        (1900, 2026, (116, 126)),
        (1909, 2026, (116, 126)),
        (2020, 2029, (0, 9)),
        (2029, 2029, (0, 9)),
        (1990, 2030, (30, 40)),
        (1999, 2030, (30, 40)),
    ],
)
def test_brackets_cover_both_possible_ages_without_a_boundary_signature(birth_year, year, expected):
    result = public_age_range(birth_year, year)
    assert result == {"min": expected[0], "max": expected[1]}
    calendar_age = year - birth_year
    assert result["min"] <= max(0, calendar_age - 1) <= calendar_age <= result["max"]


@pytest.mark.parametrize("year", [2026, 2029, 2030, 2099, 2100])
def test_every_public_output_matches_at_least_ten_valid_birth_years(year):
    cohorts = defaultdict(list)
    for birth_year in range(MIN_BIRTH_YEAR, year + 1):
        value = public_age_range(birth_year, year)
        if value is None:
            continue
        cohorts[(value["min"], value["max"])].append(birth_year)
    assert cohorts
    assert all(len(candidates) == 10 for candidates in cohorts.values())


@pytest.mark.parametrize("year", [2020, 2026, 2028, 2030])
def test_incomplete_youngest_cohorts_do_not_publish_identifying_birth_years(year):
    assert public_age_range(year, year) is None
    assert public_age_range(year // 10 * 10, year) is None


@pytest.mark.parametrize("birth_year", [1900, 1989, 1990, 1999, 2000, 2019, 2020, 2026])
def test_annual_observations_do_not_narrow_a_published_cohort_to_one_birth_year(birth_year):
    candidates = None
    for year in [2026, 2027, 2029, 2030, 2039, 2040, 2099, 2100]:
        bracket = public_age_range(birth_year, year)
        if bracket is None:
            continue
        current = {
            y for y in range(MIN_BIRTH_YEAR, year + 1) if public_age_range(y, year) == bracket
        }
        candidates = current if candidates is None else candidates & current
        assert len(candidates) == 10


@pytest.mark.parametrize("calendar_age", [9, 10, 29, 30, 39, 40, None])
def test_public_api_uses_same_private_brackets_for_entries_and_me_and_withdraws_them(
    app, calendar_age
):
    year = datetime.now(timezone.utc).year
    birth_year = None if calendar_age is None else year - calendar_age
    with TestClient(app) as client:
        account = login(client)
        with app.state.store.connection() as c:
            c.execute(
                "INSERT INTO player_ratings VALUES(?, 'tablut', 1200, 1, 1, 0, 0, 1, 0)",
                (account["id"],),
            )
        assert client.patch("/api/profile", json={"birth_year": birth_year}).status_code == 200
        assert client.get("/api/profile").json()["birth_year"] == birth_year

        def public():
            response = client.get("/api/leaderboard?ruleset=tablut")
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            value = response.json()
            assert value["entries"][0] == value["me"]
            assert "birth_year" not in value["me"] and "public_birth_year" not in value["me"]
            return value["me"]

        assert "age_range" not in public()
        assert client.patch("/api/profile", json={"show_age": True}).status_code == 200
        if birth_year is None or public_age_range(birth_year, year) is None:
            assert "age_range" not in public()
        else:
            bracket = public()["age_range"]
            assert bracket["min"] <= max(0, calendar_age - 1) <= calendar_age <= bracket["max"]
            candidates = [
                y for y in range(MIN_BIRTH_YEAR, year + 1) if public_age_range(y, year) == bracket
            ]
            assert len(candidates) >= 10
        assert client.patch("/api/profile", json={"show_age": False}).status_code == 200
        assert "age_range" not in public()
