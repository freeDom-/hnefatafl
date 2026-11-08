"""Authorized Hall queries, distinct from the always-personal dashboard APIs."""

import json

from .account_policy import account_player_sql, attributed_name
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
        # Current account writers use UTC datetime.isoformat(): either six
        # fractional digits or whole seconds. Parse seconds without the fraction;
        # julianday/strftime on fractional dates round to milliseconds.
        at = "coalesce(json_extract(data,'$.ended_at'),games.created_at)"
        attacker_name, attacker_username = account_player_sql("attackers")
        defender_name, defender_username = account_player_sql("defenders")
        account = f"""SELECT games.id,'account-single-player' AS category,
            CAST(strftime('%s',substr({at},1,19)) AS INTEGER)*1000000 +
            CASE WHEN substr({at},20,1)='.' THEN CAST(substr({at},21,6) AS INTEGER) ELSE 0 END AS order_at,
            games.created_at,coalesce(json_extract(data,'$.ended_at'),games.created_at) AS recorded_at,
            json_extract(data,'$.ruleset') AS ruleset,
            CASE WHEN json_extract(data,'$.recorded_result.winner') IS NULL THEN 'in_progress' ELSE 'completed' END AS status,
            json_extract(data,'$.recorded_result.winner') AS winner,
            (json_extract(data,'$.recorded_result.winner') IS NOT NULL) AS eligible,
            {attacker_name} AS attackers,{defender_name} AS defenders,
            public_account_name(a.username,a.display_name,a.show_display_name) AS owner,
            {attacker_username} AS attacker_username,{defender_username} AS defender_username,a.username AS owner_username,
            NULL AS attacker_rating,
            NULL AS defender_rating,
            NULL AS winner_delta
            FROM games LEFT JOIN accounts a ON a.id=games.owner_account_id"""
        online = """SELECT g.id,'online' AS category,g.updated_at AS order_at,
            g.created_at,g.updated_at AS recorded_at,g.ruleset,g.status,
            json_extract(r.payload,'$.winner') AS winner,
            coalesce(r.competitive_eligible,0) AS eligible,
            public_account_name(a.username,a.display_name,a.show_display_name) AS attackers,
            public_account_name(d.username,d.display_name,d.show_display_name) AS defenders,
            NULL AS owner,
            a.username AS attacker_username,d.username AS defender_username,NULL AS owner_username,
            (SELECT s.rating FROM online_game_start_ratings s
              WHERE s.game_id=g.id AND s.account_id=g.attackers_id) AS attacker_rating,
            (SELECT s.rating FROM online_game_start_ratings s
              WHERE s.game_id=g.id AND s.account_id=g.defenders_id) AS defender_rating,
            (SELECT ra.delta FROM rating_applications ra
              WHERE ra.game_id=g.id AND ra.account_id = CASE json_extract(r.payload,'$.winner')
                WHEN 'attackers' THEN g.attackers_id WHEN 'defenders' THEN g.defenders_id END
            ) AS winner_delta
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
        if admin and not own:
            # Trusted current all_data sees every server record.
            return online + " UNION ALL " + account, params
        # Normal users and own-scoped administrators also see their own
        # account single-player records, never another account's. AI-vs-AI games
        # are admin analysis artifacts and stay out of personal history; the
        # trusted all_data Hall still exposes them.
        account += (
            " WHERE games.owner_account_id=?"
            " AND coalesce(json_extract(games.data,'$.mode'),'') <> 'ai-ai'"
        )
        params = (*params, actor)
        return online + " UNION ALL " + account, params

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
            if not admin and player:
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
                # Search the same public label returned below, without duplicating
                # its formatting or performing any additional database queries.
                c.create_function("attributed_name", 2, attributed_name, deterministic=True)
                conditions.append(
                    "("
                    + " OR ".join(
                        f"instr(lower(coalesce({field},'')),lower(?))>0"
                        for field in (
                            "attributed_name(attackers,attacker_username)",
                            "attributed_name(defenders,defender_username)",
                            "attributed_name(owner,owner_username)",
                            "attacker_username",
                            "defender_username",
                            "owner_username",
                        )
                    )
                    + ")"
                )
                values.extend((player,) * 6)
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
                for field, username_field in (
                    ("attackers", "attacker_username"),
                    ("defenders", "defender_username"),
                    ("owner", "owner_username"),
                ):
                    game[field] = attributed_name(game[field], game.pop(username_field))
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
            # Account single-player review: owners and administrators only.
            if admin:
                row = c.execute("SELECT data FROM games WHERE id=?", (identifier,)).fetchone()
            else:
                row = c.execute(
                    "SELECT data FROM games WHERE id=? AND owner_account_id=?",
                    (identifier, account["id"]),
                ).fetchone()
            if row is None:
                raise OnlineError(404, "Recorded game not found.")
            return self.personal._single_replay(c, json.loads(row[0]))
