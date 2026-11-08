"""Editable private names cannot escape through existing online projections."""

from test_online_core import active, account_template as account_template, rig as rig


def test_online_participants_resolve_shared_names_without_changing_account_ids(rig):
    game = active(rig)
    account_id = rig.accounts["Archer"]["id"]
    with rig.store.connection() as c:
        c.execute("UPDATE accounts SET display_name='Private Þór' WHERE id=?", (account_id,))
    for shared in (False, True, False):
        with rig.store.connection() as c:
            c.execute("UPDATE accounts SET show_display_name=? WHERE id=?", (shared, account_id))
        current = rig.core.read(rig.credentials["Keeper"], game["id"])
        assert current["participants"]["attackers"] == {
            "account_id": account_id,
            "name": "Private Þór" if shared else "Archer",
        }
        assert current["version"] == game["version"]
