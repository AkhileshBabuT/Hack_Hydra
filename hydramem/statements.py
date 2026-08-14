"""Every Cypher statement the codebase can emit, in one place.

HydraDB implements a deliberate subset of OpenCypher and rejects everything
else at parse time. `make verify-cypher` executes each statement below against
a throwaway node and fails loudly on any rejection, so an illegal statement is
found in seconds rather than days later tangled with extraction and gate bugs.

`EXPLAIN` is reachable only through HydraDB's in-process shard API, not over
Bolt or HTTP, so a live node is the only validator available to us.

Constraints these statements are written against, verified against the HydraDB
source at commit 6a2fbb1:

  - MERGE matches on `id` only; `id` must be a non-negative integer.
    ON CREATE / ON MATCH do not exist.
  - A vertex upsert must be MERGE-by-id followed by SET. Folding other
    properties into the MERGE pattern is rejected.
  - One relationship pattern per UNWIND batch, one hop, directed.
  - UNWIND input must be a parameter holding a list of maps, never inline.
  - WHERE has no IN / CONTAINS / ENDS WITH / IS NULL.
  - ORDER BY takes a projected alias, <binding>.id, or count(*).
  - No index DDL exists; property indexes are maintained automatically.
  - A node carrying a label or a non-id property must be named:
    `(:Fact {id: 1})-[r]->(e)` is rejected, `(f:Fact {id: 1})-[r]->(e)` is not.
  - An edge batch aborts whole if any endpoint is missing, so node batches must
    land before edge batches. It raises rather than skipping the row -- which is
    what we want, since a missing endpoint means the row builder is wrong.
  - Guarded merge (verified live, undocumented upstream): the guard property and
    every create-only property must each be SET from a row field of the *same
    name*, and the guard property may not itself be create-only.
"""

# --- writes ---------------------------------------------------------------

UPSERT_ENTITY = """
UNWIND $rows AS row
MERGE (n {id: row.vid})
SET n:Entity,
    n.key = row.key,
    n.name = row.name,
    n.type = row.type,
    n.first_seen = row.first_seen,
    n.last_seen = row.last_seen,
    n.instance_id = row.instance_id,
    n.__hydradb_update_if_newer_by = row.last_seen,
    n.__hydradb_create_only_first_seen = row.first_seen
"""

UPSERT_FACT = """
UNWIND $rows AS row
MERGE (n {id: row.vid})
SET n:Fact,
    n.fact_id = row.fact_id,
    n.predicate = row.predicate,
    n.value_text = row.value_text,
    n.value_type = row.value_type,
    n.valid_from = row.valid_from,
    n.valid_to = row.valid_to,
    n.asserted_at = row.asserted_at,
    n.session_id = row.session_id,
    n.turn_idx = row.turn_idx,
    n.snippet = row.snippet,
    n.confidence = row.confidence,
    n.status = row.status,
    n.valid_from_hint = row.valid_from_hint,
    n.instance_id = row.instance_id,
    n.__hydradb_update_if_newer_by = row.asserted_at
"""

LINK_SUBJECT = """
UNWIND $rows AS row
MATCH (f:Fact {id: row.fid}), (e:Entity {id: row.eid})
MERGE (f)-[r:SUBJECT {id: row.rid}]->(e)
SET r.instance_id = row.instance_id
"""

UPSERT_SESSION = """
UNWIND $rows AS row
MERGE (n {id: row.vid})
SET n:Session,
    n.key = row.key,
    n.idx = row.idx,
    n.timestamp = row.timestamp,
    n.instance_id = row.instance_id
"""

# One relationship pattern per batch is a hard HydraDB limit, so each edge type
# gets its own statement. All five share a shape: both endpoints matched by id,
# the edge merged on its own deterministic id, which is what makes a replay a
# no-op rather than a duplicate.
LINK_OBJECT = """
UNWIND $rows AS row
MATCH (f:Fact {id: row.fid}), (e:Entity {id: row.eid})
MERGE (f)-[r:OBJECT {id: row.rid}]->(e)
SET r.instance_id = row.instance_id
"""

LINK_ASSERTED_IN = """
UNWIND $rows AS row
MATCH (f:Fact {id: row.fid}), (s:Session {id: row.sid})
MERGE (f)-[r:ASSERTED_IN {id: row.rid}]->(s)
SET r.instance_id = row.instance_id
"""

LINK_NEXT = """
UNWIND $rows AS row
MATCH (a:Session {id: row.src}), (b:Session {id: row.dst})
MERGE (a)-[r:NEXT {id: row.rid}]->(b)
SET r.instance_id = row.instance_id
"""

LINK_SUPERSEDES = """
UNWIND $rows AS row
MATCH (a:Fact {id: row.src}), (b:Fact {id: row.dst})
MERGE (a)-[r:SUPERSEDES {id: row.rid}]->(b)
SET r.instance_id = row.instance_id
"""

# Materialization of the derived chain. A vertex upsert, not a MATCH ... SET:
# guarded merge markers are only honoured inside the UNWIND upsert form.
#
# The guard is `valid_to`, which moves 0 -> timestamp and never back. HydraDB
# applies a guarded patch strictly when stored < incoming, so replaying this is
# a no-op and an older close cannot move the end date backwards. Note the
# companion effect: UPSERT_FACT is guarded on `asserted_at`, which is always
# equal on replay, so a later re-ingest cannot reset `status` to 'current'.
CLOSE_FACT = """
UNWIND $rows AS row
MERGE (n {id: row.vid})
SET n:Fact,
    n.status = row.status,
    n.valid_to = row.valid_to,
    n.__hydradb_update_if_newer_by = row.valid_to
"""

LINK_ALIAS_OF = """
UNWIND $rows AS row
MATCH (a:Entity {id: row.src}), (b:Entity {id: row.dst})
MERGE (a)-[r:ALIAS_OF {id: row.rid}]->(b)
SET r.instance_id = row.instance_id
"""

# --- reads ----------------------------------------------------------------

FACT_BY_ID = """
MATCH (f:Fact {id: $fid})
RETURN f.fact_id AS fact_id,
       f.predicate AS predicate,
       f.value_text AS value_text,
       f.asserted_at AS asserted_at,
       f.instance_id AS instance_id
"""

SUBJECT_OF_FACT = """
MATCH (f:Fact {id: $fid})-[:SUBJECT]->(e:Entity)
RETURN e.key AS key, e.name AS name, e.type AS type
"""

COUNT_LABEL = """
MATCH (n:Entity)
RETURN count(*) AS total
"""

ENTITY_BY_ID = """
MATCH (e:Entity {id: $eid})
RETURN e.key AS key, e.name AS name, e.type AS type,
       e.first_seen AS first_seen, e.last_seen AS last_seen
"""

# Gate 2 pulls every fact on the entity in one round trip and applies the
# candidate predicate set in Python -- WHERE has no IN, and the predicate gate
# is the primary suspect when abstention precision fails, so it has to be
# debuggable without a database.
FACTS_FOR_ENTITY = """
MATCH (f:Fact)-[:SUBJECT]->(e:Entity {id: $eid})
RETURN f.id AS id, f.fact_id AS fact_id, f.predicate AS predicate,
       f.value_text AS value_text, f.value_type AS value_type,
       f.valid_from AS valid_from, f.valid_to AS valid_to,
       f.asserted_at AS asserted_at, f.status AS status,
       f.session_id AS session_id, f.turn_idx AS turn_idx,
       f.snippet AS snippet, f.confidence AS confidence
ORDER BY asserted_at
"""

FACTS_FOR_INSTANCE = """
MATCH (f:Fact)-[:SUBJECT]->(e:Entity)
WHERE f.instance_id = $instance_id
RETURN f.id AS id, f.fact_id AS fact_id, f.predicate AS predicate,
       f.value_text AS value_text, f.valid_from AS valid_from,
       f.valid_to AS valid_to, f.asserted_at AS asserted_at,
       f.status AS status, f.session_id AS session_id,
       f.turn_idx AS turn_idx, f.snippet AS snippet,
       e.key AS subject_key, e.name AS subject_name
ORDER BY asserted_at
"""

# The chain as graph structure: what this fact replaced, newest first.
SUPERSEDED_BY_FACT = """
MATCH (new:Fact {id: $fid})-[:SUPERSEDES]->(old:Fact)
RETURN old.id AS id, old.fact_id AS fact_id, old.predicate AS predicate,
       old.value_text AS value_text, old.valid_from AS valid_from,
       old.valid_to AS valid_to, old.asserted_at AS asserted_at,
       old.status AS status
"""

COUNT_EDGES_SUPERSEDES = """
MATCH (a:Fact)-[r:SUPERSEDES]->(b:Fact)
WHERE a.instance_id = $instance_id
RETURN count(*) AS total
"""

ALIASES_OF_ENTITY = """
MATCH (a:Entity {id: $eid})-[:ALIAS_OF]->(b:Entity)
RETURN b.id AS id, b.key AS key, b.name AS name
"""

# Gate 1 resolves the question's entities against these two in one round trip
# each, not one per candidate: an instance holds tens of entities, so pulling
# the lot costs less than deciding which ones to ask for.
ENTITIES_FOR_INSTANCE = """
MATCH (e:Entity)
WHERE e.instance_id = $instance_id
RETURN e.id AS id, e.key AS key, e.name AS name, e.type AS type
"""

# The alias closure as pairs. One hop, not transitive: `alias_pairs` only ever
# points a shorter surface form at a longer one, so a chain would mean "maya" ->
# "maya chen" -> "maya chen jr" and that is not a shape it can produce.
ALIASES_FOR_INSTANCE = """
MATCH (a:Entity)-[r:ALIAS_OF]->(b:Entity)
WHERE a.instance_id = $instance_id
RETURN a.key AS alias_key, b.key AS canonical_key
"""

COUNT_FACTS = """
MATCH (f:Fact)
WHERE f.instance_id = $instance_id
RETURN count(*) AS total
"""

COUNT_ENTITIES = """
MATCH (e:Entity)
WHERE e.instance_id = $instance_id
RETURN count(*) AS total
"""

COUNT_EDGES_SUBJECT = """
MATCH (f:Fact)-[r:SUBJECT]->(e:Entity)
WHERE f.instance_id = $instance_id
RETURN count(*) AS total
"""


# Name -> (statement, parse-probe parameters).
#
# Probe params make the statement executable without writing anything: batch
# forms get an empty row list, reads get an id that matches nothing. The point
# is to reach the parser, not to exercise behaviour.
INVENTORY = {
    "upsert_entity": (UPSERT_ENTITY, {"rows": []}),
    "upsert_fact": (UPSERT_FACT, {"rows": []}),
    "upsert_session": (UPSERT_SESSION, {"rows": []}),
    "link_subject": (LINK_SUBJECT, {"rows": []}),
    "link_object": (LINK_OBJECT, {"rows": []}),
    "link_asserted_in": (LINK_ASSERTED_IN, {"rows": []}),
    "link_next": (LINK_NEXT, {"rows": []}),
    "link_alias_of": (LINK_ALIAS_OF, {"rows": []}),
    "link_supersedes": (LINK_SUPERSEDES, {"rows": []}),
    "close_fact": (CLOSE_FACT, {"rows": []}),
    "fact_by_id": (FACT_BY_ID, {"fid": 0}),
    "subject_of_fact": (SUBJECT_OF_FACT, {"fid": 0}),
    "count_label": (COUNT_LABEL, {}),
    "entity_by_id": (ENTITY_BY_ID, {"eid": 0}),
    "facts_for_entity": (FACTS_FOR_ENTITY, {"eid": 0}),
    "facts_for_instance": (FACTS_FOR_INSTANCE, {"instance_id": "__probe__"}),
    "aliases_of_entity": (ALIASES_OF_ENTITY, {"eid": 0}),
    "entities_for_instance": (ENTITIES_FOR_INSTANCE, {"instance_id": "__probe__"}),
    "aliases_for_instance": (ALIASES_FOR_INSTANCE, {"instance_id": "__probe__"}),
    "superseded_by_fact": (SUPERSEDED_BY_FACT, {"fid": 0}),
    "count_edges_supersedes": (COUNT_EDGES_SUPERSEDES, {"instance_id": "__probe__"}),
    "count_facts": (COUNT_FACTS, {"instance_id": "__probe__"}),
    "count_entities": (COUNT_ENTITIES, {"instance_id": "__probe__"}),
    "count_edges_subject": (COUNT_EDGES_SUBJECT, {"instance_id": "__probe__"}),
}
