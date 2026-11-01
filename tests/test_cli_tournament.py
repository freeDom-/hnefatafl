"""Headless tournament configuration: portable documents and legacy CLI files."""

import json

import pytest

from hnefatafl.ai import tournament
from hnefatafl.cli import check_headless_models, main, normalise_tournament_config
from hnefatafl.models import PolicyValueNet, save_checkpoint


def portable(players, **changes):
    return {
        "name": "Portable",
        "rulesets": ["brandubh"],
        "players": players,
        "games": 2,
        "max_plies": 1,
        "seed": 4,
        **changes,
    }


def neural(model_id, **changes):
    return {"config": {"algorithm": "neural", "model_id": model_id, **changes}}


def flattened(algorithm="baseline", **changes):
    return {"algorithm": algorithm, "time_limit": 0.01, **changes}


@pytest.fixture
def checkpoints(tmp_path):
    return [
        save_checkpoint(tmp_path / f"{name}.pt", PolicyValueNet("brandubh", 4, 0), {"name": name})
        for name in ("Attacker", "Defender")
    ]


def test_flattened_configurations_pass_through_untouched():
    config = portable([flattened(), flattened("alpha_beta", depth=1)])
    assert normalise_tournament_config(config) is config
    # A document without participants is also left alone.
    assert normalise_tournament_config({"rulesets": ["brandubh"]}) == {"rulesets": ["brandubh"]}


def test_normalises_portable_round_robin_players():
    document = portable(
        [
            {"profile_id": "baseline", "config": {"algorithm": "baseline", "time_limit": 1}},
            neural("model-a", search_wrapper="mcts", iterations=33, time_limit=7.5),
        ]
    )
    normalised = normalise_tournament_config(document)
    assert [player["algorithm"] for player in normalised["players"]] == ["baseline", "neural"]
    assert normalised["players"][1]["model_id"] == "model-a"
    assert normalised["players"][1]["iterations"] == 33
    assert normalised["players"][1]["time_limit"] == 7.5
    assert normalised["players"][1]["search_wrapper"] == "mcts"
    assert normalised["games"] == 2 and normalised["max_plies"] == 1 and normalised["seed"] == 4
    # The source document is never mutated.
    assert document["players"][0] == {
        "profile_id": "baseline",
        "config": {"algorithm": "baseline", "time_limit": 1},
    }


def test_normalises_portable_fixed_role_players_and_keeps_the_seat_order():
    document = portable(
        [neural("model-a", iterations=5), neural("model-b", iterations=5)],
        mode="fixed_roles",
        rulesets=["brandubh"],
    )
    normalised = normalise_tournament_config(document)
    assert normalised["mode"] == "fixed_roles"
    assert [player["model_id"] for player in normalised["players"]] == ["model-a", "model-b"]
    assert [player["iterations"] for player in normalised["players"]] == [5, 5]


def test_recorded_profile_parameters_apply_only_without_an_algorithm():
    assert normalise_tournament_config(
        portable([{"profile_id": "baseline", "config": {}}, {"config": flattened()}])
    )["players"][0] == {"algorithm": "baseline", "time_limit": 1.0}
    assert normalise_tournament_config(
        portable([{"profile_id": "baseline", "config": {"algorithm": "mcts"}}, {"config": {}}])
    )["players"][0] == {"algorithm": "mcts"}
    with pytest.raises(ValueError, match="Unknown tournament player profile"):
        normalise_tournament_config(
            portable([{"profile_id": "missing", "config": {}}, {"config": {}}])
        )
    with pytest.raises(ValueError, match="profile_id must be a string"):
        normalise_tournament_config(portable([{"profile_id": 7, "config": {}}, {"config": {}}]))


def test_presentation_fields_are_ignored_and_unknown_fields_rejected():
    document = portable(
        [
            {
                "config": flattened(),
                "name": "Raven",
                "model_name": "Raven",
                "profile_name": "Shield bearer",
                "variant_id": "ai:1",
                "agent_rune": "ᚠ",
                "rune": "ᚠ",
                "model": {"id": "model-a"},
            },
            {"config": flattened()},
        ]
    )
    assert normalise_tournament_config(document)["players"][0] == flattened()
    with pytest.raises(ValueError, match="Unsupported tournament player field: output"):
        normalise_tournament_config(
            portable([{"config": flattened(), "output": "/tmp/elsewhere"}, {"config": flattened()}])
        )


def test_rejects_mixed_and_malformed_participants():
    with pytest.raises(ValueError, match="same representation"):
        normalise_tournament_config(portable([{"config": flattened()}, flattened()]))
    with pytest.raises(ValueError, match="config must be an object"):
        normalise_tournament_config(portable([{"config": "nope"}, {"config": flattened()}]))
    # A non-object participant cannot be nested, so it is reported as a mixed
    # document rather than silently treated as a flattened configuration.
    with pytest.raises(ValueError, match="same representation"):
        normalise_tournament_config(portable(["nope", {"config": flattened()}]))
    with pytest.raises(ValueError, match="players must be a list"):
        normalise_tournament_config(portable("nope"))


def test_portable_fixed_role_document_still_validates_the_shared_search(monkeypatch):
    played = []

    def play(_ruleset, attackers, defenders, **options):
        played.append((attackers["model_id"], defenders["model_id"]))
        return {
            "winner": "attackers",
            "outcome": "completed",
            "plies": 1,
            "moves": [],
            "total_decision_time": 0.01,
        }

    monkeypatch.setattr("hnefatafl.ai.play_game", play)
    document = portable(
        [neural("model-a", iterations=5), neural("model-b", iterations=5)],
        mode="fixed_roles",
        games=3,
    )
    result = tournament(normalise_tournament_config(document))
    assert played == [("model-a", "model-b")] * 3
    assert [game["attacker_player"] for game in result["games"]] == [0, 0, 0]
    # A same-model comparison is a valid self-play pairing.
    same = tournament(
        normalise_tournament_config(
            portable(
                [neural("model-a", iterations=5), neural("model-a", iterations=5)],
                mode="fixed_roles",
            )
        )
    )
    assert [game["attacker_player"] for game in same["games"]] == [0, 0]
    # Differing effective settings are still rejected at the CLI boundary.
    mismatched = portable(
        [neural("model-a", iterations=5), neural("model-b", iterations=6)], mode="fixed_roles"
    )
    with pytest.raises(ValueError, match="identical search settings"):
        tournament(normalise_tournament_config(mismatched))
    with pytest.raises(ValueError, match="identical search settings"):
        tournament(
            normalise_tournament_config(
                {
                    **mismatched,
                    "players": [
                        neural("model-a", search_wrapper="none"),
                        neural("model-b", search_wrapper="mcts"),
                    ],
                }
            )
        )


def test_neural_participants_need_a_resolvable_checkpoint(tmp_path, checkpoints):
    check_headless_models(
        [{"algorithm": "baseline"}, {"algorithm": "neural", "model_path": str(checkpoints[0])}]
    )
    with pytest.raises(ValueError, match="needs a model_path"):
        check_headless_models([{"algorithm": "neural"}])
    with pytest.raises(ValueError, match="needs a model_path"):
        check_headless_models([{"algorithm": "neural", "model_path": ""}])
    with pytest.raises(ValueError, match="model_path does not exist"):
        check_headless_models([{"algorithm": "neural", "model_path": str(tmp_path / "missing.pt")}])


def test_cli_runs_a_portable_fixed_role_configuration(tmp_path, checkpoints, capsys):
    document = portable(
        [
            neural("model-a", model_path=str(checkpoints[0]), iterations=1, time_limit=0.01),
            neural("model-b", model_path=str(checkpoints[1]), iterations=1, time_limit=0.01),
        ],
        mode="fixed_roles",
        games=1,
        max_plies=1,
    )
    path = tmp_path / "tournament.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert main(["tournament", "--config", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["games"]) == 1
    assert report["games"][0]["attacker_player"] == 0
    assert report["games"][0]["defender_player"] == 1


def test_cli_reports_missing_model_paths_without_running(tmp_path, capsys):
    document = portable(
        [neural("model-a", iterations=1), neural("model-b", iterations=1)],
        mode="fixed_roles",
        games=1,
        max_plies=1,
    )
    path = tmp_path / "tournament.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert main(["tournament", "--config", str(path)]) == 1
    failure = json.loads(capsys.readouterr().err)
    assert "needs a model_path" in failure["error"]
