"""Out-of-band historical records for read/play regressions, never an API bypass."""

from datetime import datetime, timezone
import secrets
from uuid import uuid4

from hnefatafl.ai import ai_profiles
from hnefatafl.auth import COOKIE
from hnefatafl.game_records import game_record
from hnefatafl.identities import effective_ai_config
from hnefatafl.security_context import principal
from hnefatafl.server import ai_variant_id


def stored_player(store, player):
    """Seed a retained player identity in historical fixtures only."""
    account = principal.get()
    with store.connection() as c:
        c.execute(
            "INSERT INTO players(id,name,created_at,owner_account_id) VALUES(?,?,?,?)",
            (
                player["id"],
                player["name"],
                player["created_at"],
                account["id"] if account else None,
            ),
        )
    return {**player, "rune": store.rune(f"player:{player['id']}")}


def retired_player(store, player_id, deleted_at):
    with store.connection() as c:
        return (
            c.execute(
                "UPDATE players SET deleted_at=? WHERE id=? AND deleted_at IS NULL",
                (deleted_at, player_id),
            ).rowcount
            == 1
        )


def historical_player(client, name):
    account = client.app.state.auth.session(client.cookies.get(COOKIE))
    token = principal.set(account)
    try:
        return stored_player(
            client.app.state.store,
            {"id": uuid4().hex, "name": name, "created_at": datetime.now(timezone.utc).isoformat()},
        )
    finally:
        principal.reset(token)


def historical_game(client, **configuration):
    """Persist a former setup explicitly, then read it through the real owned API."""
    app = client.app
    account = app.state.auth.session(client.cookies.get(COOKIE))
    token = principal.set(account)
    try:
        catalog = {p["id"]: p for p in ai_profiles()}
        mode = configuration.get("mode", "human-human")
        human_side = configuration.get("human_side", "defenders")
        seed = configuration.get("seed", secrets.randbits(32))
        players = {}
        models = {m["id"]: m for m in client.get("/api/models").json()}

        def agent(provided, *, advisor=False):
            profile_id = provided.get("profile_id", "baseline")
            config = effective_ai_config(provided.get("config", {}), profile_id)
            # Fixed named presets were resolved at creation, except advisors.
            if not advisor and provided.get("profile_id") in set(catalog) - {"neural"}:
                config = effective_ai_config(
                    {
                        **catalog[profile_id]["parameters"],
                        "algorithm": catalog[profile_id]["algorithm"],
                    }
                )
            profile = catalog[config["algorithm"]]
            config["seed"] = seed
            result = {
                "name": provided.get("name", "Move advisor" if advisor else "Computer"),
                "kind": "ai",
                "profile_id": profile["id"],
                "profile_name": profile["name"],
                "config": config,
                "variant_id": ai_variant_id(config),
            }
            if config.get("model_id") in models:
                result["model_name"] = models[config["model_id"]]["name"]
            return result

        for side in ("attackers", "defenders"):
            provided = configuration.get("players", {}).get(side, {})
            if mode == "ai-ai" or (mode == "human-ai" and side != human_side):
                players[side] = agent(provided)
            else:
                player_id = provided.get("player_id")
                person = next((p for p in app.state.store.players() if p["id"] == player_id), None)
                if person is None:
                    name = provided.get("name", side.title())
                    person = next((p for p in app.state.store.players() if p["name"] == name), None)
                    person = person or historical_player(client, name)
                players[side] = {
                    "name": person["name"],
                    "player_id": person["id"],
                    "kind": "human",
                    "config": {},
                }
        advisor = (
            agent(configuration["advisor"], advisor=True) if configuration.get("advisor") else None
        )
        game = game_record(
            configuration.get("ruleset", "tablut"), mode, human_side, players, seed, advisor=advisor
        )
        app.state.store.create_game(game)
    finally:
        principal.reset(token)
    response = client.get(f"/api/games/{game['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def historical_response(client, *, json):
    """A real GET response after fixture insertion; it never posts a creation request."""
    game = historical_game(client, **json)
    return client.get(f"/api/games/{game['id']}")
