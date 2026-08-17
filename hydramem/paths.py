"""Gate 4: one batched pairwise path call for every candidate anchor.

The HydraDB-specific performance claim lives here. A multi-hop question names
several entities and the naive shape is a traversal per pair; `algo.MSpaths`
resolves every pair in one call, which is why the round-trip counter in
`client.py` is worth reading -- the claim is "at most four round trips per
question", counted, and this module is the fourth.

Two constraints the HydraDB parser imposes, both verified against
`src/query/path_procedure.rs` and against a live node:

**The call is the whole query.** `parse_native_path_call` finishes with
`parser.end()`, so nothing may follow the `RETURN`. There is no `WHERE`, which
means there is no way to scope the traversal to one instance inside the
statement -- the selector matches `(:Entity {key: ...})` and every instance in
the store has a `person:user`. `scoped()` therefore drops any path touching
another instance, and that filter is not optional: without it a question about
one instance can be answered through another instance's graph.

**`sourceValues` is a literal list, never a parameter.** `config_string_list`
does not resolve `$params`, so the anchor keys are interpolated into the query
text -- and those keys are derived from model-extracted entity names.
`literal()` is the trust boundary. It escapes rather than whitelists, because
the lexer's escape rule is exact and a charset whitelist would silently drop
legitimate keys.
"""

from dataclasses import dataclass, field

from . import client, ingest, statements

# Entity -> Fact -> Entity is two hops, so four covers a question that has to
# pass through an intermediate entity. `maxLen` above the server's
# `max_traversal_hops` (16 by default) is rejected as an admission error.
MAX_LEN = 4

# The result budget `fairRelationshipVariants` distributes round-robin across
# structural paths. Large enough that a real answer is not truncated away,
# small enough that a hyper-connected entity cannot flood the response.
RESULT_LIMIT = 64

# `person:assistant` is a bookkeeping subject, never a topical anchor.
#
# Slice 12 gave assistant-stated facts the subject `person:assistant` so that
# provenance could say who a claim came from. That makes a **second** star beside
# `person:user`, and the two share no node -- a fact hangs off one subject or the
# other. So a question whose mention resolves to assistant-sourced content, plus
# the implicit `person:user`, asked gate 4 whether the user and the assistant are
# connected. They never are.
#
# Measured on `7401057b` ("how many free night's stays can I redeem"): gate 1
# resolved `hilton` to `person:assistant`, because the hotel facts came from
# assistant turns, and gate 4 abstained `no_path` on a question whose answer is
# in the graph. A false abstention by construction, on exactly the category
# (`single-session-assistant`) that already scores worst.
#
# It was also a cost bug: both keys are hubs and the selector is not
# instance-scoped, so each matches one Entity per tenant. Pairwise over them
# under `strong` consistency exceeded the 30s query timeout and killed a
# 150-question arm at question 54.
#
# `person:user` stays. Questions genuinely are about the user, and dropping it
# would make gate 4 skip every "is the user connected to X" question -- vacuous
# rather than merely blunt. The distinction is what a question can be *about*.
NON_TOPICAL_KEYS = frozenset({"person:assistant"})


def topical(anchors) -> list:
    """Anchors that a question can be about, order-stable and deduped."""
    return [a for a in dict.fromkeys(anchors) if a not in NON_TOPICAL_KEYS]


class UnsafeAnchor(ValueError):
    """An anchor key that cannot be safely interpolated into a query."""


def literal(value: str) -> str:
    """One anchor key as a Cypher string literal for the native path parser.

    The lexer (`path_procedure.rs`, `fn lex`) reads a backslash as an escape for
    whatever character follows it, so escaping the backslash and the quote is
    exact. Control characters have no escape and are refused rather than
    stripped: a key containing one did not come from `ingest.normalize`, and
    quietly repairing it would hide whatever produced it.
    """
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise UnsafeAnchor(f"control character in anchor key: {value!r}")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def query(anchors) -> str:
    return statements.MS_PATHS.format(anchors=", ".join(literal(a) for a in anchors))


def pair_count(n: int) -> int:
    """Unordered pairs. `pairwise` filters `source < target` in the shard, so
    n anchors are n(n-1)/2 pairs -- all of them in the one call."""
    return n * (n - 1) // 2


@dataclass(frozen=True)
class PathResult:
    """What one batched call found, plus the bound it searched under.

    `pairs_tried` and `max_len` are carried so an abstention can name them.
    "No path" is not a finding; "no path between 3 pairs within 4 hops" is.
    """

    paths: list = field(default_factory=list)
    pairs_tried: int = 0
    max_len: int = MAX_LEN
    # The traversal was cut off by the server's query timeout, so an empty
    # `paths` means "not established" rather than "no path". Gate 4 must not
    # abstain on it.
    timed_out: bool = False

    @property
    def detail(self) -> str:
        return f"{self.pairs_tried} pairs, no path within {self.max_len} hops"


def nodes(path: list) -> list:
    """The node maps of a path. `path` alternates node, edge type, node, ..."""
    return path[::2]


def fact_ids(result: PathResult) -> set:
    """Every fact a path passed through. The evidence for a multi-hop answer."""
    return {node["fact_id"] for path in result.paths
            for node in nodes(path) if "fact_id" in node}


def scoped(paths: list, instance_id: str) -> list:
    """Paths that stay inside one instance.

    Not a tidiness filter. The selector resolves anchors by the `key` property,
    which is not instance-scoped -- every instance in the store holds a
    `person:user` -- so an unscoped result can connect two entities through a
    third party's graph.

    ponytail: filtered client-side because the statement has nowhere to put a
    WHERE. The traversal itself is still not instance-scoped, so the *work* is
    shared across tenants even though the *answer* is not. Scoping the traversal
    needs an instance-scoped property on Entity, which is a node wipe.
    """
    return [p for p in paths
            if all(node.get("instance_id") == instance_id for node in nodes(p))]


def find(driver, instance_id: str, anchors, max_len: int = MAX_LEN,
         result_limit: int = RESULT_LIMIT, bookmarks=None,
         consistency: str = "causal") -> PathResult:
    """Every path between every pair of anchors, in one round trip.

    Fewer than two anchors is not a multi-hop question and costs no call at all.
    """
    anchors = topical(anchors)                      # order-stable dedupe
    if len(anchors) < 2:
        return PathResult(pairs_tried=0, max_len=max_len)

    # The selector matches `skey`, so the anchors sent are instance-scoped. The
    # keys the caller passes, the trace and `scoped()` all stay in plain `key`
    # terms -- the scoping is a property of the query, not of the vocabulary.
    try:
        rows = client.read(
            driver, query(ingest.scoped_key(instance_id, a) for a in anchors),
            {"max_len": max_len, "result_limit": result_limit},
            bookmarks=bookmarks, consistency=consistency,
        )
    except client.neo4j.exceptions.Neo4jError as exc:
        # A traversal that ran out of time has not established that no path
        # exists, and reporting `no_path` from it would be an abstention
        # asserting something the database never checked. It killed an arm once
        # by propagating; `timed_out` makes gate 4 pass instead, which is the
        # direction BIAS fixes -- a false pass costs one model call and still
        # has to survive the citation check.
        if "timeout" not in str(exc).lower():
            raise
        return PathResult(pairs_tried=pair_count(len(anchors)), max_len=max_len,
                          timed_out=True)
    return PathResult(
        paths=scoped([row["path"] for row in rows], instance_id),
        pairs_tried=pair_count(len(anchors)),
        max_len=max_len,
    )
