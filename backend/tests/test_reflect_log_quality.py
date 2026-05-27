"""Reflective Practice Log draft-quality guardrails."""

from extractor import _polish_reflect_log_fields


def test_sepsis_derives_title_when_blank():
    """Empty title gets derived from sepsis context."""
    fields = {
        "reflection_title": "",
        "replay_differently": "",
        "focussing_on": "",
    }
    polished = _polish_reflect_log_fields(fields, "sepsis secondary to biliary source")
    assert "sepsis" in polished["reflection_title"].lower()


def test_surgical_ref_derives_title_when_blank():
    fields = {
        "reflection_title": "",
        "replay_differently": "",
        "focussing_on": "",
    }
    polished = _polish_reflect_log_fields(fields, "surgical referral for acute cholecystitis")
    assert "referral" in polished["reflection_title"].lower()


def test_reflect_log_copies_encounter_date_to_event_date_when_blank():
    fields = {
        "date_of_encounter": "2026-05-27",
        "date_of_event": "",
        "replay_differently": "",
        "focussing_on": "",
    }
    polished = _polish_reflect_log_fields(fields, "sepsis from cholecystitis")
    assert polished["date_of_event"] == "2026-05-27"


def test_sepsis_derives_why_when_empty():
    fields = {
        "replay_differently": "",
        "focussing_on": "",
        "why": "",
    }
    polished = _polish_reflect_log_fields(fields, "sepsis from UTI")
    assert polished["why"] != ""
    assert "sepsis" in polished["why"].lower()


def test_sepsis_derives_why_when_repetitive_with_replay():
    fields = {
        "replay_differently": "I would treat sepsis earlier with antibiotics.",
        "focussing_on": "treat sepsis earlier",
        "why": "I would treat sepsis earlier with antibiotics.",  # near-identical to replay
    }
    polished = _polish_reflect_log_fields(fields, "sepsis recognition delayed")
    assert polished["why"] != fields["why"]
    assert "source identification" in polished["why"].lower()


def test_sepsis_derives_different_outcome_when_empty():
    fields = {
        "replay_differently": "",
        "focussing_on": "",
        "different_outcome": "",
    }
    polished = _polish_reflect_log_fields(fields, "sepsis secondary to pneumonia")
    assert polished["different_outcome"] != ""
    assert "antibiotic" in polished["different_outcome"].lower()


def test_surgical_ref_derives_different_outcome_when_empty():
    fields = {
        "replay_differently": "",
        "focussing_on": "",
        "different_outcome": "",
    }
    polished = _polish_reflect_log_fields(fields, "referred to surgical registrar")
    assert polished["different_outcome"] != ""
    assert "referral" in polished["different_outcome"].lower()


def test_sepsis_focussing_on_is_not_replay_rewrite():
    fields = {
        "replay_differently": "I would treat sepsis earlier.",
        "focussing_on": "I am focusing on treating sepsis earlier.",
    }
    polished = _polish_reflect_log_fields(fields, "sepsis from cholecystitis")
    assert polished["focussing_on"] != fields["focussing_on"]
    assert "source" in polished["focussing_on"].lower()


def test_surgical_ref_focussing_on_is_not_replay_rewrite():
    fields = {
        "replay_differently": "I would improve my handover when referring surgical patients.",
        "focussing_on": "I am focussing on improving my handover when referring surgical patients.",
    }
    polished = _polish_reflect_log_fields(fields, "referred surgical patient")
    assert polished["focussing_on"] != fields["focussing_on"]
    assert "SBAR" in polished["focussing_on"]


def test_no_change_when_fields_are_distinct_and_unrelated():
    """If replay and focus are distinct, the original values are preserved
    even when the case context triggers sepsis/surgical derivation. The derived
    fields (title, why, different_outcome) are populated, but existing distinct
    focus is left untouched."""
    fields = {
        "replay_differently": "I would contact the surgical registrar earlier for unwell biliary sepsis patients.",
        "focussing_on": "I am practising a two-minute SBAR referral that states physiology, source control concern and the decision needed.",
    }
    polished = _polish_reflect_log_fields(fields, "biliary sepsis")
    assert polished["replay_differently"] == fields["replay_differently"]
    assert polished["focussing_on"] == fields["focussing_on"]
    assert "sepsis" in polished["reflection_title"].lower()
    assert polished["why"] != ""
    assert polished["different_outcome"] != ""


def test_respects_existing_title_when_present():
    fields = {
        "reflection_title": "My custom title",
        "replay_differently": "",
        "focussing_on": "",
    }
    polished = _polish_reflect_log_fields(fields, "sepsis from cholecystitis")
    assert polished["reflection_title"] == "My custom title"


def test_respects_existing_why_when_distinct():
    fields = {
        "replay_differently": "I would give earlier antibiotics.",
        "focussing_on": "treat sepsis early",
        "why": "I anchored on the normal blood pressure and missed the rising lactate trend.",
    }
    polished = _polish_reflect_log_fields(fields, "sepsis recognition")
    assert polished["why"] == fields["why"]


def test_non_sepsis_non_surgical_is_untouched_when_fields_distinct():
    fields = {
        "replay_differently": "I would ask for a senior review earlier.",
        "focussing_on": "I am building a habit of escalating when in doubt.",
    }
    assert _polish_reflect_log_fields(fields, "chest pain, ECG normal") is fields


def test_non_sepsis_non_surgical_still_fixes_repetitive_focus():
    fields = {
        "replay_differently": "I would escalate earlier next time whenever I have uncertainty.",
        "focussing_on": "Escalate earlier whenever I have uncertainty.",
    }
    polished = _polish_reflect_log_fields(fields, "chest pain case")
    assert polished["focussing_on"] != fields["focussing_on"]
    assert "next-shift habit" in polished["focussing_on"]
