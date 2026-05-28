"""Conservative vNext text → CaseFact extractor.

This is the *first* extraction adapter for the private vNext test bot.
It is intentionally narrow: from a free-text Telegram message it only
pulls demographic facts (age, sex) that appear verbatim in the source.
Nothing here calls the network, an LLM, or any pre-existing
``extractor.py`` pipeline, and nothing here is invoked for
voice/image/document inputs — those stay stricter and unconfirmed in
this slice.

Why limit to demographics?

* They are usually written as literal tokens (``62M``, ``45F``,
  ``62-year-old male``) directly in the doctor's own text.
* They never require clinical inference, so the source-tied invariant
  the engine relies on is easy to prove by inspection.
* Anything richer (diagnosis, plan, supervision level) would need a
  proper extraction model, and the engine is happy to stay in
  ``possible_case`` until that model exists.

The function is total: when the text is missing demographic markers it
returns an empty tuple, which keeps the engine in its provisional
``possible_case`` state and lets the orchestrator ask the user for
more detail before drafting anything.
"""

from __future__ import annotations

import re

_SHORTHAND_AGE_SEX_RE = re.compile(r"\b(\d{1,3})\s?([MmFf])\b")
_AGE_YEAR_OLD_RE = re.compile(
    r"\b(\d{1,3})[\s\-]year[\s\-]old\b", re.IGNORECASE
)
_SEX_PHRASE_RE = re.compile(
    r"\b(male|female|man|woman|boy|girl|gentleman|lady)\b", re.IGNORECASE
)

_MALE_WORDS: frozenset[str] = frozenset({"male", "man", "boy", "gentleman"})
_FEMALE_WORDS: frozenset[str] = frozenset({"female", "woman", "girl", "lady"})

_MAX_AGE = 120


def extract_text_facts(text: str) -> tuple[tuple[str, str], ...]:
    """Return source-tied ``(key, value)`` demographic facts from ``text``.

    The function is pure and conservative: it only emits facts whose value
    appears verbatim in ``text``. When no demographic marker is present it
    returns an empty tuple so the engine stays in ``possible_case`` and
    the orchestrator can ask for confirmation before drafting.
    """

    if not text or not text.strip():
        return ()

    shorthand = _SHORTHAND_AGE_SEX_RE.search(text)
    if shorthand and _is_plausible_age(shorthand.group(1)):
        return (
            ("age", shorthand.group(1)),
            ("sex", shorthand.group(2).upper()),
        )

    age_match = _AGE_YEAR_OLD_RE.search(text)
    if not age_match or not _is_plausible_age(age_match.group(1)):
        return ()

    facts: list[tuple[str, str]] = [("age", age_match.group(1))]
    tail = text[age_match.end() : age_match.end() + 40]
    sex_match = _SEX_PHRASE_RE.search(tail)
    if sex_match:
        normalised = _normalise_sex(sex_match.group(1))
        if normalised:
            facts.append(("sex", normalised))
    return tuple(facts)


def _is_plausible_age(value: str) -> bool:
    try:
        age = int(value)
    except ValueError:
        return False
    return 0 <= age <= _MAX_AGE


def _normalise_sex(word: str) -> str | None:
    lowered = word.lower()
    if lowered in _MALE_WORDS:
        return "M"
    if lowered in _FEMALE_WORDS:
        return "F"
    return None
