"""Bolt client: connection, bookmarks, consistency modes, metrics.

Two HydraDB behaviours the rest of the system leans on:

  - A write returns a bookmark. Handing that bookmark to a later causal read
    refreshes the reader until the write's storage sequence is visible before
    pinning the snapshot. That is read-your-own-writes as a server guarantee,
    which vector-store memory layers do not have.

  - Mutations carry an idempotency key in transaction metadata, used for
    durable write deduplication. It does *not* police conflicts on the Cypher
    path -- one key with two different payloads is accepted and both applied,
    verified against a live node. Uniqueness comes from the key being derived
    from the rows it sends (`ingest.batch_key`). The mapping below stays as a
    net in case a future build starts enforcing it.

Consistency is set per transaction: `strong` refreshes from object storage
before pinning (used by evaluation, so scores reproduce), `causal` uses the
node's current durable reader view (used by the demo, so latency is the real
hot path).
"""

import os
import urllib.request
from contextlib import contextmanager

import neo4j

DEFAULT_URI = os.environ.get("HYDRAMEM_BOLT_URI", "neo4j://127.0.0.1:7687")
DEFAULT_TOKEN = os.environ.get("HYDRAMEM_AUTH_TOKEN", "local-development-token-32-bytes")
DEFAULT_METRICS = os.environ.get("HYDRAMEM_METRICS_URL", "http://127.0.0.1:9090/metrics")

# HydraDB transaction-metadata keys. Not Neo4j conventions -- these are read by
# HydraDB's Bolt layer specifically.
TX_CONSISTENCY = "hydradb.consistency"
TX_IDEMPOTENCY_KEY = "hydradb.idempotency_key"


# Bolt round trips issued by this process. The cost story turns on this number
# and it only means anything if it is counted rather than estimated, so it is
# incremented where the round trip actually happens rather than tallied by
# whoever thinks they know how many they asked for.
#
# ponytail: a module-level int, not a per-driver counter or a metrics object.
# Every read and write in this codebase goes through this module and the harness
# is single threaded. Take a difference around the call you care about; make it
# per-driver when something concurrent needs it.
_round_trips = 0


def round_trips() -> int:
    return _round_trips


class IdempotencyConflict(RuntimeError):
    """A key was reused with different content.

    This means two distinct facts computed the same identity. It is a
    correctness bug, never a retry condition.
    """


def connect(uri: str = DEFAULT_URI, token: str = DEFAULT_TOKEN) -> neo4j.Driver:
    driver = neo4j.GraphDatabase.driver(uri, auth=neo4j.bearer_auth(token))
    driver.verify_connectivity()
    return driver


@contextmanager
def session(driver: neo4j.Driver, bookmarks=None):
    with driver.session(bookmarks=bookmarks) as s:
        yield s


def write(driver, statement, params=None, idempotency_key=None, bookmarks=None):
    """Run one mutation. Returns (records, bookmarks-after-write).

    HydraDB rejects explicit transactions -- each accepted mutation commits as
    one bounded server operation -- so this is an auto-commit RUN carrying its
    metadata on the query. Ingest is therefore idempotent batched writes, not
    transactional units, and must be safe under at-least-once delivery.
    """
    global _round_trips
    _round_trips += 1
    metadata = {TX_CONSISTENCY: "causal"}
    if idempotency_key is not None:
        metadata[TX_IDEMPOTENCY_KEY] = idempotency_key

    query = neo4j.Query(statement, metadata=metadata)
    with driver.session(bookmarks=bookmarks) as s:
        try:
            records = [r.data() for r in s.run(query, **(params or {}))]
        except neo4j.exceptions.Neo4jError as exc:
            if "idempotenc" in str(exc).lower():
                raise IdempotencyConflict(str(exc)) from exc
            raise
        return records, s.last_bookmarks()


def read(driver, statement, params=None, bookmarks=None, consistency="causal"):
    """Run one read. Passing the bookmark from a write gives read-your-writes."""
    global _round_trips
    _round_trips += 1
    query = neo4j.Query(statement, metadata={TX_CONSISTENCY: consistency})
    with driver.session(bookmarks=bookmarks) as s:
        return [r.data() for r in s.run(query, **(params or {}))]


def metrics(url: str = DEFAULT_METRICS) -> dict:
    """Scrape the admin endpoint into {metric_name: float}.

    Only unlabelled scalar samples are parsed. Histograms carry deliberately
    mixed units in HydraDB and are handled where they are actually used, not
    here -- guessing units centrally is how you get confidently wrong latency.
    """
    with urllib.request.urlopen(url, timeout=10) as response:
        body = response.read().decode("utf-8")

    out = {}
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, value = line.partition(" ")
        if "{" in name:
            continue
        try:
            out[name] = float(value)
        except ValueError:
            continue
    return out


# The unit is carried in the metric *name*, and that is authoritative rather
# than conventional. Verified in `hydra-db/hydradb` @ 6a2fbb1,
# `crates/telemetry/src/meter.rs`:
#
#   - The kernel measures in microseconds and *only* in microseconds.
#     `HistogramUnit` converts at the export boundary and nowhere else, and the
#     same enum value picks both the suffix and the scaling (`render_bound`,
#     `scale_sum`). So a `_seconds` series cannot be microseconds by accident.
#   - `HistogramUnit::Seconds` "exists for exactly one instrument":
#     `db.client.operation.duration`, whose OTel semantic convention fixes it in
#     seconds. Everything else keeps microseconds under a `hydradb.*` name.
#   - `CounterUnit` has *no* seconds variant, deliberately -- scaling a counter
#     would make it disagree with the `_microseconds` series rendered from the
#     same field, "and nothing downstream could detect the factor of a million".
#
# Which is why this reads the suffix instead of assuming one: mixing the two up
# is a 1,000,000x error that produces a plausible-looking table.
_UNIT_TO_MS = {"seconds": 1_000.0, "microseconds": 0.001, "milliseconds": 1.0}


def histograms(url: str = DEFAULT_METRICS) -> dict:
    """`{name: {unit, sum, count, mean_ms}}`, summed over label sets.

    `metrics()` above skips every labelled sample, which drops histograms
    entirely -- deliberately, because guessing their units centrally is how you
    get confidently wrong latency. This is the place they are actually used, so
    this is where the unit is resolved.
    """
    with urllib.request.urlopen(url, timeout=10) as response:
        body = response.read().decode("utf-8")

    found = {}
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, value = line.partition(" ")
        base = name.split("{", 1)[0]
        for suffix in ("_sum", "_count"):
            if not base.endswith(suffix):
                continue
            metric = base[: -len(suffix)]
            unit = metric.rsplit("_", 1)[-1]
            if unit not in _UNIT_TO_MS:
                continue
            try:
                found.setdefault(metric, {"unit": unit, "sum": 0.0, "count": 0.0})
                found[metric][suffix[1:]] += float(value)
            except ValueError:
                pass

    for metric, row in found.items():
        scale = _UNIT_TO_MS[row["unit"]]
        row["mean_ms"] = round(row["sum"] * scale / row["count"], 3) if row["count"] else 0.0
    return found
