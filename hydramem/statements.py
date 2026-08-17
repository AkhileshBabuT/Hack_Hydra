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
    n.skey = row.skey,
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
    n.role = row.role,
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

# **Filter the node, then join.** Every instance-scoped read below splits into
# two MATCH clauses on purpose. Written as one pattern with the filter in the
# WHERE -- `MATCH (f:Fact)-[:SUBJECT]->(e:Entity) WHERE f.instance_id = $id` --
# HydraDB builds the join across *every* tenant in the store and filters
# afterwards, so latency scales with the size of the whole store rather than
# with the tenant being read. Measured on a store holding 2,122 Fact nodes:
# **7,635 ms to return 11 rows.** Filtering a single-node scan first, so the
# automatic property index on `instance_id` drives, returns the same 11 rows in
# **250 ms** -- 30x, and the gap widens as the store grows.
#
# There is no index DDL and `EXPLAIN` is unreachable over Bolt, so clause order
# is the only lever there is. Do not "simplify" one of these back into a single
# pattern.

FACTS_FOR_INSTANCE = """
MATCH (f:Fact)
WHERE f.instance_id = $instance_id
MATCH (f)-[:SUBJECT]->(e:Entity)
RETURN f.id AS id, f.fact_id AS fact_id, f.predicate AS predicate,
       f.value_text AS value_text, f.valid_from AS valid_from,
       f.valid_to AS valid_to, f.asserted_at AS asserted_at,
       f.status AS status, f.session_id AS session_id,
       f.turn_idx AS turn_idx, f.role AS role, f.snippet AS snippet,
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

# The change history as graph structure: every revision in the instance, with
# the value it replaced and the date it replaced it. One round trip for the
# whole instance, because HydraDB has no variable-length pattern -- the chain is
# reassembled from these pairs, or derived from the fact list by
# `temporal.history`, and slice 08's live test asserts the two agree.
SUPERSESSION_CHAIN_FOR_INSTANCE = """
MATCH (new:Fact)
WHERE new.instance_id = $instance_id
MATCH (new)-[r:SUPERSEDES]->(old:Fact)
RETURN new.fact_id AS new_fact_id, new.value_text AS new_value,
       new.valid_from AS changed_at, new.predicate AS predicate,
       old.fact_id AS old_fact_id, old.value_text AS old_value,
       old.valid_from AS old_valid_from, old.valid_to AS old_valid_to,
       old.status AS old_status
ORDER BY changed_at
"""

COUNT_EDGES_SUPERSEDES = """
MATCH (a:Fact)
WHERE a.instance_id = $instance_id
MATCH (a)-[r:SUPERSEDES]->(b:Fact)
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
MATCH (a:Entity)
WHERE a.instance_id = $instance_id
MATCH (a)-[r:ALIAS_OF]->(b:Entity)
RETURN a.key AS alias_key, b.key AS canonical_key
"""

# Gate 4. One pairwise path call for every candidate anchor at once, instead of
# a round trip per pair. Verified against the HydraDB parser
# (`src/query/path_procedure.rs`) and live:
#
#   - The call must be the *whole* query. `parse_native_path_call` ends with
#     `parser.end()`, so no MATCH, no WHERE, no LIMIT may follow it. That is why
#     the instance filter is applied client-side in `paths.py` -- there is
#     nowhere in this statement to put it.
#   - Unknown config keys are rejected outright, and `sourceValues` /
#     `targetValues` / `relTypes` must be *literal* lists: `config_string_list`
#     never resolves a `$parameter`. Hence the interpolation, and hence
#     `paths.literal` escaping what it interpolates.
#   - `maxLen` and `resultLimit` *are* parameterizable (`config_u64` accepts a
#     parameter), so the bound stays a bound and not a string.
#   - `fairRelationshipVariants` requires pairwise MSpaths and rejects any of
#     weightProp / costProp / maxCost. It is what distributes the result budget
#     round-robin across structural paths, so one hyper-connected entity cannot
#     consume the whole response.
#   - `pairwise` enumerates each unordered pair once (the shard filters
#     `source < target`), so n anchors cost n(n-1)/2 pairs in one call.
#
# `path` comes back as a flat list: [node-map, 'EDGE_TYPE', node-map, ...].
# `skey`, not `key`. The selector resolves anchors by an exact property match and
# is the only part of this call that can be scoped at all -- there is nowhere to
# put a WHERE, because HydraDB's native path parser ends with `parser.end()`. On
# `key`, every tenant's `person:user` is a source, so the traversal cost grows
# with the whole store and a real path can be crowded out of `resultLimit` by
# other tenants' paths and read as `no_path`. Both happened: a 150-question arm
# died on the 30s timeout, and two fixture tests started failing once the store
# held ~160 tenants. `skey` is "<instance_id>|<key>", so the traversal starts and
# stays inside one tenant.
MS_PATHS = """
CALL algo.MSpaths({{
  sourceLabel: 'Entity', sourceProperty: 'skey', sourceValues: [{anchors}],
  targetLabel: 'Entity', targetProperty: 'skey', targetValues: [{anchors}],
  pairwise: true, fairRelationshipVariants: true,
  relTypes: ['SUBJECT', 'OBJECT'], relDirection: 'both',
  maxLen: $max_len, resultLimit: $result_limit
}}) YIELD path RETURN path
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
MATCH (f:Fact)
WHERE f.instance_id = $instance_id
MATCH (f)-[r:SUBJECT]->(e:Entity)
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
    "facts_for_instance": (FACTS_FOR_INSTANCE, {"instance_id": "__probe__"}),
    "aliases_of_entity": (ALIASES_OF_ENTITY, {"eid": 0}),
    "entities_for_instance": (ENTITIES_FOR_INSTANCE, {"instance_id": "__probe__"}),
    "aliases_for_instance": (ALIASES_FOR_INSTANCE, {"instance_id": "__probe__"}),
    "superseded_by_fact": (SUPERSEDED_BY_FACT, {"fid": 0}),
    "supersession_chain_for_instance": (SUPERSESSION_CHAIN_FOR_INSTANCE,
                                        {"instance_id": "__probe__"}),
    "count_edges_supersedes": (COUNT_EDGES_SUPERSEDES, {"instance_id": "__probe__"}),
    "count_facts": (COUNT_FACTS, {"instance_id": "__probe__"}),
    "count_entities": (COUNT_ENTITIES, {"instance_id": "__probe__"}),
    "count_edges_subject": (COUNT_EDGES_SUBJECT, {"instance_id": "__probe__"}),
    # Probed fully formed, not as a template: this one is assembled at call
    # time, and a statement whose *assembled* shape is never parsed is exactly
    # the statement the inventory exists to catch.
    "ms_paths": (MS_PATHS.format(anchors="'__probe__'"),
                 {"max_len": 2, "result_limit": 1}),
}
