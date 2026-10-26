"""Private query-derived history; no guest storage, game writes or accounting tables."""

import base64
import hashlib
import json

from .online_core import OnlineError, instant, micros, stamp


def context_key(account, category, filters):
    return hashlib.sha256(
        json.dumps([account, category, filters], sort_keys=True).encode()
    ).hexdigest()


def cursor_value(cursor, context, *, online=False):
    if cursor is None:
        return None
    try:
        if not isinstance(cursor, str) or len(cursor) > 512:
            raise ValueError()
        value = json.loads(
            base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        )
        if not isinstance(value, list) or len(value) != 3 or value[0] != context:
            raise ValueError()
        at, identifier = value[1:]
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 100:
            raise ValueError()
        if online:
            if type(at) is not int or not -(2**63) <= at < 2**63:
                raise ValueError()
        elif not isinstance(at, str) or not 1 <= len(at) <= 80:
            raise ValueError()
        return at, identifier
    except (ValueError, TypeError, UnicodeError):
        raise OnlineError(422, "Invalid page cursor. Refresh the first page.") from None


def page(c, base, params, conditions, values, *, limit, cursor, context, online=False):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise OnlineError(422, "Page size must be between 1 and 100.")
    after = cursor_value(cursor, context, online=online)
    where = " AND ".join(conditions) or "1"
    matching = c.execute(
        f"WITH records AS ({base}) SELECT count(*) FROM records WHERE {where}", (*params, *values)
    ).fetchone()[0]
    if after:
        where += " AND (order_at < ? OR (order_at = ? AND id > ?))"
        values = [*values, after[0], after[0], after[1]]
    rows = c.execute(
        f"WITH records AS ({base}) SELECT * FROM records WHERE {where} ORDER BY order_at DESC,id ASC LIMIT ?",
        (*params, *values, limit + 1),
    ).fetchall()
    selected = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = selected[-1]
        next_cursor = (
            base64.urlsafe_b64encode(
                json.dumps([context, last["order_at"], last["id"]], separators=(",", ":")).encode()
            )
            .decode()
            .rstrip("=")
        )
    return selected, {"limit": limit, "next_cursor": next_cursor, "matching": matching}


class PersonalHistory:
    def __init__(self, online, *, outcome, replay):
        self.online, self.store = online, online.store
        self.outcome, self.account_replay = outcome, replay

    def _account_base(self, c):
        # Only uncertain old records need engine replay. SQL consumes one record
        # at a time; no full-history array or per-record DB lookup is created.
        c.create_function(
            "personal_legacy_winner", 1, lambda raw: self.outcome(json.loads(raw))["winner"]
        )
        return """SELECT id,created_at AS order_at,created_at,
            json_extract(data,'$.ruleset') AS ruleset,
            json_extract(data,'$.mode') AS mode,
            CASE WHEN json_type(data,'$.recorded_result') IS NOT NULL
                THEN json_extract(data,'$.recorded_result.winner')
                WHEN json_extract(data,'$.cursor')=json_array_length(data,'$.moves')
                THEN json_extract(data,'$.state.winner')
                ELSE personal_legacy_winner(data) END AS winner,
            coalesce(json_extract(data,'$.recorded_result.plies'),json_array_length(data,'$.moves')) AS plies,
            json_extract(data,'$.ended_at') AS ended_at,
            substr(json_extract(data,'$.players.attackers.name'),1,80) AS attacker_name,
            substr(json_extract(data,'$.players.defenders.name'),1,80) AS defender_name,
            json_extract(data,'$.players.attackers.kind') AS attacker_kind,
            json_extract(data,'$.players.defenders.kind') AS defender_kind
            FROM games WHERE owner_account_id=?"""

    @staticmethod
    def _account_summary(row):
        return {
            "id": row["id"],
            "category": "account-single-player",
            "ruleset": row["ruleset"],
            "mode": row["mode"],
            "created_at": row["created_at"],
            "ended_at": row["ended_at"],
            "status": "completed" if row["winner"] else "in_progress",
            "winner": row["winner"],
            "plies": row["plies"],
            "players": {
                "attackers": {"name": row["attacker_name"], "kind": row["attacker_kind"]},
                "defenders": {"name": row["defender_name"], "kind": row["defender_kind"]},
            },
        }

    def single_player(
        self, credential, *, limit=25, cursor=None, ruleset=None, status=None, winner=None
    ):
        filters = {"ruleset": ruleset, "status": status, "winner": winner}
        with self.store.connection() as c:
            c.execute("BEGIN")
            account = self.online._identity(c, credential)
            base = self._account_base(c)
            totals = dict(
                c.execute(
                    f"""WITH records AS ({base}) SELECT
                count(*) AS total_games,
                count(winner) AS completed_games,
                count(*)-count(winner) AS in_progress,
                coalesce(sum(winner='attackers'),0) AS attackers_wins,
                coalesce(sum(winner='defenders'),0) AS defenders_wins,
                coalesce(sum(winner='draw'),0) AS draws,
                coalesce(avg(CASE WHEN winner IS NOT NULL THEN plies END),0) AS average_game_length
                FROM records""",
                    (account["id"],),
                ).fetchone()
            )
            conditions, values = [], []
            for key, value in (("ruleset", ruleset), ("winner", winner)):
                if value is not None:
                    conditions.append(f"{key}=?")
                    values.append(value)
            if status:
                conditions.append(
                    "winner IS NOT NULL" if status == "completed" else "winner IS NULL"
                )
            rows, paging = page(
                c,
                base,
                (account["id"],),
                conditions,
                values,
                limit=limit,
                cursor=cursor,
                context=context_key(account["id"], "single-player", filters),
            )
            return {
                "category": "account-single-player",
                "games": [self._account_summary(r) for r in rows],
                "totals": totals,
                **paging,
            }

    def single_replay(self, credential, identifier):
        with self.store.connection() as c:
            c.execute("BEGIN")
            account = self.online._identity(c, credential)
            row = c.execute(
                "SELECT data FROM games WHERE id=? AND owner_account_id=?",
                (identifier, account["id"]),
            ).fetchone()
            if row is None:
                raise OnlineError(404, "Account game not found.")
            game = json.loads(row[0])
            replay = self.account_replay(game)
            # Reuse the established retained-result semantics, independent of cursor.
            result = self.outcome(game)
            summary = {
                "id": game["id"],
                "category": "account-single-player",
                "ruleset": game["ruleset"],
                "mode": game["mode"],
                "created_at": game["created_at"],
                "ended_at": game.get("ended_at"),
                "status": "completed" if result["winner"] else "in_progress",
                "winner": result["winner"],
                "plies": result["plies"],
                "players": {
                    side: {"name": str(p["name"])[:80], "kind": p["kind"]}
                    for side, p in game["players"].items()
                },
            }
            return {
                **replay,
                "states": [
                    {k: v for k, v in state.items() if k != "history"} for state in replay["states"]
                ],
                "game": summary,
                "moves": [{"source": m["source"], "target": m["target"]} for m in game["moves"]],
                "version": game["version"],
            }

    def online_games(
        self,
        credential,
        *,
        limit=25,
        cursor=None,
        ruleset=None,
        section="current",
        status=None,
        outcome=None,
    ):
        filters = {"ruleset": ruleset, "section": section, "status": status, "outcome": outcome}
        with self.store.connection() as c:
            c.execute("BEGIN")
            account = self.online._identity(c, credential)
            actor = account["id"]
            now = micros(self.online.clock())
            base = """SELECT g.id,g.match_id,g.leg,g.creator_id,g.attackers_id,g.defenders_id,
                g.ruleset,g.status,g.version,g.created_at,g.updated_at,g.deadline,m.mode,
                CASE WHEN g.status IN ('completed','cancelled','voided') THEN g.updated_at ELSE g.created_at END AS order_at,
                json_extract(g.position,'$.turn') AS turn,
                (g.status='active' AND g.deadline>? AND
                  CASE json_extract(g.position,'$.turn') WHEN 'attackers' THEN g.attackers_id ELSE g.defenders_id END=?) AS your_turn,
                coalesce(r.competitive_eligible,0) AS eligible,
                json_extract(r.payload,'$.winner') AS winner,
                json_extract(r.payload,'$.termination_reason') AS reason,
                r.abandoned_account_id,r.revision,
                substr(coalesce(a.display_name,a.username),1,80) AS attacker_name,
                substr(coalesce(d.display_name,d.username),1,80) AS defender_name,
                CASE WHEN g.status='completed' AND r.competitive_eligible=1 THEN
                  CASE WHEN json_extract(r.payload,'$.winner')='draw' THEN 'draw'
                    WHEN CASE WHEN g.attackers_id=? THEN 'attackers' ELSE 'defenders' END=json_extract(r.payload,'$.winner') THEN 'win'
                    ELSE 'loss' END ELSE NULL END AS outcome
                FROM online_games g JOIN online_matches m ON m.id=g.match_id
                LEFT JOIN online_results r ON r.game_id=g.id AND r.revision=g.result_revision
                LEFT JOIN accounts a ON a.id=g.attackers_id LEFT JOIN accounts d ON d.id=g.defenders_id
                WHERE g.creator_id=? OR g.attackers_id=? OR g.defenders_id=? OR m.opponent_id=?"""
            params = (now, actor, actor, actor, actor, actor, actor)
            overview = dict(
                c.execute(
                    f"""WITH records AS ({base}) SELECT
                coalesce(sum(your_turn),0) AS your_turn,
                coalesce(sum(status='active' AND NOT your_turn),0) AS other_active,
                coalesce(sum(status='waiting' AND creator_id=?),0) AS waiting,
                coalesce(sum(status='completed'),0) AS completed,
                coalesce(sum(status='cancelled'),0) AS cancelled,
                coalesce(sum(status='voided'),0) AS voided FROM records""",
                    (*params, actor),
                ).fetchone()
            )
            conditions, values = [], []
            sections = {
                "current": "status IN ('waiting','active')",
                "your_turn": "your_turn=1",
                "other_active": "status='active' AND your_turn=0",
                "waiting": "status='waiting' AND creator_id=?",
                "history": "status IN ('completed','cancelled','voided')",
            }
            conditions.append("(" + sections[section] + ")")
            if section == "waiting":
                values.append(actor)
            for key, value in (("ruleset", ruleset), ("status", status)):
                if value is not None:
                    conditions.append(f"{key}=?")
                    values.append(value)
            if outcome == "abandoned":
                conditions.append("status='completed' AND eligible=1 AND abandoned_account_id=?")
                values.append(actor)
            elif outcome:
                conditions.append("outcome=?")
                values.append(outcome)
            rows, paging = page(
                c,
                base,
                params,
                conditions,
                values,
                limit=limit,
                cursor=cursor,
                context=context_key(actor, "online", filters),
                online=True,
            )
            match_ids = sorted({r["match_id"] for r in rows})
            siblings: dict[str, list[dict]] = {}
            if match_ids:
                placeholders = ",".join("?" for _ in match_ids)
                for item in c.execute(
                    f"SELECT match_id,id,leg,status,version FROM online_games WHERE match_id IN ({placeholders}) ORDER BY leg",
                    match_ids,
                ):
                    siblings.setdefault(item["match_id"], []).append(
                        {k: item[k] for k in ("id", "leg", "status", "version")}
                    )
            games = []
            for row in rows:
                side = (
                    "attackers"
                    if row["attackers_id"] == actor
                    else "defenders"
                    if row["defenders_id"] == actor
                    else None
                )
                games.append(
                    {
                        "id": row["id"],
                        "category": "online",
                        "ruleset": row["ruleset"],
                        "status": row["status"],
                        "version": row["version"],
                        "turn": row["turn"],
                        "own_side": side,
                        "your_turn": bool(row["your_turn"]),
                        "overdue": row["status"] == "active" and row["deadline"] <= now,
                        "deadline": stamp(instant(row["deadline"]))
                        if row["deadline"] is not None
                        else None,
                        "created_at": stamp(instant(row["created_at"])),
                        "updated_at": stamp(instant(row["updated_at"])),
                        "players": {
                            "attackers": row["attacker_name"],
                            "defenders": row["defender_name"],
                        },
                        "outcome": row["outcome"],
                        "abandoned": bool(
                            row["eligible"]
                            and row["status"] == "completed"
                            and row["abandoned_account_id"] == actor
                        ),
                        "match": {
                            "id": row["match_id"],
                            "mode": row["mode"],
                            "leg": row["leg"],
                            "games": siblings[row["match_id"]],
                        },
                        "result": {
                            "winner": row["winner"],
                            "reason": row["reason"],
                            "revision": row["revision"],
                            "eligible": bool(row["eligible"]),
                        }
                        if row["revision"] is not None
                        else None,
                    }
                )
            return {
                "category": "online",
                "games": games,
                "overview": overview,
                "totals": self.online._statistics(c, actor),
                "as_of": stamp(instant(now)),
                **paging,
            }
