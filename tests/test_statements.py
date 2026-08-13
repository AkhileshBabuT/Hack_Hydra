"""Every statement in the inventory must survive HydraDB's parser.

This is the test that would have caught `ON CREATE SET`, `WHERE ... IN`, string
node ids and the multi-clause UNWIND write before any of them cost a day.
It runs against a live node because `EXPLAIN` is reachable only through
HydraDB's in-process shard API, not over Bolt or HTTP.
"""

import pytest

from hydramem import client, statements


@pytest.mark.parametrize("name", sorted(statements.INVENTORY))
def test_statement_is_accepted(driver, name):
    statement, params = statements.INVENTORY[name]
    try:
        client.read(driver, statement, params)
    except Exception as exc:  # noqa: BLE001 - we want the parser message verbatim
        pytest.fail(f"{name} rejected by HydraDB:\n{exc}")


def test_inventory_covers_every_exported_statement():
    """A statement defined but not registered is a statement nobody verifies."""
    exported = {
        name
        for name in dir(statements)
        if name.isupper() and isinstance(getattr(statements, name), str)
    }
    registered = {
        statement.strip() for statement, _ in statements.INVENTORY.values()
    }
    missing = [
        name
        for name in exported
        if getattr(statements, name).strip() not in registered
    ]
    assert not missing, f"statements not in INVENTORY: {sorted(missing)}"
