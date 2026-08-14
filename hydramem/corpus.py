"""LongMemEval loader: raw JSON -> question instances with whole sessions.

Extraction windows at the session level, so the unit this module yields is a
whole session, never a turn.

`instance_id` is the benchmark question id. It is the tenant partition on every
node, which is what keeps 500 unrelated histories in one graph without them
seeing each other.
"""

import datetime as dt
import json
import pathlib
from dataclasses import dataclass

DATA = pathlib.Path("data")
S_CORPUS = DATA / "longmemeval_s_cleaned.json"
ORACLE_CORPUS = DATA / "longmemeval_oracle.json"

# "2023/04/10 (Mon) 17:50" -- the weekday is redundant with the date, and every
# date in both corpora matches this shape exactly (checked, 500 instances).
_DATE_FMT = "%Y/%m/%d %H:%M"


def parse_date(text: str) -> int:
    """Corpus date string -> epoch seconds (UTC)."""
    cleaned = text.replace(text[text.index("(") : text.index(")") + 1], "").replace("  ", " ")
    return int(dt.datetime.strptime(cleaned.strip(), _DATE_FMT).replace(tzinfo=dt.timezone.utc).timestamp())


@dataclass(frozen=True)
class Turn:
    idx: int
    role: str          # user | assistant
    content: str
    has_answer: bool   # gold evidence marker; never fed to the pipeline


@dataclass(frozen=True)
class Session:
    session_id: str
    idx: int           # ordinal position in the history, for NEXT and first_seen
    timestamp: int     # epoch seconds
    turns: tuple[Turn, ...]

    def text(self) -> str:
        return "\n".join(f"[{t.idx}] {t.role}: {t.content}" for t in self.turns)


@dataclass(frozen=True)
class Instance:
    instance_id: str   # question_id; the tenant partition
    question_type: str
    question: str
    answer: str
    asked_at: int
    sessions: tuple[Session, ...]
    answer_session_ids: tuple[str, ...]

    @property
    def is_abstention(self) -> bool:
        """LongMemEval marks unanswerable questions with an _abs id suffix."""
        return self.instance_id.endswith("_abs")


def to_instance(raw: dict) -> Instance:
    """Sessions are ordered by their timestamp, not by corpus order.

    The corpus lists haystack sessions out of chronological order (verified:
    dates descend then jump). Recency is structure in this graph -- NEXT edges,
    session idx, supersession order -- so the ordering is fixed once, here,
    rather than being re-derived correctly-or-not at each use site.
    """
    triples = sorted(
        zip(raw["haystack_session_ids"], raw["haystack_dates"], raw["haystack_sessions"]),
        key=lambda t: parse_date(t[1]),
    )
    sessions = tuple(
        Session(
            session_id=sid,
            idx=idx,
            timestamp=parse_date(date),
            turns=tuple(
                Turn(i, t["role"], t["content"], bool(t.get("has_answer")))
                for i, t in enumerate(turns)
            ),
        )
        for idx, (sid, date, turns) in enumerate(triples)
    )
    return Instance(
        instance_id=raw["question_id"],
        question_type=raw["question_type"],
        question=raw["question"],
        # Gold answers are sometimes numbers ("how many hours" -> 24). Coerce
        # once here so no scorer or prompt has to think about the type.
        answer=str(raw["answer"]),
        asked_at=parse_date(raw["question_date"]),
        sessions=sessions,
        answer_session_ids=tuple(raw.get("answer_session_ids") or ()),
    )


def iter_instances(path: pathlib.Path = S_CORPUS):
    """Yield instances one at a time from a top-level JSON array.

    ponytail: hand-rolled streaming rather than ijson. The _s corpus is a 277 MB
    array of ~500 objects; json.load holds every one of them as live dicts at
    once (multi-GB), while raw_decode holds the text plus a single instance.
    One dependency avoided and the peak stays flat.
    """
    text = pathlib.Path(path).read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    pos = text.index("[") + 1
    while True:
        while pos < len(text) and text[pos] in " \t\r\n,":
            pos += 1
        if pos >= len(text) or text[pos] == "]":
            return
        raw, pos = decoder.raw_decode(text, pos)
        yield to_instance(raw)


def load_instance(instance_id: str, path: pathlib.Path = S_CORPUS) -> Instance:
    for instance in iter_instances(path):
        if instance.instance_id == instance_id:
            return instance
    raise KeyError(f"{instance_id} not in {path}")
