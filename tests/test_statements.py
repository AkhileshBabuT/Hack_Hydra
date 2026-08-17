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
    # A statement assembled at call time (`MS_PATHS`, whose anchor list cannot
    # be a parameter) is registered in its *filled* form, which is the form the
    # parser has to accept. It is covered when some registered statement shares
    # its literal prefix -- the text up to the first substitution.
    templates = {name for name in exported if "{" in getattr(statements, name)}
    missing = [
        name
        for name in exported - templates
        if getattr(statements, name).strip() not in registered
    ]
    assert not missing, f"statements not in INVENTORY: {sorted(missing)}"

    for name in templates:
        head = getattr(statements, name).split("{", 1)[0].strip()
        assert any(statement.startswith(head) for statement in registered), \
            f"{name} is assembled but no assembled form is probed"


# Instance-scoped reads that join. Each must filter a single-node scan *before*
# the join, not inside a WHERE attached to the whole pattern.
JOINING_READS = [
    "FACTS_FOR_INSTANCE", "ALIASES_FOR_INSTANCE",
    "SUPERSESSION_CHAIN_FOR_INSTANCE", "COUNT_EDGES_SUPERSEDES",
    "COUNT_EDGES_SUBJECT",
]


@pytest.mark.parametrize("name", JOINING_READS)
def test_an_instance_read_filters_before_it_joins(name):
    """Clause order is the only performance lever this database gives us.

    Written as one pattern with the filter in the WHERE, HydraDB builds the join
    across every tenant in the store and filters afterwards, so latency scales
    with the whole store rather than with the tenant being read. Measured on a
    store of 2,122 Fact nodes: `FACTS_FOR_INSTANCE` took **7,635 ms to return 11
    rows**. Splitting the MATCH so the automatic property index on `instance_id`
    drives a single-node scan first returned the same 11 rows in **250 ms**, and
    the whole fixture suite went from 228s back to under 10s.

    There is no index DDL and `EXPLAIN` is unreachable over Bolt, so nothing else
    would catch a regression here -- the query keeps returning correct rows, just
    slower and slower as the store fills. Hence a shape assertion.
    """
    statement = getattr(statements, name)
    clauses = [line for line in statement.splitlines()
               if line.strip().upper().startswith(("MATCH", "WHERE"))]
    assert len(clauses) >= 3, f"{name} does not split its MATCH:\n{statement}"
    assert clauses[0].strip().upper().startswith("MATCH")
    assert clauses[1].strip().upper().startswith("WHERE"), (
        f"{name} must filter on instance_id before joining:\n{statement}")
    assert "instance_id" in clauses[1]
    assert clauses[2].strip().upper().startswith("MATCH")
    # The first MATCH must be a bare node, not already a pattern.
    assert "-[" not in clauses[0], (
        f"{name} joins in its first MATCH, so the filter cannot drive:\n{statement}")
