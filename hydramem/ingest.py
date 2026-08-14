"""Extracted facts -> graph rows -> batched writes.

The row builder is a pure function of (instance, extractions). Everything that
can be got wrong -- entity keys, alias pairs, deterministic ids, the temporal
axes -- is decided there and tested without a database, so an ingest bug is a
failing unit test rather than an afternoon of graph archaeology.

Writes are batched UNWIND statements, one per node label and one per edge type,
in dependency order: HydraDB aborts an edge batch whole if an endpoint does not
exist yet.
"""

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from pydantic import ValidationError

from . import chain, client, extract, ids, statements

BATCH = 500

# The corpus is a first-person chat log, so most subjects are the user under
# whatever surface form the extractor picked. They are one entity.
SELF_FORMS = frozenset(["user", "i", "me", "myself", "the user", "my"])
SELF_KEY = "person:user"

# Entity type for an entity-valued object, by predicate. Everything else is a
# thing -- getting this wrong costs display quality, never retrievability.
OBJECT_TYPES = {
    "employer": "org", "subscribes_to": "org",
    "lives_in": "place", "hometown": "place", "address": "place",
    "family_relation": "person",
    "attended": "event", "scheduled": "event",
}

_HONORIFICS = re.compile(r"^(mr|mrs|ms|miss|dr|prof|sir)\.?\s+", re.I)
_PUNCT = re.compile(r"[^\w\s-]")
_WS = re.compile(r"\s+")

# Date shapes the extractor actually emits in valid_from_hint. Anything vaguer
# ("last summer") falls back to the session timestamp: a wrong precise date is
# worse than an honest approximate one, since the temporal gate filters on it.
_DATE_PATTERNS = (
    (re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$"), ("y", "m", "d")),
    (re.compile(r"^(\d{4})[-/](\d{1,2})$"), ("y", "m")),
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), ("m", "d", "y")),
    (re.compile(r"^(\d{1,2})/(\d{1,2})$"), ("m", "d")),
    (re.compile(r"^(\d{4})$"), ("y",)),
)


def normalize(text: str) -> str:
    """Surface form -> comparison key. Cheap on purpose."""
    text = _HONORIFICS.sub("", text.strip().lower())
    return _WS.sub(" ", _PUNCT.sub(" ", text)).strip()


def entity_key(entity_type: str, name: str) -> str:
    normalized = normalize(name)
    if normalized in SELF_FORMS:
        return SELF_KEY
    return f"{entity_type}:{normalized}"


def resolve_valid_from(hint, session_ts: int) -> int:
    """When the fact became true in the world, in epoch seconds.

    Falls back to the assertion time, which is the honest default: a fact
    volunteered without a date is known true no later than when it was said.
    """
    if not hint:
        return session_ts
    text = hint.strip()
    said = dt.datetime.fromtimestamp(session_ts, dt.timezone.utc)
    for pattern, order in _DATE_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        parts = dict(zip(order, (int(g) for g in match.groups())))
        year, month, day = parts.get("y", said.year), parts.get("m", 1), parts.get("d", 1)
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return session_ts
        try:
            when = dt.datetime(year, month, day, tzinfo=dt.timezone.utc)
        except ValueError:
            return session_ts
        # "3/22" said in January means last year, not nine months from now.
        if "y" not in parts and when > said + dt.timedelta(days=1):
            when = when.replace(year=year - 1)
        return int(when.timestamp())
    return session_ts


@dataclass
class Rows:
    """One ingest's worth of write payloads, grouped by statement."""

    sessions: list = field(default_factory=list)
    entities: list = field(default_factory=list)
    facts: list = field(default_factory=list)
    subject: list = field(default_factory=list)
    object: list = field(default_factory=list)
    asserted_in: list = field(default_factory=list)
    next: list = field(default_factory=list)
    alias: list = field(default_factory=list)
    close: list = field(default_factory=list)        # derived, see chain.py
    supersedes: list = field(default_factory=list)   # derived, see chain.py

    def counts(self) -> dict:
        return {
            "sessions": len(self.sessions), "entities": len(self.entities),
            "facts": len(self.facts), "subject": len(self.subject),
            "object": len(self.object), "asserted_in": len(self.asserted_in),
            "next": len(self.next), "alias": len(self.alias),
            "close": len(self.close), "supersedes": len(self.supersedes),
        }


def alias_pairs(entities: list) -> list:
    """Near-identical surface forms, as (alias_key, canonical_key) pairs.

    An alias edge, never a merge: edges are reversible and auditable, a bad
    merge is neither. "maya" -> "maya chen" is exactly the case that matters,
    and it is a containment check, not a clustering problem.

    ponytail: O(n^2) over one instance's entities (tens). If a tenant ever holds
    thousands, block by type and first token before comparing.
    """
    pairs = []
    for a in entities:
        for b in entities:
            if a["key"] == b["key"] or a["type"] != b["type"]:
                continue
            left, right = normalize(a["name"]), normalize(b["name"])
            if not left or not right or len(left) >= len(right):
                continue
            contained = set(left.split()) < set(right.split())
            similar = SequenceMatcher(None, left, right).ratio() > 0.92
            if contained or similar:
                pairs.append((a["key"], b["key"]))
    return sorted(set(pairs))


def build_rows(instance, extractions: list) -> Rows:
    """(session, facts) pairs -> every row the write path needs. Pure.

    Ids are content-derived, so the same input builds the same rows in any
    process, in any batch grouping, in any order. That is the first of the two
    idempotency layers; the mutation key is the second.
    """
    inst = instance.instance_id
    rows = Rows()
    entities = {}

    def touch(key: str, name: str, entity_type: str, session_idx: int) -> int:
        vid = ids.entity_id(inst, key)
        seen = entities.get(key)
        if seen is None:
            entities[key] = {
                "vid": vid, "key": key, "name": name, "type": entity_type,
                "first_seen": session_idx, "last_seen": session_idx,
                "instance_id": inst,
            }
        else:
            # first_seen is the min over the whole instance, computed here rather
            # than left to write order: the graph's create-only guard pins
            # whatever lands first, and only this makes that value the right one.
            seen["first_seen"] = min(seen["first_seen"], session_idx)
            seen["last_seen"] = max(seen["last_seen"], session_idx)
        return vid

    session_vids = []
    for session, facts in extractions:
        svid = ids.session_id(inst, session.session_id)
        session_vids.append(svid)
        rows.sessions.append({
            "vid": svid, "key": session.session_id, "idx": session.idx,
            "timestamp": session.timestamp, "instance_id": inst,
        })

        for fact in facts:
            subject_key = entity_key(fact.subject_type, fact.subject)
            subject_vid = touch(subject_key, fact.subject, fact.subject_type, session.idx)

            key = ids.idempotency_key(
                inst, session.session_id, str(fact.turn_idx),
                fact.predicate, subject_key, fact.value,
            )
            fvid = ids.fact_id(key)
            turn = session.turns[fact.turn_idx] if fact.turn_idx < len(session.turns) else None
            snippet = (fact.evidence_span or (turn.content if turn else ""))[: extract.MAX_SPAN]

            rows.facts.append({
                "vid": fvid,
                # 16 hex chars is what the answering model has to copy back as a
                # citation. The full key stays the identity; this is the handle.
                "fact_id": key[:16],
                "predicate": fact.predicate,
                "value_text": fact.value,
                "value_type": "entity" if fact.value_is_entity else "literal",
                "valid_from": resolve_valid_from(fact.valid_from_hint, session.timestamp),
                "valid_to": 0,                      # open until chain.py closes it
                "asserted_at": session.timestamp,
                "session_id": session.session_id,
                "turn_idx": fact.turn_idx,
                "snippet": snippet,
                "confidence": float(fact.confidence),
                "status": "current",                # until chain.py supersedes it
                "valid_from_hint": fact.valid_from_hint or "",
                "instance_id": inst,
            })
            rows.subject.append({
                "fid": fvid, "eid": subject_vid,
                "rid": ids.edge_id(fvid, "SUBJECT", subject_vid), "instance_id": inst,
            })
            rows.asserted_in.append({
                "fid": fvid, "sid": svid,
                "rid": ids.edge_id(fvid, "ASSERTED_IN", svid), "instance_id": inst,
            })
            if fact.value_is_entity:
                object_type = OBJECT_TYPES.get(fact.predicate, "thing")
                object_key = entity_key(object_type, fact.value)
                object_vid = touch(object_key, fact.value, object_type, session.idx)
                rows.object.append({
                    "fid": fvid, "eid": object_vid,
                    "rid": ids.edge_id(fvid, "OBJECT", object_vid), "instance_id": inst,
                })

    rows.entities = list(entities.values())
    rows.next = [
        {"src": a, "dst": b, "rid": ids.edge_id(a, "NEXT", b), "instance_id": inst}
        for a, b in zip(session_vids, session_vids[1:])
    ]
    rows.alias = [
        {
            "src": entities[alias]["vid"], "dst": entities[canonical]["vid"],
            "rid": ids.edge_id(entities[alias]["vid"], "ALIAS_OF", entities[canonical]["vid"]),
            "instance_id": inst,
        }
        for alias, canonical in alias_pairs(rows.entities)
    ]
    # Derived last, from the rows just built: the chain is a function of the
    # fact set, so it is permutation-invariant by construction rather than by
    # write ordering.
    rows.close, rows.supersedes = chain.materialize(rows, inst)
    return rows


def batch_key(name: str, rows: list) -> str:
    """Mutation idempotency key: <=128 chars of [A-Za-z0-9._-], content-derived.

    Content-derived is the point, and it is the *only* thing making the key
    meaningful: HydraDB does not reject a reused key carrying different rows
    (verified live). Hashing the payload means one key cannot name two payloads.
    """
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()
    return f"{name}.{digest}"


# Statement per payload, in dependency order. Nodes before edges is not a style
# choice: an edge batch with a missing endpoint is rejected whole.
WRITE_PLAN = (
    ("sessions", "upsert_session", statements.UPSERT_SESSION),
    ("entities", "upsert_entity", statements.UPSERT_ENTITY),
    ("facts", "upsert_fact", statements.UPSERT_FACT),
    ("close", "close_fact", statements.CLOSE_FACT),
    ("subject", "link_subject", statements.LINK_SUBJECT),
    ("object", "link_object", statements.LINK_OBJECT),
    ("asserted_in", "link_asserted_in", statements.LINK_ASSERTED_IN),
    ("next", "link_next", statements.LINK_NEXT),
    ("alias", "link_alias_of", statements.LINK_ALIAS_OF),
    ("supersedes", "link_supersedes", statements.LINK_SUPERSEDES),
)


def write_rows(driver, rows: Rows, batch_size: int = BATCH):
    """Write every payload. Returns the bookmark of the last write.

    The bookmark is the read-your-own-writes handle: hand it to a causal read
    and the reader refreshes until this write is visible before pinning.
    """
    bookmarks = None
    for attribute, name, statement in WRITE_PLAN:
        payload = getattr(rows, attribute)
        for start in range(0, len(payload), batch_size):
            chunk = payload[start : start + batch_size]
            _, bookmarks = client.write(
                driver, statement, {"rows": chunk},
                idempotency_key=batch_key(name, chunk), bookmarks=bookmarks,
            )
    return bookmarks


def extract_instance(instance, model: str = None):
    """One cached extraction call per session. Returns (extractions, failures).

    A session whose output does not fit the schema is skipped and named, never
    silently counted as zero facts. The extractor does mangle roughly one
    session in forty -- a misspelled field, a null where a string belongs -- and
    that is a measured quality number (slice 06), not a reason to discard the
    other thirty-nine.
    """
    out, failures = [], []
    for session in instance.sessions:
        date = dt.datetime.fromtimestamp(session.timestamp, dt.timezone.utc)
        try:
            facts = extract.extract_session(
                session, date.strftime("%Y-%m-%d %H:%M"), model=model
            )
        except ValidationError as exc:
            failures.append((session.session_id, str(exc).splitlines()[0]))
            continue
        out.append((session, facts))
    return out, failures


def ingest_instance(driver, instance, model: str = None) -> dict:
    """Ingest one instance. Idempotent for identical input.

    Not idempotent across *extractor* changes: fact ids are content-derived, so a
    new prompt or model produces new ids and a second generation of facts lands
    beside the first. Deleting the old generation is not an option -- DETACH
    DELETE runs at ~0.3s per node and a few hundred facts exceed HydraDB's 30s
    query timeout -- so the reset unit is the whole node: `docker compose down`,
    remove `hydradb-data/`, `docker compose up -d`.
    """
    extractions, failures = extract_instance(instance, model=model)
    rows = build_rows(instance, extractions)
    bookmarks = write_rows(driver, rows)
    return {
        "instance_id": instance.instance_id, "bookmarks": bookmarks,
        "parse_failures": len(failures), "failed_sessions": failures,
        **rows.counts(),
    }
