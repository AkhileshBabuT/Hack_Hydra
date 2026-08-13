"""Deterministic integer node identity.

HydraDB node ids must be non-negative integers and MERGE matches on id alone,
so every string identity in the schema is hashed to an int. The canonical
string is kept as a property for display and as an MSpaths selector.

This is what makes MERGE idempotent with no id allocator and no lookup round
trip: the same logical entity computes the same id in any process, on any run.
"""

import hashlib

# 60 bits. Wide enough that collisions are not a practical concern at benchmark
# scale (~1e5 nodes -> ~1e-9), narrow enough to stay well inside an i64 so no
# transport or storage layer has to think about sign.
_BITS = 60
_SHIFT = 64 - _BITS


def nid(kind: str, key: str) -> int:
    """Stable non-negative int id for a namespaced string key."""
    digest = hashlib.sha256(f"{kind}:{key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") >> _SHIFT


def entity_id(instance_id: str, entity_key: str) -> int:
    return nid("E", f"{instance_id}|{entity_key}")


def fact_id(idempotency_key: str) -> int:
    return nid("F", idempotency_key)


def session_id(instance_id: str, session_key: str) -> int:
    return nid("S", f"{instance_id}|{session_key}")


def edge_id(src: int, edge_type: str, dst: int) -> int:
    return nid("R", f"{src}|{edge_type}|{dst}")


def idempotency_key(*parts: str) -> str:
    """Content hash for a mutation, safe as HydraDB tx metadata.

    HydraDB caps the key at 128 chars and accepts only [A-Za-z0-9._-], so the
    hex digest is used directly rather than the raw joined parts.
    """
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
