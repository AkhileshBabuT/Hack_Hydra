"""Slice 09: the batched path call, gate 4, and the round-trip counter.

Two things are being defended here and they pull in opposite directions.

The *claim* is that a multi-hop question costs one path call however many
anchors it names, and at most four Bolt round trips end to end. That is only
worth asserting against a live node, so the round-trip tests are live.

The *risk* is that `sourceValues` cannot be a `$parameter`, so anchor keys --
which come from model-extracted entity names -- are interpolated into query
text, and the statement has no `WHERE` to scope the traversal to one instance.
Those two are tested without a database, because they are the tests that have
to keep passing when nobody is thinking about them.
"""

import pytest
from conftest import make_fact, make_instance, make_session

from hydramem import client, gates, ingest, paths, statements

USER = {"id": 1, "key": "person:user", "name": "user", "type": "person"}
ACME = {"id": 2, "key": "org:acme", "name": "Acme", "type": "org"}


def node(key, instance_id="i", **kw):
    return {"key": key, "instance_id": instance_id, **kw}


def path(*keys, instance_id="i"):
    """A path in the shape HydraDB returns: node, edge type, node, ..."""
    out = []
    for i, key in enumerate(keys):
        if i:
            out.append("SUBJECT")
        out.append(node(key, instance_id) if not key.startswith("fact:")
                   else node(key, instance_id, fact_id=key.split(":", 1)[1]))
    return out


# --- the trust boundary ---------------------------------------------------

def test_an_anchor_key_is_escaped_not_concatenated():
    """`sourceValues` is a literal list -- `config_string_list` never resolves a
    `$parameter` -- so these strings go into query text. The lexer treats a
    backslash as an escape for the next character, so this escaping is exact.
    """
    assert paths.literal("person:user") == "'person:user'"
    assert paths.literal("o'brien") == r"'o\'brien'"
    assert paths.literal("back\\slash") == r"'back\\slash'"


def test_a_quote_cannot_close_the_literal_and_start_a_new_option():
    """The injection this escaping exists to stop: an entity name that ends the
    string and appends a config key HydraDB would otherwise honour.
    """
    hostile = "user', targetValues: ['admin"
    assert paths.literal(hostile).count("'") == 2 + hostile.count("'")
    built = paths.query(["person:user", hostile])
    assert "targetValues: ['admin" not in built


@pytest.mark.parametrize("bad", ["new\nline", "tab\there", "nul\x00byte", "del\x7f"])
def test_a_control_character_is_refused_rather_than_stripped(bad):
    """A key with a control character did not come from `ingest.normalize`.
    Quietly repairing it would hide whatever produced it.
    """
    with pytest.raises(paths.UnsafeAnchor):
        paths.literal(bad)


def test_a_path_leaving_the_instance_is_dropped():
    """The selector matches on `key`, which is not instance-scoped: every
    instance in the store holds a `person:user`. With no `WHERE` available in
    the statement, this filter is the only thing standing between a question
    about one instance and an answer routed through another one.
    """
    mine = path("person:user", "org:acme", instance_id="mine")
    theirs = path("person:user", "org:acme", instance_id="theirs")
    crossing = [node("person:user", "mine"), "SUBJECT", node("org:acme", "theirs")]

    assert paths.scoped([mine, theirs, crossing], "mine") == [mine]


# --- the batched call -----------------------------------------------------

def test_every_anchor_appears_as_both_source_and_target():
    built = paths.query(["person:user", "org:acme"])
    assert built.count("'person:user'") == 2 and built.count("'org:acme'") == 2
    assert "pairwise: true" in built and "fairRelationshipVariants: true" in built


@pytest.mark.parametrize("n,pairs", [(1, 0), (2, 1), (3, 3), (5, 10), (20, 190)])
def test_pair_count_is_every_unordered_pair(n, pairs):
    """All of them in the one call. The shard filters `source < target`, so
    twenty anchors are 190 pairs and still one round trip.
    """
    assert paths.pair_count(n) == pairs


def test_fewer_than_two_anchors_costs_no_call(driver):
    before = client.round_trips()
    assert paths.find(driver, "i", ["person:user"]).paths == []
    assert client.round_trips() == before


def test_fact_ids_are_pulled_off_the_path():
    result = paths.PathResult(paths=[path("person:user", "fact:abc", "org:acme")])
    assert paths.fact_ids(result) == {"abc"}


# --- gate 4 ---------------------------------------------------------------

def stub(paths_found, pairs=1, max_len=paths.MAX_LEN):
    def find(anchors):
        return paths.PathResult(paths=paths_found, pairs_tried=pairs, max_len=max_len)
    return find


def test_a_single_entity_question_is_not_multi_hop_and_skips_the_gate():
    def explode(anchors):
        raise AssertionError("gate 4 ran on a question with nothing to connect")

    assert gates.path_gate(("person:user",), explode).passed


def test_unconnected_anchors_abstain_naming_the_pairs_and_the_bound():
    result = gates.path_gate(("person:user", "org:acme"), stub([], pairs=1, max_len=4))
    assert not result.passed
    assert result.reason == "no_path"
    assert result.detail == (
        "no_path: person:user, org:acme: 1 pairs, no path within 4 hops"
    )


def test_connected_anchors_pass():
    found = stub([path("person:user", "org:acme")])
    assert gates.path_gate(("person:user", "org:acme"), found).passed


def test_gate_4_runs_only_after_gates_1_to_3_have_passed():
    def explode(anchors):
        raise AssertionError("gate 4 ran on a question already lost")

    lost = gates.run("Where does Maya Chen work?", [USER], {},
                     lambda key: [], None, explode)
    assert lost.reason == "unknown_entity"


def test_the_cascade_reports_gate_4_last():
    facts = {"person:user": [{"predicate": "employer", "value_text": "Acme"}],
             "org:acme": []}
    result = gates.run("Where do I work, at Acme?", [USER, ACME], {},
                       facts.get, None, stub([]))
    assert result.reason == "no_path"


# --- against a live node --------------------------------------------------

SESSIONS = [make_session(0, "s0", 1_000_000), make_session(1, "s1", 2_000_000)]


@pytest.fixture
def two_entities(driver, instance_id):
    """An instance holding `person:user` and `org:acme`, joined through a Fact.

    `value_is_entity` is what makes ingest write the OBJECT edge -- without it
    "Acme" is a literal string on the fact and `org:acme` is never created, so
    there is nothing to path *to*. Easy to leave out and it fails as "no path"
    rather than as an error.
    """
    ingest.write_rows(driver, ingest.build_rows(
        make_instance(SESSIONS, instance_id),
        [(SESSIONS[0], [make_fact(predicate="employer", value="Acme",
                                  value_is_entity=True)])]))
    return instance_id


def test_one_call_finds_the_path_between_two_entities(driver, two_entities):
    before = client.round_trips()
    found = paths.find(driver, two_entities, ["person:user", "org:acme"],
                       consistency="strong")

    assert client.round_trips() - before == 1, "the whole point is one call"
    assert found.paths, "user -SUBJECT- fact -OBJECT- org should be reachable"
    assert found.pairs_tried == 1
    assert all(len(p) == 5 for p in found.paths)      # node, edge, node, edge, node


def test_no_returned_path_leaves_the_instance(driver, two_entities):
    """`person:user` is not a unique key -- every tenant in the store has one,
    and the statement has no WHERE to say which. Measured on this node: the
    same anchor pair matched **6** tenants and returned 6 paths, 1 of them
    ours. The filter is what keeps the other 5 out of an answer.
    """
    found = paths.find(driver, two_entities, ["person:user", "org:acme"],
                       consistency="strong")
    assert found.paths
    assert all(node.get("instance_id") == two_entities
               for p in found.paths for node in paths.nodes(p))


def test_the_hop_bound_is_real_and_the_abstention_names_it(driver, two_entities):
    """Entity to entity is two hops through the Fact. At `maxLen=1` there is no
    path, which is what makes "no path within N hops" a claim rather than a
    shrug -- the same anchors succeed one line above.
    """
    found = paths.find(driver, two_entities, ["person:user", "org:acme"],
                       max_len=1, consistency="strong")
    assert found.paths == []

    result = gates.path_gate(("person:user", "org:acme"), lambda a: found)
    assert result.detail.endswith("1 pairs, no path within 1 hops")


def test_a_multi_hop_question_costs_four_round_trips(driver, two_entities):
    """The number the cost story rests on, counted rather than estimated.

    Entities, aliases, one shared fetch of the instance's facts, and one
    batched path call. No model is involved: `check_gates` is the whole
    round-trip budget of a question.
    """
    from hydramem import answer

    before = client.round_trips()
    verdict, facts = answer.check_gates(driver, two_entities,
                                        "Do I still work at Acme?",
                                        consistency="strong")
    assert client.round_trips() - before == 4, "one call per pair has crept back in"
    assert verdict.passed and facts


def test_a_question_lost_at_gate_1_costs_two_round_trips(driver, two_entities):
    """Short-circuiting is not decoration: the fact read is never issued and
    the path call never happens.
    """
    from hydramem import answer

    before = client.round_trips()
    verdict, facts = answer.check_gates(driver, two_entities,
                                        "Did Maya Chen call?", consistency="strong")
    assert client.round_trips() - before == 2
    assert verdict.reason == "unknown_entity" and facts == []


def test_a_single_entity_question_costs_three_round_trips(driver, two_entities):
    from hydramem import answer

    before = client.round_trips()
    verdict, _ = answer.check_gates(driver, two_entities, "Where do I work?",
                                    consistency="strong")
    assert client.round_trips() - before == 3
    assert verdict.passed


def test_the_assembled_path_statement_is_the_one_the_inventory_probes():
    """`MS_PATHS` is a template, so the inventory registers its filled form.
    If the two drift apart, the statement in the inventory is not the statement
    that runs -- which is the whole failure mode the inventory exists to stop.
    """
    probed, _ = statements.INVENTORY["ms_paths"]
    assert probed == statements.MS_PATHS.format(anchors="'__probe__'")
    assert paths.query(["__probe__"]) == probed
