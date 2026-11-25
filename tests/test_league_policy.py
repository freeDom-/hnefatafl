"""Season boundaries, fair matching and correction-safe progress policy."""

from datetime import datetime, timedelta, timezone

import pytest

from hnefatafl.league_policy import (
    Contribution,
    Standing,
    compatible_ratings,
    fold_contributions,
    rating_window,
    season_for,
)


def test_seasons_use_utc_not_the_device_month_and_roll_over_years():
    local = datetime(2027, 1, 1, 0, 30, tzinfo=timezone(timedelta(hours=2)))
    season = season_for(local)
    assert season.identifier == "2026-12"
    assert season.start == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert season.end == datetime(2027, 1, 1, tzinfo=timezone.utc)
    assert season.contains_pairing(season.end - timedelta(microseconds=1))
    assert not season.contains_pairing(season.end)
    assert season.accepts_completion(season.grace_end - timedelta(microseconds=1))
    assert not season.accepts_completion(season.grace_end)
    assert not season.accepts_completion(season.start - timedelta(microseconds=1))
    with pytest.raises(ValueError, match="timezone"):
        season_for(datetime(2026, 12, 1))


def test_leap_february_has_real_calendar_boundaries():
    season = season_for(datetime(2028, 2, 29, 12, tzinfo=timezone.utc))
    assert (season.end - season.start).days == 29
    assert season.grace_end == datetime(2028, 3, 2, tzinfo=timezone.utc)


def test_waiting_does_not_force_a_newcomer_to_accept_a_wider_rating_gap():
    assert rating_window(59.99) == 200
    assert rating_window(60) == 300
    assert rating_window(240) == 600
    assert rating_window(100000) == 600
    assert rating_window(-100) == 200
    assert not compatible_ratings(1500, 1800, 300, 0)
    assert compatible_ratings(1500, 1800, 300, 60)
    assert not compatible_ratings(1500, 2101, 599, 599)
    assert compatible_ratings(1500, 2100, 599, 599)


def test_correcting_an_early_loss_replays_the_floor_instead_of_subtracting_a_delta():
    # Loss at zero, then win: three points. Correct that loss to a win: six,
    # not seven (naive +4 would incorrectly restore the previously clamped point).
    before = fold_contributions([Contribution("first", 4, "loss"), Contribution("later", 9, "win")])
    corrected = fold_contributions(
        [Contribution("later", 9, "win"), Contribution("first", 4, "win")]
    )
    assert before.points == 3
    assert corrected.points == 6
    assert corrected.tier == "nidavellir"
    # Voiding the earlier game withdraws its contribution entirely.
    voided = fold_contributions([Contribution("later", 9, "win")])
    assert voided.points == 3
    assert voided.played == 1
    assert not voided.qualified


def test_official_sequence_drives_demotion_and_qualification():
    contributions = [Contribution(str(i), i, "win") for i in range(1, 3)]
    promoted = fold_contributions(contributions)
    assert promoted.tier == "nidavellir"
    assert not promoted.qualified
    demoted = fold_contributions([Contribution("loss", 3, "loss"), *reversed(contributions)])
    assert demoted.points == 5
    assert demoted.tier == "midgard"
    assert demoted.qualified
    assert (demoted.wins, demoted.draws, demoted.losses) == (2, 0, 1)


@pytest.mark.parametrize(
    "points,tier",
    [
        (0, "midgard"),
        (6, "nidavellir"),
        (15, "alfheim"),
        (30, "asgard"),
        (50, "valhalla"),
        (300, "valhalla"),
    ],
)
def test_tier_thresholds(points, tier):
    assert Standing(points=points).tier == tier


def test_empty_history_has_no_fabricated_participation():
    standing = fold_contributions([])
    assert standing == Standing()
    assert not standing.qualified


def test_duplicate_or_untrusted_contributions_cannot_award_twice():
    with pytest.raises(ValueError, match="Duplicate"):
        fold_contributions([Contribution("same", 1, "win"), Contribution("same", 2, "win")])
    with pytest.raises(ValueError, match="Duplicate"):
        fold_contributions([Contribution("one", 1, "win"), Contribution("two", 1, "draw")])
    with pytest.raises(ValueError, match="positive ledger"):
        fold_contributions([Contribution("game", 0, "win")])
    with pytest.raises(ValueError, match="Unknown"):
        fold_contributions([Contribution("game", 1, "client-winner")])  # type: ignore[arg-type]
