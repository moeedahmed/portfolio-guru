"""Reflective Practice Log draft-quality guardrails."""

from extractor import _polish_reflect_log_fields
from form_schemas import FORM_SCHEMAS


# Voice transcript Moeed sent during the May 2026 beta regression:
# RUQ pain + fever + sepsis features + surgical escalation + handover reflection.
# The bot produced a draft where title, date_of_event, type-of-event, why, and
# different_outcome were all blank and focussing_on copied replay_differently
# verbatim. The polish layer must fix every one of those fields without
# inventing clinical content.
MOEED_RUQ_SEPSIS_CASE = (
    "okay so I saw 42 years old woman in ED with right upper quadrant abdominal pain "
    "vomiting and fever. she was tachycardic at 118 temperature 38.4 blood pressure "
    "104 over 68 with tenderness and guarding in the right upper quadrant. I considered "
    "acute cholecystitis with possible sepsis. I inserted a cannula sent bloods including "
    "LFTs and cultures started IV fluids analgesia and antibiotics and requested an "
    "urgent ultrasound. I escalated to surgical registrar and discussed with my ED senior "
    "because of the sepsis features. so the reflections were that this was a good case "
    "for prioritizing sepsis treatment while keeping the surgical diagnosis in mind. I "
    "want to improve my handover structure when referring unwell surgical patients"
)


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


def test_moeed_ruq_sepsis_beta_regression():
    """End-to-end regression for the May 2026 beta broken draft.

    The LLM produced: blank title, blank date_of_event, blank event_type,
    blank why, blank different_outcome, and focussing_on that was a verbatim
    copy of replay_differently. All seven must be fixed without inventing
    clinical facts not present in the source transcript.
    """
    # Exact broken LLM output fields as reported from the beta
    broken_fields = {
        "date_of_encounter": "27/5/2026",
        "reflection_title": "",
        "date_of_event": "",
        "event_type": "",
        "reflection": (
            "I saw a 42-year-old woman in ED with right upper quadrant abdominal pain, "
            "vomiting and fever. She was tachycardic at 118, temperature 38.4, BP 104/68 "
            "with RUQ tenderness and guarding. I considered acute cholecystitis with possible "
            "sepsis, inserted a cannula, sent bloods including LFTs and cultures, started IV "
            "fluids, analgesia and antibiotics, and requested an urgent ultrasound. I escalated "
            "to the surgical registrar and discussed with my ED senior because of the sepsis features."
        ),
        "replay_differently": (
            "I would improve my handover structure when referring unwell surgical patients."
        ),
        "why": "",
        "different_outcome": "",
        "focussing_on": (
            "I would improve my handover structure when referring unwell surgical patients."
        ),
        "learned": (
            "I learned the importance of prioritising sepsis treatment while maintaining "
            "awareness of the underlying surgical diagnosis."
        ),
    }

    polished = _polish_reflect_log_fields(broken_fields, MOEED_RUQ_SEPSIS_CASE)

    # Title derived and references sepsis or surgical referral
    assert polished["reflection_title"], "reflection_title must not be blank"
    title_lower = polished["reflection_title"].lower()
    assert any(w in title_lower for w in ("sepsis", "referral", "surgical")), (
        f"title should reference sepsis or surgical referral, got: {polished['reflection_title']!r}"
    )

    # date_of_event copied from date_of_encounter
    assert polished["date_of_event"] == "27/5/2026", (
        f"date_of_event should be copied from date_of_encounter, got: {polished['date_of_event']!r}"
    )

    # event_type set for ED context
    assert polished["event_type"], "event_type must not be blank for an ED encounter"
    assert polished["event_type"] in FORM_SCHEMAS["REFLECT_LOG"]["fields"][3]["options"], (
        f"event_type must be a valid schema option, got: {polished['event_type']!r}"
    )

    # why is filled and anchors the sepsis-vs-surgical-referral tension
    assert polished["why"], "why must not be blank"
    why_lower = polished["why"].lower()
    assert any(w in why_lower for w in ("sepsis", "source", "referral", "surgical")), (
        f"why should explain sepsis/surgical referral tension, got: {polished['why']!r}"
    )

    # different_outcome filled, references shared understanding / urgency, does not claim
    # a different clinical outcome as established fact
    assert polished["different_outcome"], "different_outcome must not be blank"
    diff_lower = polished["different_outcome"].lower()
    assert any(w in diff_lower for w in ("shared", "urgency", "referral", "structured", "explicit", "clearer")), (
        f"different_outcome should reference structured referral or shared mental model, "
        f"got: {polished['different_outcome']!r}"
    )
    # Must qualify the outcome claim, not assert a different patient course as fact
    assert "clinical course" in diff_lower or "would" in diff_lower or "could" in diff_lower, (
        f"different_outcome must hedge rather than assert a different patient outcome, "
        f"got: {polished['different_outcome']!r}"
    )

    # focussing_on must NOT be a copy of replay_differently and must name a concrete action
    assert polished["focussing_on"] != broken_fields["replay_differently"], (
        "focussing_on must not be identical to replay_differently"
    )
    focus_lower = polished["focussing_on"].lower()
    assert any(w in focus_lower for w in ("sbar", "structured", "referral")), (
        f"focussing_on should specify SBAR or structured referral, got: {polished['focussing_on']!r}"
    )

    # learned is already substantive — must be preserved unchanged
    assert polished["learned"] == broken_fields["learned"], (
        "learned was already good and must not be overwritten"
    )


def test_stemi_communication_avoids_absolute_no_phrasing():
    """When a STEMI case has an LLM-generated absolute 'No, the clinical
    outcome would remain the same', the polish layer must replace it with
    softer communication-quality framing that doesn't dismiss the value of
    the reflection."""
    fields = {
        "replay_differently": "I would have explained the STEMI diagnosis more clearly to the patient and family.",
        "different_outcome": "No, the clinical outcome would remain the same as the STEMI was identified and treated appropriately with primary PCI.",
        "focussing_on": "",
    }
    polished = _polish_reflect_log_fields(
        fields,
        "STEMI identified in ED, patient anxious about the diagnosis and prognosis, taken for primary PCI",
    )
    diff_lower = polished["different_outcome"].lower()
    assert "no, the clinical outcome would remain the same" not in diff_lower, (
        f"Absolute 'No clinical outcome change' phrasing must be replaced for "
        f"communication-quality cases. Got: {polished['different_outcome']!r}"
    )
    assert any(w in diff_lower for w in ("communication", "understanding", "anxiety", "clearer", "may have", "appropriate")), (
        f"different_outcome should use softer communication-quality framing. "
        f"Got: {polished['different_outcome']!r}"
    )


def test_communication_case_without_stemi_also_avoids_absolute_no():
    """The absolute-no guard applies to any case where communication is the
    focus, not only STEMI — e.g. a handover case where the patient outcome
    was fine but communication could have been clearer."""
    fields = {
        "replay_differently": "I would have communicated the diagnosis more clearly.",
        "different_outcome": "No. The clinical outcome would be the same as the patient was treated correctly.",
        "focussing_on": "",
    }
    polished = _polish_reflect_log_fields(
        fields,
        "Patient anxious after diagnosis; I explained the plan but communication could have been clearer",
    )
    diff_lower = polished["different_outcome"].lower()
    assert "no." not in diff_lower or "clinical outcome would be the same" not in diff_lower, (
        f"Absolute-no phrasing must be softened when communication context is present. "
        f"Got: {polished['different_outcome']!r}"
    )
