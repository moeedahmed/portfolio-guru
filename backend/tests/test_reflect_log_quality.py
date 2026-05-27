"""Reflective Practice Log draft-quality guardrails."""

from extractor import _polish_reflect_log_fields


def test_reflect_log_focus_field_is_not_reworded_replay_for_handover_case():
    fields = {
        "replay_differently": "I would improve my handover structure when referring unwell surgical patients.",
        "focussing_on": "I am focussing on improving my handover structure when referring unwell surgical patients.",
    }

    polished = _polish_reflect_log_fields(
        fields,
        "Possible sepsis from acute cholecystitis. I referred to the surgical registrar.",
    )

    assert polished["replay_differently"] == fields["replay_differently"]
    assert polished["focussing_on"] != fields["focussing_on"]
    assert "SBAR-style referral" in polished["focussing_on"]
    assert "working diagnosis" in polished["focussing_on"]


def test_reflect_log_focus_field_keeps_distinct_specific_content():
    fields = {
        "replay_differently": "I would contact the surgical registrar earlier for unwell biliary sepsis patients.",
        "focussing_on": "I am practising a two-minute referral that states physiology, source control concern and the decision needed.",
    }

    assert _polish_reflect_log_fields(fields, "biliary sepsis") is fields
