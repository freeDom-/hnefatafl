"""Headless tournament configuration: portable documents and legacy CLI files."""

import json
import re
from pathlib import Path

import pytest

from auth_support import AuthenticatedClient as TestClient

from hnefatafl.ai import SUPPORTED_AI_CONFIG_FIELDS, tournament
from hnefatafl.cli import check_headless_models, main, normalise_tournament_config
from hnefatafl.models import PolicyValueNet, save_checkpoint
from hnefatafl.server import create_app


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
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as session:
        yield session


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
    # The historical MCTS wrapper is the default representation, so it is not
    # recorded explicitly in the effective configuration.
    assert "search_wrapper" not in normalised["players"][1]
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


def first_player(player):
    return normalise_tournament_config(portable([player, {"config": flattened()}]))["players"][0]


def test_named_fixed_profiles_are_authoritative_like_the_api():
    baseline = first_player({"profile_id": "baseline", "config": {}})
    assert baseline["algorithm"] == "baseline"
    assert baseline["time_limit"] == 1.0
    assert baseline["seed"] == 0
    # A matching explicit algorithm keeps the canonical preset.
    assert first_player({"profile_id": "baseline", "config": {"algorithm": "baseline"}}) == baseline
    # A conflicting algorithm or search configuration is replaced, exactly as the
    # API does for a named fixed profile.
    assert (
        first_player({"profile_id": "baseline", "config": {"algorithm": "mcts", "iterations": 100}})
        == baseline
    )
    # Discarded preset settings are not validated, so a typo inside them is not an
    # error either: the API ignores the whole object.
    assert first_player({"profile_id": "baseline", "config": {"iteratons": 100}}) == baseline
    alpha = first_player(
        {"profile_id": "alpha_beta", "config": {"algorithm": "mcts", "depth": 1, "time_limit": 0.5}}
    )
    assert alpha["algorithm"] == "alpha_beta"
    assert alpha["depth"] == 5
    assert alpha["time_limit"] == 2.0
    mcts = first_player(
        {"profile_id": "mcts", "config": {"iterations": 3, "search_wrapper": "none"}}
    )
    assert mcts["algorithm"] == "mcts"
    assert mcts["iterations"] == 5000
    assert mcts["rollout_depth"] == 8
    assert "search_wrapper" not in mcts
    maiden = first_player({"profile_id": "shield_maiden", "config": {"algorithm": "baseline"}})
    assert maiden["algorithm"] == "shield_maiden"
    assert maiden["heuristic_version"] == 1


def test_custom_and_neural_profiles_keep_their_configuration():
    # A trained model is not a fixed preset, so its own configuration wins.
    neural_player = first_player(
        {
            "profile_id": "neural",
            "config": {"algorithm": "neural", "model_id": "model-a", "iterations": 33},
        }
    )
    assert neural_player["algorithm"] == "neural"
    assert neural_player["model_id"] == "model-a"
    assert neural_player["iterations"] == 33
    # Without an algorithm the catalog parameters fill the gaps only.
    filled = first_player({"profile_id": "neural", "config": {"model_id": "model-a"}})
    assert filled["algorithm"] == "neural"
    assert filled["iterations"] == 40
    assert filled["time_limit"] == 10.0
    # A participant without a profile keeps every recorded setting.
    custom = first_player({"config": {"algorithm": "alpha_beta", "depth": 1, "time_limit": 0.5}})
    assert custom["algorithm"] == "alpha_beta"
    assert custom["depth"] == 1
    assert custom["time_limit"] == 0.5
    with pytest.raises(ValueError, match="Unknown tournament player profile"):
        first_player({"profile_id": "missing", "config": {}})
    with pytest.raises(ValueError, match="profile_id must be a string"):
        first_player({"profile_id": 7, "config": {}})


def test_portable_presets_resolve_identically_through_the_api(client):
    document = portable(
        [
            {"profile_id": "baseline", "config": {"algorithm": "mcts", "iterations": 100}},
            {"profile_id": "alpha_beta", "config": {"algorithm": "mcts", "depth": 1}},
        ],
        games=2,
    )
    normalised = normalise_tournament_config(document)["players"]
    assert normalised[0]["algorithm"] == "baseline"
    assert normalised[0]["time_limit"] == 1.0
    assert normalised[1]["algorithm"] == "alpha_beta"
    assert normalised[1]["depth"] == 5
    response = client.post("/api/tournaments", json=document)
    assert response.status_code == 201, response.text
    record = response.json()
    stored = [player["config"] for player in record["config"]["players"]]

    # Both paths resolve the same effective configuration for the same document.
    # The tournament seed supersedes any per-participant seed, exactly as it does
    # for the browser, so only that field may differ.
    def without_seed(config):
        return {key: value for key, value in config.items() if key != "seed"}

    assert [without_seed(config) for config in stored] == [
        without_seed(config) for config in normalised
    ]
    assert [config["seed"] for config in stored] == [4, 4]
    assert client.post(f"/api/tournaments/{record['id']}/cancel").status_code == 200


def test_fixed_role_rulesets_are_validated_at_the_cli_boundary():
    players = [neural("model-a", iterations=5), neural("model-b", iterations=5)]
    for rulesets in (["brandubh", "tablut"], [], None, "brandubh", ["unknown"]):
        document = portable(players, mode="fixed_roles", games=5)
        document["rulesets"] = rulesets
        with pytest.raises(ValueError, match="battlefield|rulesets"):
            tournament(normalise_tournament_config(document))
    # A flattened document is validated the same way.
    with pytest.raises(ValueError, match="battlefield"):
        tournament(
            {
                "mode": "fixed_roles",
                "rulesets": ["brandubh", "tablut"],
                "players": [
                    {"algorithm": "neural", "model_id": "a", "iterations": 5},
                    {"algorithm": "neural", "model_id": "b", "iterations": 5},
                ],
                "games": 5,
            }
        )


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
    normalised = normalise_tournament_config(document)["players"]
    assert normalised[0] == normalised[1]
    assert normalised[0]["algorithm"] == "baseline"
    assert normalised[0]["time_limit"] == 0.01
    with pytest.raises(ValueError, match="player 1 has an unsupported field: output"):
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


def test_portable_configs_reject_unsupported_and_misspelled_fields():
    with pytest.raises(ValueError, match="player 1 config has an unsupported field: iteratons"):
        normalise_tournament_config(portable([neural("model-a", iteratons=500), neural("model-b")]))
    with pytest.raises(ValueError, match="player 2 config has an unsupported field: wrapper"):
        normalise_tournament_config(
            portable([neural("model-a"), neural("model-b", wrapper="mcts")])
        )
    with pytest.raises(ValueError, match="player 2 config has an unsupported field: output"):
        normalise_tournament_config(
            portable(
                [{"config": flattened()}, {"config": {"algorithm": "baseline", "output": "/x"}}]
            )
        )
    # Unknown nested alpha-beta search fields are rejected by the shared search
    # validation instead of being ignored by the engine.
    with pytest.raises(
        ValueError, match="player 1: Unsupported search configuration field: unknown"
    ):
        normalise_tournament_config(
            portable(
                [
                    {"config": {"algorithm": "alpha_beta", "search": {"unknown": True}}},
                    {"config": {"algorithm": "alpha_beta"}},
                ]
            )
        )
    # A fixed preset discards the recorded configuration, so a typo inside it is
    # not an error in either path.
    assert (
        normalise_tournament_config(
            portable(
                [{"profile_id": "baseline", "config": {"iteratons": 500}}, {"config": flattened()}]
            )
        )["players"][0]["algorithm"]
        == "baseline"
    )


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"iterations": "500"}, "player 1: AI iterations must be an integer"),
        ({"iterations": 500.5}, "player 1: AI iterations must be an integer"),
        ({"iterations": 0}, "player 1: AI iterations must be between 1 and 5000"),
        ({"iterations": 5001}, "player 1: AI iterations must be between 1 and 5000"),
        ({"time_limit": 0}, "player 1: AI time_limit must be between 0.01 and 15"),
        ({"exploration": 11}, "player 1: AI exploration must be between 0 and 10"),
        ({"temperature": 6}, "player 1: AI temperature must be between 0 and 5"),
        ({"rollout_depth": 51}, "player 1: AI rollout_depth must be between 0 and 50"),
        ({"search_wrapper": "mcts "}, "player 1: Search wrapper must be mcts, alpha_beta or none"),
        ({"evaluation": "guess"}, "player 1: Evaluation must be heuristic or neural"),
        ({"heuristic": "shield_bearer"}, "player 1: A search heuristic applies only"),
    ],
)
def test_portable_neural_configs_validate_types_and_ranges(config, message):
    with pytest.raises(ValueError, match=message):
        normalise_tournament_config(portable([neural("model-a", **config), neural("model-b")]))


def test_portable_config_rejects_a_non_string_model_id():
    with pytest.raises(ValueError, match="player 1: Model ID must be a string"):
        normalise_tournament_config(
            portable([{"config": {"algorithm": "neural", "model_id": 7}}, {"config": flattened()}])
        )


def test_portable_valid_search_configurations_are_accepted():
    def both(**changes):
        return normalise_tournament_config(
            portable([neural("model-a", **changes), neural("model-b", **changes)])
        )["players"]

    mcts = both(
        search_wrapper="mcts", iterations=120, exploration=1.2, temperature=0.4, time_limit=3
    )
    assert mcts[0]["iterations"] == 120
    assert mcts[0]["exploration"] == 1.2
    assert mcts[0]["temperature"] == 0.4
    assert mcts[0]["time_limit"] == 3
    alpha = both(
        search_wrapper="alpha_beta",
        depth=4,
        time_limit=2,
        search={"transposition": False, "move_ordering": True},
    )
    assert alpha[0]["search_wrapper"] == "alpha_beta"
    assert alpha[0]["depth"] == 4
    assert alpha[0]["search"]["transposition"] is False
    assert alpha[0]["search"]["move_ordering"] is True
    assert alpha[0]["search"]["symmetry_pruning"] is False
    direct = both(search_wrapper="none", temperature=0.7, time_limit=1)
    assert direct[0]["search_wrapper"] == "none"
    assert direct[0]["temperature"] == 0.7


def test_portable_model_path_is_preserved_and_validated(tmp_path, checkpoints):
    document = portable(
        [
            neural("model-a", model_path=str(checkpoints[0]), iterations=1, time_limit=0.01),
            neural("model-b", model_path=str(checkpoints[1]), iterations=1, time_limit=0.01),
        ],
        mode="fixed_roles",
        games=1,
        max_plies=1,
    )
    normalised = normalise_tournament_config(document)["players"]
    assert normalised[0]["model_path"] == str(checkpoints[0])
    assert normalised[1]["model_path"] == str(checkpoints[1])
    check_headless_models(normalised)
    with pytest.raises(ValueError, match="model_path does not exist"):
        check_headless_models([{**normalised[0], "model_path": str(tmp_path / "missing.pt")}])
    # A model identifier is never turned into a filesystem path.
    without_path = normalise_tournament_config(portable([neural("model-a"), neural("model-b")]))[
        "players"
    ]
    assert "model_path" not in without_path[0]
    with pytest.raises(ValueError, match="needs a model_path"):
        check_headless_models(without_path)


def test_flattened_configurations_keep_their_lenient_interpretation():
    # Historical flattened files are passed through untouched. Validating them
    # would reject files that already work, so only the portable representation
    # carries the strict contract.
    config = {
        "rulesets": ["brandubh"],
        "players": [
            {"algorithm": "baseline", "time_limit": 0.01, "output": "/tmp/legacy"},
            {"algorithm": "baseline", "time_limit": 0.01},
        ],
        "games": 2,
        "max_plies": 1,
        "seed": 1,
    }
    assert normalise_tournament_config(config) is config
    assert tournament(config)["games"] is not None


def test_cli_rejects_a_misspelled_search_parameter_before_playing(tmp_path, capsys):
    document = portable(
        [
            neural("model-a", model_path=str(tmp_path / "a.pt"), iteratons=500),
            neural("model-b", model_path=str(tmp_path / "b.pt")),
        ],
        games=1,
        max_plies=1,
    )
    path = tmp_path / "tournament.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert main(["tournament", "--config", str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    failure = json.loads(captured.err)
    assert "player 1 config has an unsupported field: iteratons" in failure["error"]


def test_portable_fixed_role_participant_seeds_are_ignored(
    tmp_path, checkpoints, capsys, monkeypatch
):
    played = []

    def play(_ruleset, attackers, defenders, **options):
        played.append((options["seed"], attackers["model_id"], defenders["model_id"]))
        return {
            "winner": "attackers",
            "outcome": "completed",
            "plies": 1,
            "moves": [],
            "total_decision_time": 0.01,
        }

    monkeypatch.setattr("hnefatafl.ai.play_game", play)
    document = portable(
        [
            neural(
                "model-a", model_path=str(checkpoints[0]), iterations=1, time_limit=0.01, seed=11
            ),
            neural(
                "model-b", model_path=str(checkpoints[1]), iterations=1, time_limit=0.01, seed=12
            ),
        ],
        mode="fixed_roles",
        games=5,
        max_plies=1,
    )
    path = tmp_path / "tournament.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert main(["tournament", "--config", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    # The tournament seed derives every game seed; the participant seeds are not a
    # search difference and never reach the engine.
    assert [seed for seed, *_ in played] == [4, 5, 6, 7, 8]
    assert [attacker for _, attacker, _ in played] == ["model-a"] * 5
    assert [defender for _, _, defender in played] == ["model-b"] * 5
    assert report["seed"] == 4
    assert len(report["games"]) == 5
    assert [game["attacker_player"] for game in report["games"]] == [0] * 5


@pytest.mark.parametrize(
    ("seed", "message"),
    [
        (-1, "player 1: AI seed must be between 0 and 4294967295"),
        (2**32, "player 1: AI seed must be between 0 and 4294967295"),
        ("42", "player 1: AI seed must be an integer"),
        (1.5, "player 1: AI seed must be an integer"),
        (True, "player 1: AI seed must be an integer"),
        ([], "player 1: AI seed must be an integer"),
    ],
)
def test_portable_participant_seeds_are_still_validated(seed, message):
    with pytest.raises(ValueError, match=message):
        normalise_tournament_config(portable([neural("model-a", seed=seed), neural("model-b")]))


def test_frontend_field_allowlist_matches_the_backend_contract():
    """The importer mirrors the server's accepted AI fields; keep them in step."""
    source = (
        Path(__file__).resolve().parents[1] / "frontend/src/tournamentConfigImport.ts"
    ).read_text(encoding="utf-8")
    block = re.search(r"const AI_CONFIG_FIELDS = new Set\(\[(.*?)\]\)", source, re.S)
    assert block, "the frontend AI configuration allowlist is missing"
    frontend = set(re.findall(r"'([^']+)'", block.group(1)))
    assert frontend == set(SUPPORTED_AI_CONFIG_FIELDS)
