"""Private query-derived history; no game writes or accounting tables."""

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
    def __init__(self, online, *, replay):
        self.online, self.store = online, online.store
        self.account_replay = replay

    @staticmethod
    def _account_base():
        return """SELECT id,created_at AS order_at,created_at,
            json_extract(data,'$.ruleset') AS ruleset,
            json_extract(data,'$.mode') AS mode,
            json_extract(data,'$.human_side') AS human_side,
            json_extract(data,'$.recorded_result.winner') AS winner,
            coalesce(json_extract(data,'$.recorded_result.plies'),json_array_length(data,'$.moves')) AS plies,
            json_extract(data,'$.ended_at') AS ended_at,
            substr(json_extract(data,'$.players.attackers.name'),1,80) AS attacker_name,
            substr(json_extract(data,'$.players.defenders.name'),1,80) AS defender_name,
            json_extract(data,'$.players.attackers.kind') AS attacker_kind,
            json_extract(data,'$.players.defenders.kind') AS defender_kind,
            json_extract(data,'$.players.attackers.account_id') AS attacker_account_id,
            json_extract(data,'$.players.defenders.account_id') AS defender_account_id
            FROM games WHERE owner_account_id=?
              AND coalesce(json_extract(data,'$.mode'),'') <> 'ai-ai'"""

    @staticmethod
    def _personal_side_case():
        # Only an account-bound human seat is authoritative. Legacy local
        # human-human records have no account_id and stay unattributed.
        return """CASE
            WHEN human_side='attackers' AND attacker_kind='human' AND attacker_account_id=?
              THEN 'attackers'
            WHEN human_side='defenders' AND defender_kind='human' AND defender_account_id=?
              THEN 'defenders'
            WHEN attacker_kind='human' AND attacker_account_id=?
                 AND (defender_kind IS NULL OR defender_kind<>'human')
              THEN 'attackers'
            WHEN defender_kind='human' AND defender_account_id=?
                 AND (attacker_kind IS NULL OR attacker_kind<>'human')
              THEN 'defenders'
            ELSE NULL END"""

    @staticmethod
    def _rate(wins, decided):
        return wins / decided if decided else None

    @classmethod
    def _account_statistics(cls, c, account_id):
        """Personal human-vs-AI outcomes from persisted results, by ruleset.

        AI-vs-AI games are excluded from personal history and statistics; they
        exist only for administrator AI-strength analysis. Each game has exactly
        one mode, so grouping by ruleset cannot double-count.
        """
        rows = c.execute(
            f"""WITH records AS ({cls._account_base()})
            SELECT ruleset,winner,plies,{cls._personal_side_case()} AS personal_side
            FROM records""",
            (account_id, account_id, account_id, account_id, account_id),
        ).fetchall()
        groups: dict[str, dict] = {}
        unattributed = 0
        for row in rows:
            side = row["personal_side"]
            if side is None:
                unattributed += 1
                continue
            group = groups.setdefault(row["ruleset"], cls._empty_group(row["ruleset"]))
            group["played"] += 1
            winner = row["winner"]
            if winner is None:
                continue
            group["completed"] += 1
            group["length_sum"] += row["plies"] or 0
            if winner == "draw":
                group["draws"] += 1
            elif winner == side:
                group["wins"] += 1
            else:
                group["losses"] += 1
            if side == "attackers":
                group["decided_attackers"] += 1
                if winner == side:
                    group["wins_attackers"] += 1
            else:
                group["decided_defenders"] += 1
                if winner == side:
                    group["wins_defenders"] += 1
        return {
            "groups": [cls._group_result(groups[ruleset]) for ruleset in sorted(groups)],
            "coverage": {"unattributed_games": unattributed},
        }

    @staticmethod
    def _empty_group(ruleset):
        return {
            "ruleset": ruleset,
            "mode": None,
            "played": 0,
            "completed": 0,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "wins_attackers": 0,
            "decided_attackers": 0,
            "wins_defenders": 0,
            "decided_defenders": 0,
            "length_sum": 0,
        }

    @classmethod
    def _group_result(cls, group):
        completed = group["completed"]
        return {
            "ruleset": group["ruleset"],
            "mode": None,
            "played": group["played"],
            "wins": group["wins"],
            "losses": group["losses"],
            "draws": group["draws"],
            "win_rate": cls._rate(group["wins"], group["wins"] + group["losses"] + group["draws"]),
            "win_rate_attackers": cls._rate(group["wins_attackers"], group["decided_attackers"]),
            "win_rate_defenders": cls._rate(group["wins_defenders"], group["decided_defenders"]),
            "average_game_length": (group["length_sum"] / completed) if completed else None,
            "average_game_length_unit": "moves",
        }

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
            base = self._account_base()
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
                "statistics": self._account_statistics(c, account["id"]),
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
            return self._single_replay(json.loads(row[0]))

    def _single_replay(self, game):
        replay = self.account_replay(game)
        # Canonical replay validates the board, never reconstructs result metadata.
        result = game.get("recorded_result") or {}
        winner = result.get("winner")
        summary = {
            "id": game["id"],
            "category": "account-single-player",
            "ruleset": game["ruleset"],
            "mode": game["mode"],
            "created_at": game["created_at"],
            "ended_at": game.get("ended_at"),
            "status": "completed" if winner else "in_progress",
            "winner": winner,
            "plies": result.get("plies", len(game["moves"])),
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

    @classmethod
    def _online_statistics(cls, c, account_id):
        """Current effective eligible results grouped only by ruleset.

        Each online game has exactly one match mode, so summing across modes per
        ruleset never double-counts a game. The optional rating comes from the
        separate rated-game pool and is omitted while the account is unrated.
        """
        rows = c.execute(
            """SELECT g.ruleset,pr.rating AS rating,
              json_extract(r.payload,'$.winner') AS winner,
              CASE WHEN g.attackers_id=? THEN 'attackers' ELSE 'defenders' END AS side,
              r.abandoned_account_id,
              coalesce(json_extract(r.payload,'$.move_count'),0) AS move_count
            FROM online_games g
            JOIN online_results r ON r.game_id=g.id AND r.revision=g.result_revision
            LEFT JOIN player_ratings pr ON pr.account_id=? AND pr.pool=g.ruleset
            WHERE g.status='completed' AND r.competitive_eligible=1
              AND (g.attackers_id=? OR g.defenders_id=?)""",
            (account_id, account_id, account_id, account_id),
        ).fetchall()
        groups: dict[str, dict] = {}
        for row in rows:
            group = groups.setdefault(
                row["ruleset"],
                {
                    "ruleset": row["ruleset"],
                    "rating": row["rating"],
                    "played": 0,
                    "wins": 0,
                    "losses": 0,
                    "draws": 0,
                    "timed_out": 0,
                    "wins_attackers": 0,
                    "decided_attackers": 0,
                    "wins_defenders": 0,
                    "decided_defenders": 0,
                    "length_sum": 0,
                },
            )
            group["played"] += 1
            group["length_sum"] += row["move_count"] or 0
            winner, side = row["winner"], row["side"]
            if winner == "draw":
                group["draws"] += 1
            elif winner == side:
                group["wins"] += 1
            else:
                group["losses"] += 1
            if row["abandoned_account_id"] == account_id:
                group["timed_out"] += 1
            if side == "attackers":
                group["decided_attackers"] += 1
                if winner == side:
                    group["wins_attackers"] += 1
            else:
                group["decided_defenders"] += 1
                if winner == side:
                    group["wins_defenders"] += 1
        result = []
        for ruleset in sorted(groups):
            group = groups[ruleset]
            played = group["played"]
            result.append(
                {
                    "ruleset": ruleset,
                    "mode": None,
                    "played": played,
                    "wins": group["wins"],
                    "losses": group["losses"],
                    "draws": group["draws"],
                    "timed_out": group["timed_out"],
                    "win_rate": cls._rate(group["wins"], played),
                    "win_rate_attackers": cls._rate(
                        group["wins_attackers"], group["decided_attackers"]
                    ),
                    "win_rate_defenders": cls._rate(
                        group["wins_defenders"], group["decided_defenders"]
                    ),
                    "average_game_length": group["length_sum"] / played if played else None,
                    "average_game_length_unit": "moves",
                    "rating": group["rating"],
                }
            )
        return {"groups": result, "coverage": {"unattributed_games": 0}}

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
                g.ruleset,g.status,g.version,g.created_at,g.updated_at,g.deadline,m.mode,m.rated,
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
                        "rated": bool(row["rated"]),
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
                "statistics": self._online_statistics(c, actor),
                "as_of": stamp(instant(now)),
                **paging,
            }
