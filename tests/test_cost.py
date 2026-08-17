"""Slice 14: the histogram units, which are a factor-of-a-million trap.

HydraDB's admin endpoint serves `graph_client_operation_read_duration_seconds`
and `graph_query_rows_duration_microseconds` side by side, on the *same* bucket
ladder scaled by 1e6 -- `le="0.0001"` and `le="100"` are the same bound. Read
either as the other's unit and every latency number is wrong by six orders of
magnitude while the table still looks like a table.

The units are not a convention to be honoured, they are derivable: in
`crates/telemetry/src/meter.rs` the same `HistogramUnit` value picks both the
name suffix and the scaling (`render_bound`, `scale_sum`), so the suffix cannot
be wrong. That is what `client.histograms` reads, and this is what pins it.
"""

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from hydramem import client  # noqa: E402

import cost_table  # noqa: E402


# Real shapes, trimmed. Both histograms are on the same underlying microsecond
# ladder; only the export unit differs.
BODY = """\
# TYPE graph_client_operation_read_duration_seconds histogram
graph_client_operation_read_duration_seconds_bucket{le="0.0001"} 0
graph_client_operation_read_duration_seconds_bucket{le="+Inf"} 4
graph_client_operation_read_duration_seconds_sum 2.0
graph_client_operation_read_duration_seconds_count 4
# TYPE graph_query_rows_duration_microseconds histogram
graph_query_rows_duration_microseconds_bucket{scope="a",cell_id="c",le="100"} 3
graph_query_rows_duration_microseconds_sum{scope="a",cell_id="c"} 1000000
graph_query_rows_duration_microseconds_count{scope="a",cell_id="c"} 4
graph_query_rows_duration_microseconds_sum{scope="b",cell_id="c"} 1000000
graph_query_rows_duration_microseconds_count{scope="b",cell_id="c"} 4
# TYPE graph_gc_duration_microseconds counter
graph_gc_duration_microseconds 5
graph_vertices_total 12
"""


@pytest.fixture
def scraped(monkeypatch):
    class Fake(io.StringIO):
        def read(self):
            return BODY.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(client.urllib.request, "urlopen", lambda *a, **k: Fake())
    return client.histograms()


def test_a_seconds_histogram_is_scaled_up_to_milliseconds(scraped):
    # 2.0 s over 4 samples = 500 ms. Read as microseconds it would be 0.0005 ms.
    assert scraped["graph_client_operation_read_duration_seconds"]["mean_ms"] == 500.0


def test_a_microseconds_histogram_is_scaled_down_to_milliseconds(scraped):
    # 2,000,000 us over 8 samples = 250 ms. Read as seconds it would be 250,000,000 ms.
    assert scraped["graph_query_rows_duration_microseconds"]["mean_ms"] == 250.0


def test_the_two_units_do_not_collapse_into_one_number(scraped):
    """The whole point. Same ladder, different unit, and a single assumed unit
    would make one of these wrong by 1e6 without changing its shape."""
    units = {name: row["unit"] for name, row in scraped.items()}
    assert units["graph_client_operation_read_duration_seconds"] == "seconds"
    assert units["graph_query_rows_duration_microseconds"] == "microseconds"


def test_a_labelled_histogram_is_summed_over_its_label_sets(scraped):
    """`graph_query_rows_duration_microseconds` carries scope and cell_id, so
    every sample is labelled and a parser that skips labelled lines drops it
    entirely -- which is exactly what `client.metrics` does on purpose."""
    assert scraped["graph_query_rows_duration_microseconds"]["count"] == 8


def test_a_counter_is_not_read_as_a_histogram(scraped):
    """`CounterUnit` has no seconds variant by design. Counters are cumulative
    microsecond sums with no `_count` partner, so a mean from one is nonsense."""
    assert "graph_gc_duration" not in scraped
    assert not any("gc" in name for name in scraped)


def test_the_scalar_scrape_still_ignores_every_labelled_sample(monkeypatch):
    class Fake(io.StringIO):
        def read(self):
            return BODY.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(client.urllib.request, "urlopen", lambda *a, **k: Fake())
    scalars = client.metrics()
    assert scalars["graph_vertices_total"] == 12
    assert not any("{" in name for name in scalars)


# --- the harness side ------------------------------------------------------

_n = [0]


def qrow(arm="hydramem", latency=100, trips=3, prompt=10, completion=2, iid=None):
    if iid is None:
        _n[0] += 1
        iid = f"q{_n[0]}"
    return {"arm": arm, "instance_id": iid, "latency_ms": latency,
            "round_trips": trips, "prompt_tokens": prompt,
            "completion_tokens": completion}


def test_the_same_question_scored_twice_is_costed_once():
    """Two processes resuming one arm append it twice. Without the dedupe the
    table divides every per-question cost by the wrong n and still looks fine."""
    table = cost_table.query_cost([qrow(iid="same"), qrow(iid="same")])
    assert {r["metric"]: r["value"] for r in table}["questions"] == 1


def test_the_round_trip_distribution_is_reported_not_just_its_mean():
    """2 means the question was lost at gate 1 and 4 means gate 4 issued its
    call. An average of 3 describes neither."""
    table = cost_table.query_cost([qrow(trips=2), qrow(trips=4), qrow(trips=3)])
    metrics = {r["metric"]: r["value"] for r in table}
    assert metrics["bolt_round_trips_per_question"] == 3.0
    for cost in (2, 3, 4):
        assert metrics[f"questions_costing_{cost}_round_trips"] == 1


def test_the_tail_is_reported_alongside_the_median():
    table = cost_table.query_cost([qrow(latency=ms) for ms in range(1, 101)])
    metrics = {r["metric"]: r["value"] for r in table}
    assert metrics["median_latency"] < metrics["p95_latency"]


def test_each_arm_is_costed_separately():
    table = cost_table.query_cost([qrow(arm="hydramem", prompt=10),
                                   qrow(arm="full_context", prompt=100_000)])
    tokens = {(r["arm"], r["metric"]): r["value"] for r in table}
    assert tokens[("hydramem", "prompt_tokens_per_question")] == 10
    assert tokens[("full_context", "prompt_tokens_per_question")] == 100_000


def test_the_consistency_mode_rides_on_every_latency_row():
    """A latency without its consistency mode is not comparable to anything.
    Evaluation pins strong; the demo path runs causal."""
    table = cost_table.query_cost([qrow()])
    latency = [r for r in table if r["metric"].endswith("latency")]
    assert latency and all("strong" in r["note"] for r in latency)


def test_ingest_cost_is_per_fact_not_per_run():
    table = cost_table.ingest_cost([
        {"instance_id": "i1", "sessions": 2, "facts": 10, "entities": 3, "parse_failures": 0,
         "prompt_tokens": 900, "completion_tokens": 100, "round_trips": 9,
         "latency_ms": 3000},
    ])
    metrics = {r["metric"]: r["value"] for r in table}
    assert metrics["extraction_tokens_per_fact"] == 100
    assert metrics["bolt_round_trips_per_fact"] == 0.9
    assert metrics["facts_per_session"] == 5.0


def test_the_same_instance_ingested_twice_is_counted_once():
    """Two arms' processes racing on one instance, or a re-ingest after a wipe,
    would otherwise multiply every per-fact figure by the number of attempts --
    and the resulting table looks entirely reasonable."""
    one = {"instance_id": "i1", "sessions": 1, "facts": 10, "entities": 2,
           "parse_failures": 0, "prompt_tokens": 900, "completion_tokens": 100,
           "round_trips": 9, "latency_ms": 3000}
    table = cost_table.ingest_cost([one, dict(one)])
    metrics = {r["metric"]: r["value"] for r in table}
    assert metrics["instances_ingested"] == 1
    assert metrics["facts_written"] == 10


def test_an_empty_ingest_ledger_says_why_rather_than_reporting_zeroes():
    """Every instance already in the graph is the normal case on a rerun, and a
    table of zeroes there would read as a pipeline that writes nothing."""
    table = cost_table.ingest_cost([])
    assert "wiped node" in table[0]["note"]
