"""ESLE "Domains of performance" — evidence-grounded selection.

Kaizen's ESLE: Part 1 & 2 form asks, as a required question:

    "Which specific Domains of performance in this session would you like
     focused on in this ESLE?"

It is a multi-select whose visible options are ``All Domains`` plus the four
ESLE non-technical-skill domains. The form's own help text makes ``All
Domains`` exclusive: *If you choose "All Domains" please do not select the
individual domains.*

Nothing here guesses. A domain is only offered when the session's own words
evidence it; when nothing is evidenced the caller gets an empty list, which
leaves the schema-required field blank so the doctor is asked in Telegram
rather than having a claim invented for them.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, List, Sequence

ALL_DOMAINS = "All Domains"

MANAGEMENT_AND_SUPERVISION = "Management & Supervision"
TEAMWORK_AND_COOPERATION = "Teamwork & Cooperation"
DECISION_MAKING = "Decision Making"
SITUATIONAL_AWARENESS = "Situational Awareness"

INDIVIDUAL_DOMAINS: tuple[str, ...] = (
    MANAGEMENT_AND_SUPERVISION,
    TEAMWORK_AND_COOPERATION,
    DECISION_MAKING,
    SITUATIONAL_AWARENESS,
)

# Order matters: this is the order the options appear on the Kaizen widget.
ESLE_DOMAIN_OPTIONS: tuple[str, ...] = (ALL_DOMAINS,) + INDIVIDUAL_DOMAINS

# Spelling variants the model or a doctor may type for each option.
_DOMAIN_ALIASES: dict[str, str] = {
    "all domains": ALL_DOMAINS,
    "all": ALL_DOMAINS,
    "all of the domains": ALL_DOMAINS,
    "all four domains": ALL_DOMAINS,
    "management supervision": MANAGEMENT_AND_SUPERVISION,
    "management and supervision": MANAGEMENT_AND_SUPERVISION,
    "management": MANAGEMENT_AND_SUPERVISION,
    "supervision": MANAGEMENT_AND_SUPERVISION,
    "teamwork cooperation": TEAMWORK_AND_COOPERATION,
    "teamwork and cooperation": TEAMWORK_AND_COOPERATION,
    "teamwork and co operation": TEAMWORK_AND_COOPERATION,
    "teamwork": TEAMWORK_AND_COOPERATION,
    "team work": TEAMWORK_AND_COOPERATION,
    "cooperation": TEAMWORK_AND_COOPERATION,
    "decision making": DECISION_MAKING,
    "decision-making": DECISION_MAKING,
    "decisions": DECISION_MAKING,
    "situational awareness": SITUATIONAL_AWARENESS,
    "situation awareness": SITUATIONAL_AWARENESS,
    "awareness": SITUATIONAL_AWARENESS,
}

# Evidence patterns. Deliberately narrow: a generic ED word that appears in
# every case note (e.g. "department") would mark a domain on every session and
# make the answer meaningless.
_DOMAIN_EVIDENCE: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        MANAGEMENT_AND_SUPERVISION,
        (
            r"supervis\w*",
            r"delegat\w*",
            r"team leader",
            r"led the (?:team|resus\w*|arrest|shift)",
            r"leading the (?:team|resus\w*|arrest|shift)",
            r"leadership",
            r"allocat\w*",
            r"oversaw",
            r"oversight",
            r"debrief\w*",
            # "in charge" alone also matches "the nurse in charge", which is a
            # colleague in the story, not the doctor supervising anyone.
            r"\bi (?:was|am) in charge",
            r"in charge of the (?:department|shift|floor|resus\w*|team)",
            r"task[- ]manag\w*",
            r"junior doctor",
            r"foundation (?:doctor|trainee)",
        ),
    ),
    (
        TEAMWORK_AND_COOPERATION,
        (
            r"\bteam\b",
            r"teamwork",
            r"team work",
            r"co-?operation",
            r"\bmdt\b",
            r"multi[- ]?disciplinary",
            r"nurs\w+",
            r"colleague\w*",
            r"handover\w*",
            r"handed over",
            r"referral\w*",
            r"referred",
            r"liais\w*",
            r"escalat\w*",
            r"asked for help",
            r"radiograph\w+",
            r"paramedic\w*",
            r"communicat\w+ with",
        ),
    ),
    (
        DECISION_MAKING,
        (
            r"decision\w*",
            r"decided",
            r"differential\w*",
            r"diagnos\w+",
            r"management plan",
            r"treatment plan",
            r"prioriti[sz]\w*",
            r"weigh\w+ up",
            r"risk assessment",
            r"chose to",
            r"opted to",
            r"judge?ment",
            r"triage\w*",
        ),
    ),
    (
        SITUATIONAL_AWARENESS,
        (
            r"situation(?:al)? awareness",
            r"shop floor",
            r"workload",
            r"anticipat\w*",
            r"crowd\w+",
            r"capacity",
            r"patient flow",
            r"department flow",
            r"board round",
            r"waiting time\w*",
            r"kept track",
            r"re[- ]?assess\w*",
            r"bigger picture",
        ),
    ),
)

# Cues that the ESLE covered the doctor running a whole session/department
# rather than one or two encounters. Required before ``All Domains``.
_DEPARTMENT_WIDE_CUES: tuple[str, ...] = (
    r"whole shift",
    r"entire shift",
    r"full shift",
    r"whole session",
    r"entire session",
    r"across the (?:shift|department|session|floor)",
    r"department[- ]wide",
    r"whole department",
    r"shop floor",
    r"in charge of the (?:department|floor|shift)",
    r"(?:ran|running|led|leading) the (?:department|floor|shift)",
)

# How many individual domains must be evidenced before a department-wide
# session is reported as ``All Domains``.
_ALL_DOMAINS_MIN_EVIDENCED = 3

# The doctor naming the option outright. Only ever read from the doctor's own
# words, never from model-written narrative: a model that writes "all domains"
# into a reflection must not be able to widen the claim that way.
_EXPLICIT_ALL_DOMAINS_CUES: tuple[str, ...] = (
    r"\ball (?:of )?(?:the )?(?:four |4 )?domains\b",
    r"\bevery domain\b",
    r"\beach of the domains\b",
)

# "not all domains" / "rather than all domains" mean the opposite.
_ALL_DOMAINS_NEGATION = re.compile(r"(?:\bnot\b|n't|\brather than\b|\binstead of\b)\s*$")

_NORMALISE_RE = re.compile(r"[^a-z0-9]+")


def canonical_domain(value: Any) -> str | None:
    """Return the exact Kaizen option label for a loosely-written domain."""
    text = _NORMALISE_RE.sub(" ", str(value or "").lower()).strip()
    if not text:
        return None
    for option in ESLE_DOMAIN_OPTIONS:
        if text == _NORMALISE_RE.sub(" ", option.lower()).strip():
            return option
    return _DOMAIN_ALIASES.get(text)


def normalise_domains(values: Any) -> List[str]:
    """Canonicalise a selection and enforce Kaizen's ``All Domains`` rule.

    ``All Domains`` is exclusive on the form, so a mixed selection has to be
    resolved rather than sent as-is:

    - all four individual domains  -> ``["All Domains"]`` (same claim, and the
      form asks for it that way)
    - ``All Domains`` plus some individual domains -> the individual domains.
      They are the narrower, evidence-backed claim; keeping ``All Domains``
      would widen what the doctor is asserting about the session.
    """
    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        raw: Sequence[Any] = re.split(r"[;,\n]", values.decode() if isinstance(values, bytes) else values)
    elif isinstance(values, Iterable):
        raw = list(values)
    else:
        raw = [values]

    resolved: List[str] = []
    for item in raw:
        option = canonical_domain(item)
        if option and option not in resolved:
            resolved.append(option)

    individual = [option for option in INDIVIDUAL_DOMAINS if option in resolved]
    if len(individual) == len(INDIVIDUAL_DOMAINS):
        return [ALL_DOMAINS]
    if individual:
        return individual
    if ALL_DOMAINS in resolved:
        return [ALL_DOMAINS]
    return []


def _matches(patterns: Iterable[str], text: str) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def derive_domains_from_source(text: Any) -> List[str]:
    """Domains the session's own words evidence. Empty when nothing is clear."""
    body = str(text or "").lower()
    if not body.strip():
        return []

    evidenced = [
        domain for domain, patterns in _DOMAIN_EVIDENCE if _matches(patterns, body)
    ]
    if (
        len(evidenced) >= _ALL_DOMAINS_MIN_EVIDENCED
        and _matches(_DEPARTMENT_WIDE_CUES, body)
    ):
        return [ALL_DOMAINS]
    return normalise_domains(evidenced)


def states_all_domains(text: Any) -> bool:
    """True when the doctor's own words ask for ``All Domains`` outright.

    The heuristics below read a session and infer what it shows. That is the
    right default for narrative, but it leaves no way for a doctor answering
    "which domains?" with "all domains" to be heard — the phrase evidences no
    individual domain, so the answer would come back empty and the question
    would be asked again. Their explicit instruction is treated as evidence.
    """
    body = str(text or "").lower()
    for pattern in _EXPLICIT_ALL_DOMAINS_CUES:
        for match in re.finditer(pattern, body):
            preceding = body[max(0, match.start() - 20):match.start()]
            if not _ALL_DOMAINS_NEGATION.search(preceding):
                return True
    return False


def resolve_esle_domains(
    extracted: Any,
    source_text: Any,
    doctor_text: Any = None,
) -> List[str]:
    """Final domain selection for an ESLE draft.

    An extracted selection is kept only where the session text actually
    supports it, so a model that offers all four domains for a single
    resuscitation cannot inflate the claim. Anything left unsupported falls
    back to what the text evidences, and an unevidenced session returns ``[]``
    so the doctor is asked instead of being given a guess.

    ``doctor_text`` is the doctor's own words (the case note and anything they
    added when asked). An explicit "all domains" there is an instruction, not a
    guess, and settles the answer.
    """
    if states_all_domains(doctor_text):
        return [ALL_DOMAINS]

    evidenced = derive_domains_from_source(source_text)
    claimed = normalise_domains(extracted)
    if not claimed:
        return evidenced
    if not evidenced:
        return []

    if claimed == [ALL_DOMAINS]:
        # A whole-department claim stands only where the text spans the
        # session; otherwise it narrows to what the text actually shows.
        return evidenced
    if evidenced == [ALL_DOMAINS]:
        # The text covers the whole session, so a narrower claim is still true.
        return claimed

    supported = [option for option in claimed if option in evidenced]
    return normalise_domains(supported) or evidenced
