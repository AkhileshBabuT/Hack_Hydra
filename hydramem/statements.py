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
    n.instance_id = row.instance_id
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
    n.instance_id = row.instance_id
"""

LINK_SUBJECT = """
UNWIND $rows AS row
MATCH (f:Fact {id: row.fid}), (e:Entity {id: row.eid})
MERGE (f)-[r:SUBJECT {id: row.rid}]->(e)
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


# Name -> (statement, parse-probe parameters).
#
# Probe params make the statement executable without writing anything: batch
# forms get an empty row list, reads get an id that matches nothing. The point
# is to reach the parser, not to exercise behaviour.
INVENTORY = {
    "upsert_entity": (UPSERT_ENTITY, {"rows": []}),
    "upsert_fact": (UPSERT_FACT, {"rows": []}),
    "link_subject": (LINK_SUBJECT, {"rows": []}),
    "fact_by_id": (FACT_BY_ID, {"fid": 0}),
    "subject_of_fact": (SUBJECT_OF_FACT, {"fid": 0}),
    "count_label": (COUNT_LABEL, {}),
}
