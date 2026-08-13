"""Identity must be stable, non-negative, and identical across processes.

Cross-process stability is the property that actually matters: it is what lets
a resumed ingest in a fresh process MERGE onto the rows a crashed one wrote.
"""

import subprocess
import sys

from hydramem import ids


def test_nid_is_deterministic():
    assert ids.nid("E", "person:maya") == ids.nid("E", "person:maya")


def test_nid_is_non_negative_and_fits_in_60_bits():
    for key in ["person:maya", "org:acme", "", "unicode: é中文", "x" * 500]:
        value = ids.nid("E", key)
        assert 0 <= value < 2**60


def test_kind_namespaces_the_key():
    assert ids.nid("E", "same") != ids.nid("F", "same")


def test_instance_partitions_entities():
    assert ids.entity_id("a", "person:maya") != ids.entity_id("b", "person:maya")


def test_edge_id_is_directional():
    assert ids.edge_id(1, "SUBJECT", 2) != ids.edge_id(2, "SUBJECT", 1)


def test_edge_id_distinguishes_type():
    assert ids.edge_id(1, "SUBJECT", 2) != ids.edge_id(1, "OBJECT", 2)


def test_idempotency_key_fits_hydradb_limits():
    key = ids.idempotency_key("inst", "sess", "0", "employer", "person:maya", "Acme|x")
    assert len(key) <= 128
    assert all(c.isalnum() or c in "._-" for c in key)


def test_nid_is_stable_across_processes():
    """Same input, separate interpreter, same id. PYTHONHASHSEED must not matter."""
    code = "from hydramem import ids; print(ids.entity_id('inst-1', 'person:maya'))"
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert int(out.stdout.strip()) == ids.entity_id("inst-1", "person:maya")
