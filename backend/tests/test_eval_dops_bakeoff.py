"""Offline tests for the DOPS bake-off scorer.

The scoring function is the only part of `eval_dops_bakeoff` that does not
require live API keys, so it lives behind a clean unit test gate. The actual
provider calls are exercised manually via the CLI.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from eval_dops_bakeoff import score_dops_extraction  # noqa: E402


FULL_DOPS = {
    "procedure_name": "DC cardioversion",
    "indication": (
        "Unstable atrial fibrillation with rapid ventricular response and "
        "hypotension requiring emergency cardioversion."
    ),
    "trainee_performance": (
        "I led the synchronised cardioversion under ketamine sedation, "
        "delivered three escalating shocks, recognised refractory rhythm "
        "and escalated to ITU."
    ),
    "reflection": (
        "Reinforced the value of early ITU escalation when rhythm fails "
        "to convert and the patient remains compromised. Next time I will "
        "request the med reg sooner."
    ),
    "key_capabilities": [
        "SLO3 KC2: be expert in fluid management and circulatory support",
        "SLO3 KC3: manage all life-threatening conditions",
        "SLO3 KC5: effectively lead and support resuscitation teams",
        "SLO6 KC2: perform EM procedural skills safely and in a timely fashion",
    ],
}


def test_score_dops_extraction_full_draft_is_high_quality():
    scores = score_dops_extraction(FULL_DOPS)
    assert scores["procedure"] == 1.0
    assert scores["indication"] >= 0.9
    assert scores["trainee_performance"] >= 0.9
    assert scores["reflection"] >= 0.9
    assert scores["kc_links"] == 1.0
    assert scores["grammar"] >= 0.6
    assert scores["overall"] >= 0.85


def test_score_dops_extraction_drops_when_indication_missing():
    fields = dict(FULL_DOPS)
    fields["indication"] = ""
    scores = score_dops_extraction(fields)
    assert scores["indication"] == 0.0
    assert scores["overall"] < score_dops_extraction(FULL_DOPS)["overall"]


def test_score_dops_extraction_drops_when_trainee_performance_missing():
    fields = dict(FULL_DOPS)
    fields["trainee_performance"] = ""
    scores = score_dops_extraction(fields)
    assert scores["trainee_performance"] == 0.0


def test_score_dops_extraction_reflection_short_fragment_scores_zero():
    fields = dict(FULL_DOPS)
    fields["reflection"] = "ok done"
    scores = score_dops_extraction(fields)
    assert scores["reflection"] == 0.0
    # Grammar score also drops because the reflection no longer contributes
    # a coherent sentence; the trainee performance text alone still has
    # several well-formed sentences so this does not crash to zero.
    assert scores["grammar"] < score_dops_extraction(FULL_DOPS)["grammar"] + 0.01


def test_score_dops_extraction_kc_links_full_credit_at_four_or_more():
    fields = dict(FULL_DOPS)
    fields["key_capabilities"] = ["SLO3 KC3: only one KC"]
    scores = score_dops_extraction(fields)
    assert scores["kc_links"] == 0.25
    fields["key_capabilities"] = FULL_DOPS["key_capabilities"][:2]
    scores = score_dops_extraction(fields)
    assert scores["kc_links"] == 0.5


def test_score_dops_extraction_empty_input_is_zero():
    scores = score_dops_extraction({})
    assert scores["overall"] == 0.0


def test_score_dops_extraction_procedural_skill_fallback():
    # Some extractors emit `procedural_skill` (the 2025 Kaizen label) instead
    # of `procedure_name`. The scorer must accept either.
    scores = score_dops_extraction({"procedural_skill": "DC cardioversion"})
    assert scores["procedure"] == 1.0
