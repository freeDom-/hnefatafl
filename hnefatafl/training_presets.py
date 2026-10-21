"""Ruleset-specific starting budgets; strength requires measured comparison."""

from copy import deepcopy


RECOMMENDED = {
    "brandubh": {
        "channels": 32,
        "blocks": 2,
        "iterations": 20,
        "games": 16,
        "epochs": 2,
        "batch_size": 64,
        "learning_rate": 0.001,
        "mcts_iterations": 128,
        "mcts_time_limit": 2.0,
        "replay_buffer_size": 8000,
        "max_game_plies": 200,
        "evaluation_games": 12,
        "evaluation_iterations": 64,
        "evaluation_max_plies": 300,
        "dirichlet_alpha": 0.3,
    },
    "tablut": {
        "channels": 48,
        "blocks": 3,
        "iterations": 30,
        "games": 24,
        "epochs": 3,
        "batch_size": 96,
        "learning_rate": 0.0007,
        "mcts_iterations": 192,
        "mcts_time_limit": 3.0,
        "replay_buffer_size": 12000,
        "max_game_plies": 300,
        "evaluation_games": 16,
        "evaluation_iterations": 96,
        "evaluation_max_plies": 500,
        "dirichlet_alpha": 0.2,
    },
    "copenhagen": {
        "channels": 64,
        "blocks": 4,
        "iterations": 40,
        "games": 32,
        "epochs": 3,
        "batch_size": 128,
        "learning_rate": 0.0005,
        "mcts_iterations": 256,
        "mcts_time_limit": 4.0,
        "replay_buffer_size": 20000,
        "max_game_plies": 500,
        "evaluation_games": 20,
        "evaluation_iterations": 128,
        "evaluation_max_plies": 800,
        "dirichlet_alpha": 0.15,
    },
}


def training_presets() -> list[dict]:
    """Return copies so callers cannot mutate future default configurations."""
    sizes = {"brandubh": 7, "tablut": 9, "copenhagen": 11}
    reasons = {
        "brandubh": "Compact 7×7 tactics: 32 channels, two residual blocks and 128 search visits.",
        "tablut": "9×9 edge escape: wider/deeper features and longer self-play and evaluation.",
        "copenhagen": "11×11 siege and shieldwalls: 64 channels, four blocks and larger replay/search budgets.",
    }
    return [
        {
            "ruleset": ruleset,
            "size": sizes[ruleset],
            "config": {"preset": "recommended", **deepcopy(config)},
            "reason": reasons[ruleset],
        }
        for ruleset, config in RECOMMENDED.items()
    ]
