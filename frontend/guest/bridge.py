"""Private protocol for the guest worker; never imported by the API server."""

import json
from hnefatafl.engine import RULESETS
from hnefatafl.guest import GuestSession

ENGINE_VERSION = "__GUEST_ENGINE_VERSION__"
session = GuestSession(ENGINE_VERSION)


def dispatch(payload):
    request = json.loads(payload)
    action = request.get("action")
    if action == "info" and set(request) == {"action"}:
        value = {
            "engine_version": ENGINE_VERSION,
            "rulesets": [
                {"id": r.id, "name": r.name, "version": r.version} for r in RULESETS.values()
            ],
        }
    elif action == "restore" and set(request) == {"action", "record"}:
        value = session.restore(request["record"])
    elif action == "new" and set(request) == {"action", "ruleset", "human_side"}:
        value = session.start(request["ruleset"], request["human_side"])
    elif action == "move" and set(request) == {"action", "source", "target"}:
        value = session.play(computer=False, source=request["source"], target=request["target"])
    elif action == "computer" and set(request) == {"action"}:
        value = session.play(computer=True)
    elif action == "seek" and set(request) == {"action", "cursor"}:
        value = session.seek(request["cursor"])
    else:
        raise ValueError("Invalid guest operation.")
    return json.dumps(value)
