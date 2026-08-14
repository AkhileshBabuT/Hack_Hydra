"""Bitemporal reads: value now, value at T, and how the value changed.

Gate 3 sits on top of these -- the gate wrapper itself lives in `gates.py`, so
the whole cascade stays in one file. Everything here is a pure function of a
plain fact list, no driver and no model, for the reason the predicate gate is:
when an abstention is wrong, the window that produced it has to be reproducible
from a question string and a list of dicts.

Three reads over one fact set:

  `current`      the head of every supersession chain, which is what `status`
                 already materializes, so this is a filter not a traversal.
  `in_window`    facts whose validity interval overlaps a resolved window. An
                 open interval (`valid_to == OPEN`) is unbounded, so a fact
                 asserted in 2019 and never revised is still true in 2024.
  `history`      the chain per (subject, predicate), collapsed to the moments
                 the value actually changed, each with the time it changed.

Window resolution is lexical, by the same rule that binds every other gate: a
gate that decides whether the model may speak cannot itself be a model. The
table below is deliberately small. A phrasing it cannot read yields no window,
and no window means the gate passes -- see `gates.BIAS`.
"""

import datetime as dt
import re

OPEN = 0          # valid_to sentinel: this fact has no end, it is still true
DAY = 86400


class Window:
    """A half-open interval [start, end) in epoch seconds. `end == OPEN` is unbounded.

    Carries the phrase it was resolved from: an abstention that names the
    window it searched is auditable, one that just says no is not.
    """

    __slots__ = ("start", "end", "phrase")

    def __init__(self, start: int, end: int, phrase: str = ""):
        self.start, self.end, self.phrase = int(start), int(end), phrase

    def __eq__(self, other):
        return (isinstance(other, Window)
                and (self.start, self.end) == (other.start, other.end))

    def __repr__(self):
        return f"Window({self.label})"

    @staticmethod
    def _day(epoch: int) -> str:
        return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%d")

    @property
    def label(self) -> str:
        end = "onwards" if self.end == OPEN else self._day(self.end)
        return f"{self._day(self.start)}..{end}"

    @property
    def detail(self) -> str:
        return f"{self.phrase} ({self.label})" if self.phrase else self.label


def _utc(year: int, month: int = 1, day: int = 1) -> int:
    return int(dt.datetime(year, month, day, tzinfo=dt.timezone.utc).timestamp())


def _month_end(year: int, month: int) -> int:
    return _utc(year + 1, 1) if month == 12 else _utc(year, month + 1)


MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))
_YEAR = r"(?:19|20)\d{2}"
_COUNTS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}

# Matched in order. The directional forms go first because "before 2019" also
# contains the shape "<preposition> 2019" and would otherwise resolve to the
# year itself -- the exact opposite window.
#
# ponytail: a literal table with no bare-month form. "in May" is not
# distinguishable from the verb without a capitalisation heuristic, and a
# window resolved wrong is worse than no window at all, because a wrong window
# abstains silently. Add the bare month when a measured miss says it matters.
_ISO = re.compile(rf"\b(?:on|as of)\s+(?P<y>{_YEAR})-(?P<m>\d{{1,2}})-(?P<d>\d{{1,2}})\b")
_BEFORE = re.compile(
    rf"\b(?:before|prior to|up to|until|earlier than)\s+(?:the\s+)?(?P<y>{_YEAR})\b"
)
_SINCE = re.compile(rf"\b(?:since|after|from)\s+(?:the\s+)?(?P<y>{_YEAR})\b")
_MONTH_YEAR = re.compile(rf"\b(?P<m>{_MONTH_ALT})\s+(?:of\s+)?(?P<y>{_YEAR})\b")
_IN_YEAR = re.compile(
    rf"\b(?:in|during|back in|around|as of|throughout|over|of)"
    rf"\s+(?:the\s+)?(?:year\s+)?(?P<y>{_YEAR})\b"
)
_LAST_UNIT = re.compile(r"\b(?:last|previous|past)\s+(?P<u>year|month|week)\b")
_AGO = re.compile(
    r"\b(?P<n>\d{1,2}|an?|one|two|three|four|five)\s+(?P<u>year|month|week|day)s?\s+ago\b"
)


def parse_window(question: str, asked_at: int = None):
    """The time window the question scopes itself to, or None if it names none.

    None is the common answer and the safe one: without a window the temporal
    gate passes and the question is answered against the current value.
    Relative phrasing ("last year") needs `asked_at`; without an anchor the
    phrase resolves to nothing rather than to today, because resolving it
    against the wall clock would make an evaluation unreproducible.
    """
    text = question.lower()

    match = _ISO.search(text)
    if match:
        year, month, day = (int(match.group(g)) for g in ("y", "m", "d"))
        try:
            start = _utc(year, month, day)
        except ValueError:
            return None                      # "on 2019-13-40" names no window
        return Window(start, start + DAY, match.group(0))

    match = _BEFORE.search(text)
    if match:
        return Window(0, _utc(int(match.group("y"))), match.group(0))

    match = _SINCE.search(text)
    if match:
        return Window(_utc(int(match.group("y"))), OPEN, match.group(0))

    match = _MONTH_YEAR.search(text)
    if match:
        month, year = MONTHS[match.group("m")], int(match.group("y"))
        return Window(_utc(year, month), _month_end(year, month), match.group(0))

    match = _IN_YEAR.search(text)
    if match:
        year = int(match.group("y"))
        return Window(_utc(year), _utc(year + 1), match.group(0))

    if asked_at is None:
        return None
    asked = dt.datetime.fromtimestamp(asked_at, dt.timezone.utc)

    match = _LAST_UNIT.search(text)
    if match:
        return _relative(asked, 1, match.group("u"), match.group(0))

    match = _AGO.search(text)
    if match:
        raw = match.group("n")
        count = int(raw) if raw.isdigit() else _COUNTS[raw]
        return _relative(asked, count, match.group("u"), match.group(0))

    return None


def _relative(asked: dt.datetime, count: int, unit: str, phrase: str) -> Window:
    """`count` units before the question was asked, as an interval.

    Years and months resolve to the calendar year or month, which is what "last
    year" means to a reader. Weeks and days resolve to a rolling offset, since
    "three days ago" implies no calendar boundary.
    """
    if unit == "year":
        year = asked.year - count
        return Window(_utc(year), _utc(year + 1), phrase)
    if unit == "month":
        month = asked.month - count
        year, month = asked.year + (month - 1) // 12, (month - 1) % 12 + 1
        return Window(_utc(year, month), _month_end(year, month), phrase)
    span = DAY * (7 if unit == "week" else 1)
    end = int(asked.timestamp()) - span * (count - 1)
    return Window(end - span, end, phrase)


# --- the three reads ------------------------------------------------------

def current(facts: list) -> list:
    """The head of every chain. `status` is that head, materialized by slice 05."""
    return [f for f in facts if f.get("status", "current") == "current"]


def overlaps(fact: dict, window: Window) -> bool:
    """Does this fact's validity interval meet the window at all?

    `valid_to == OPEN` means the fact was never revised, so it is still true.
    That is the single most important line here: most facts are open, and a
    naive `valid_to >= window.start` would exclude every one of them.
    """
    valid_to = fact.get("valid_to", OPEN)
    if valid_to != OPEN and valid_to <= window.start:
        return False
    if window.end != OPEN and fact.get("valid_from", 0) >= window.end:
        return False
    return True


def in_window(facts: list, window: Window) -> list:
    return [f for f in facts if overlaps(f, window)]


def at_time(facts: list, when: int) -> list:
    """Value-at-T: the facts whose interval contains the instant `when`."""
    return in_window(facts, Window(when, when + 1))


def _slot(fact: dict):
    return (fact.get("subject_key", ""), fact.get("predicate", ""))


def _ordered(facts: list) -> list:
    """The total order `chain.derive` pairs on, so history and the graph agree."""
    return sorted(facts, key=lambda f: (f.get("valid_from", 0), f.get("asserted_at", 0),
                                        f.get("turn_idx", 0), f.get("id", 0)))


def _same(left: dict, right: dict) -> bool:
    return (left.get("value_text", "").strip().lower()
            == right.get("value_text", "").strip().lower())


def since(facts: list) -> int:
    """When the newest value *became* true, not when it was last restated.

    A restatement is a new Fact node with a later `valid_from`, so the head of
    the chain answers "since when has X been true" with the most recent
    mention. Walking back over the unchanged run fixes it at read time, where
    it is observable, rather than at ingest, where fixing it would cost a node
    wipe (see CLAUDE.md, "Re-ingesting after an extractor change").
    """
    ordered = _ordered(facts)
    if not ordered:
        return 0
    head = ordered[-1]
    start = head.get("valid_from", 0)
    for fact in reversed(ordered[:-1]):
        if not _same(fact, head):
            break
        start = min(start, fact.get("valid_from", 0))
    return start


def history(facts: list) -> dict:
    """(subject_key, predicate) -> [(changed_at, value, fact_id)], oldest first.

    The chain collapsed to the moments the value changed: a value restated
    unchanged is one entry, not three. `changed_at` is the `valid_from` of the
    fact that introduced the value, so a two-entry history is exactly the "what
    did X used to be, and when did it stop" answer.
    """
    slots = {}
    for fact in facts:
        slots.setdefault(_slot(fact), []).append(fact)

    out = {}
    for slot, group in slots.items():
        changes = []
        for fact in _ordered(group):
            if changes and _same(fact, changes[-1][-1]):
                continue                      # a restatement is not a change
            changes.append((fact.get("valid_from", 0), fact.get("value_text", ""),
                            fact.get("fact_id", ""), fact))
        out[slot] = [entry[:3] for entry in changes]
    return out
