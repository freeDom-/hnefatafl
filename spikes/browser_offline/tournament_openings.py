"""Generate and validate reproducible tournament opening corpora.

Positions are produced by deterministic seeded random legal playouts using the
authoritative engine. Only nonterminal states with legal moves are retained.
Full state identity (board, turn, ply, winner, ordered repetition history) is
canonicalized under D4 symmetries for duplicate rejection. The corpus records its
generator configuration, seed, per-opening starting ply/kind and a digest so a
tournament report can bind the exact opening set it used.

    .venv/bin/python spikes/browser_offline/tournament_openings.py \
      --ruleset brandubh --pairs 50 --seed 20261124 --output /tmp/brandubh-openings.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from hnefatafl.engine import RULESETS, State, apply_move, legal_moves, new_game  # noqa: E402
from hnefatafl.symmetry import canonical_state_key  # noqa: E402

SCHEMA = 1
KIND = "browser-analysis-tournament-openings"
GENERATOR = "random-playout-v1"
KINDS = ("opening", "midgame", "tactical", "endgame", "repetition")


def _kind_for_ply(ply: int, max_plies: int) -> str:
    fraction = ply / max(1, max_plies)
    if fraction <= 0.15:
        return "opening"
    if fraction <= 0.4:
        return "midgame"
    if fraction <= 0.65:
        return "tactical"
    if fraction <= 0.85:
        return "endgame"
    return "repetition"


def _canonical_state(state: State) -> str:
    key, _ = canonical_state_key(state)
    return repr(key)


def generate_corpus(ruleset: str, pairs: int, seed: int, max_plies: int) -> dict:
    if ruleset not in RULESETS:
        raise ValueError(f"unsupported ruleset {ruleset!r}")
    if type(pairs) is not int or pairs < 1:
        raise ValueError("pairs must be a positive integer")
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    if type(max_plies) is not int or max_plies < 4:
        raise ValueError("max_plies must be at least 4")
    rng = random.Random(seed)
    openings = []
    seen: set[str] = set()
    attempts = 0
    limit = max(1000, pairs * 200)
    while len(openings) < pairs and attempts < limit:
        attempts += 1
        state = new_game(ruleset)
        if rng.random() < 0.2:
            target_ply = 0
        else:
            low = max(2, max_plies // 12)
            high = max(low + 1, max_plies)
            target_ply = rng.randint(low, high)
        while state.winner is None and state.ply < target_ply:
            moves = legal_moves(state)
            if not moves:
                break
            state = apply_move(state, rng.choice(moves))
        if state.winner is not None or state.ply >= max_plies:
            continue
        if not legal_moves(state):
            continue
        key = _canonical_state(state)
        if key in seen:
            continue
        seen.add(key)
        index = len(openings)
        openings.append(
            {
                "name": f"{ruleset}-corpus-{index:03d}",
                "kind": _kind_for_ply(state.ply, max_plies),
                "starting_ply": state.ply,
                "state": state.to_dict(),
            }
        )
    if len(openings) < pairs:
        raise ValueError(
            f"could only generate {len(openings)} unique nonterminal openings of {pairs}"
        )
    canonical = [
        {"state": entry["state"], "starting_ply": entry["starting_ply"]}
        for entry in openings
    ]
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "kind": KIND,
        "schema": SCHEMA,
        "ruleset": ruleset,
        "generator": {
            "name": GENERATOR,
            "seed": seed,
            "max_plies": max_plies,
            "requested_pairs": pairs,
            "generated_pairs": len(openings),
        },
        "corpus_sha256": digest,
        "openings": openings,
        "authoritative": False,
    }


def validate_corpus(document: dict, ruleset: str, *, minimum_pairs: int = 1) -> dict:
    if not isinstance(document, dict):
        raise ValueError("opening corpus must be an object")
    if document.get("kind") != KIND or document.get("schema") != SCHEMA:
        raise ValueError("unsupported opening corpus schema")
    if document.get("ruleset") != ruleset:
        raise ValueError("opening corpus ruleset does not match the tournament")
    generator = document.get("generator")
    if not isinstance(generator, dict):
        raise ValueError("opening corpus lacks generator configuration")
    for field in ("name", "seed", "max_plies", "requested_pairs"):
        if field not in generator:
            raise ValueError(f"opening corpus generator lacks {field}")
    if generator.get("name") != GENERATOR:
        raise ValueError("opening corpus generator identity is unsupported")
    openings = document.get("openings")
    if not isinstance(openings, list) or len(openings) < minimum_pairs:
        raise ValueError(
            f"opening corpus has {len(openings) if isinstance(openings, list) else 0} "
            f"openings; at least {minimum_pairs} are required"
        )
    names = set()
    keys = set()
    canonical = []
    for index, entry in enumerate(openings):
        if not isinstance(entry, dict):
            raise ValueError("opening entry must be an object")
        name = entry.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("opening names must be unique non-empty strings")
        names.add(name)
        state = State.from_dict(entry.get("state"))
        if state.ruleset != ruleset:
            raise ValueError("opening state ruleset does not match the corpus")
        if state.winner is not None or not legal_moves(state):
            raise ValueError("opening corpora must contain nonterminal positions with moves")
        if entry.get("starting_ply") != state.ply:
            raise ValueError("opening starting ply does not match the serialized state")
        if entry.get("kind") not in KINDS:
            raise ValueError("opening kind is unsupported")
        key = _canonical_state(state)
        if key in keys:
            raise ValueError("opening corpus contains duplicate or symmetry-equivalent positions")
        keys.add(key)
        canonical.append({"state": entry["state"], "starting_ply": state.ply})
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if document.get("corpus_sha256") != digest:
        raise ValueError("opening corpus digest does not match its contents")
    if generator.get("generated_pairs") != len(openings):
        raise ValueError("opening corpus generated_pairs does not match the contents")
    return {
        "kind": "corpus",
        "generator": dict(generator),
        "corpus_sha256": digest,
        "ruleset": ruleset,
        "count": len(openings),
        "openings": [
            {"name": entry["name"], "state": entry["state"]} for entry in openings
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ruleset", choices=tuple(RULESETS), required=True)
    parser.add_argument("--pairs", type=int, required=True)
    parser.add_argument("--seed", type=int, default=20261124)
    parser.add_argument("--max-plies", type=int, default=120)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validate", type=Path)
    args = parser.parse_args()
    try:
        if args.validate is not None:
            document = json.loads(args.validate.read_text())
            validated = validate_corpus(document, args.ruleset, minimum_pairs=args.pairs)
            result = {
                "ok": True,
                "ruleset": args.ruleset,
                "openings": validated["count"],
                "corpus_sha256": validated["corpus_sha256"],
            }
            print(json.dumps(result, indent=2))
            return 0
        document = generate_corpus(args.ruleset, args.pairs, args.seed, args.max_plies)
        validate_corpus(document, args.ruleset, minimum_pairs=args.pairs)
    except (ValueError, OSError, TypeError) as error:
        print(json.dumps({"ok": False, "failures": [str(error)]}, indent=2))
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2) + "\n")
    print(
        json.dumps(
            {
                "ok": True,
                "ruleset": args.ruleset,
                "openings": len(document["openings"]),
                "corpus_sha256": document["corpus_sha256"],
                "output": str(args.output),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
