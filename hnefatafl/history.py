"""Authorized Hall queries, distinct from the always-personal dashboard APIs."""

import json

from .online_core import OnlineError, instant, stamp
from .personal import context_key, page
from .security_context import allowed


class RecordedHistory:
    def __init__(self, personal):
        self.personal = personal
        self.online, self.store = personal.online, personal.store

    @staticmethod
    def _base(actor, *, admin, own):
        # Project summaries only. No boards, engine replay, config, email or audit notes.
        account = """SELECT games.id,'account-single-player' AS category,
            CAST((julianday(coalesce(json_extract(data,'$.ended_at'),games.created_at))-2440587.5)*86400000000 AS INTEGER) AS order_at,
            games.created_at,coalesce(json_extract(data,'$.ended_at'),games.created_at) AS recorded_at,
            json_extract(data,'$.ruleset') AS ruleset,
            CASE WHEN json_extract(data,'$.recorded_result.winner') IS NULL THEN 'in_progress' ELSE 'completed' END AS status,
            json_extract(data,'$.recorded_result.winner') AS winner,
            (json_extract(data,'$.recorded_result.winner') IS NOT NULL) AS eligible,
            substr(json_extract(data,'$.players.attackers.name'),1,80) AS attackers,
            substr(json_extract(data,'$.players.defenders.name'),1,80) AS defenders,
            substr(coalesce(a.display_name,a.username),1,80) AS owner
            FROM games JOIN accounts a ON a.id=games.owner_account_id"""
        online = """SELECT g.id,'online' AS category,g.updated_at AS order_at,
            g.created_at,g.updated_at AS recorded_at,g.ruleset,g.status,
            json_extract(r.payload,'$.winner') AS winner,
            coalesce(r.competitive_eligible,0) AS eligible,
            substr(coalesce(a.display_name,a.username),1,80) AS attackers,
            substr(coalesce(d.display_name,d.username),1,80) AS defenders,
            NULL AS owner
            FROM online_games g JOIN online_matches m ON m.id=g.match_id
            LEFT JOIN online_results r ON r.game_id=g.id AND r.revision=g.result_revision
            LEFT JOIN accounts a ON a.id=g.attackers_id
            LEFT JOIN accounts d ON d.id=g.defenders_id"""
        params: tuple = ()
        if not admin or own:
            online += (
                " WHERE g.creator_id=? OR g.attackers_id=? OR g.defenders_id=? OR m.opponent_id=?"
            )
            params = (actor,) * 4
        if admin:
            if own:
                account += " WHERE games.owner_account_id=?"
                params = (*params, actor)
            return online + " UNION ALL " + account, params
        return online, params

    def listing(
        self,
        credential,
        *,
        limit=25,
        cursor=None,
        category=None,
        ruleset=None,
        player=None,
        status=None,
        winner=None,
        dashboard=False,
    ):
        with self.store.connection() as c:
            c.execute("BEGIN")
            account = self.online._identity(c, credential)
            admin = allowed(account, "all_data")
            if not admin and (category == "account-single-player" or player):
                raise OnlineError(403, "Administrator history permission is required.")
            base, params = self._base(account["id"], admin=admin, own=dashboard)
            conditions, values = [], []
            for key, value in (
                ("category", category),
                ("ruleset", ruleset),
                ("status", status),
                ("winner", winner),
            ):
                if value:
                    conditions.append(key + "=?")
                    values.append(value)
            if player:
                # Literal substring matching; SQL LIKE wildcard input is not a capability.
                conditions.append(
                    "(instr(lower(coalesce(attackers,'')),lower(?))>0 OR instr(lower(coalesce(defenders,'')),lower(?))>0 OR instr(lower(coalesce(owner,'')),lower(?))>0)"
                )
                values.extend((player,) * 3)
            if dashboard:
                conditions.append("status='completed' AND eligible=1")
                limit, cursor = 3, None
            filters = [category, ruleset, player, status, winner, dashboard, admin]
            rows, paging = page(
                c,
                base,
                params,
                conditions,
                values,
                limit=limit,
                cursor=cursor,
                context=context_key(account["id"], "recorded-history", filters),
                online=True,
            )
            where = " AND ".join(conditions) or "1"
            totals = [
                dict(r)
                for r in c.execute(
                    f"""WITH records AS ({base}) SELECT category,count(*) AS games,
                coalesce(sum(status='completed' AND eligible=1),0) AS completed,
                coalesce(sum(status='completed' AND eligible=1 AND winner='attackers'),0) AS attackers_wins,
                coalesce(sum(status='completed' AND eligible=1 AND winner='defenders'),0) AS defenders_wins,
                coalesce(sum(status='completed' AND eligible=1 AND winner='draw'),0) AS draws
                FROM records WHERE {where} GROUP BY category ORDER BY category""",
                    (*params, *values),
                )
            ]
            games = []
            for row in rows:
                game = dict(row)
                game.pop("order_at")
                game["eligible"] = bool(game["eligible"])
                if game["category"] == "online":
                    for key in ("created_at", "recorded_at"):
                        game[key] = stamp(instant(game[key]))
                games.append(game)
            return {"games": games, "totals": totals, **paging}

    def review(self, credential, category, identifier):
        with self.store.connection() as c:
            c.execute("BEGIN")
            account = self.online._identity(c, credential)
            admin = allowed(account, "all_data")
            if category == "online":
                if not admin:
                    self.online._participant(c, account["id"], identifier)
                return self.online._replay(c, identifier)
            if not admin:
                raise OnlineError(404, "Recorded game not found.")
            row = c.execute("SELECT data FROM games WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise OnlineError(404, "Recorded game not found.")
            return self.personal._single_replay(json.loads(row[0]))
