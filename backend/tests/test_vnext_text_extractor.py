"""Offline coverage for the conservative vNext text extractor.

The extractor is the *first* extraction adapter wired into the private
vNext test bot. These tests pin its safety contract: only emit facts
that appear verbatim in the source text, never invent or infer, and
return an empty tuple when the text is ambiguous so the engine stays
in ``possible_case`` and asks the user for confirmation.
"""

import pytest

from vnext_text_extractor import extract_text_facts


# --- Shorthand age/sex (62M, 45 F) ---------------------------------------


def test_shorthand_age_sex_no_space():
    assert extract_text_facts("62M with chest pain") == (
        ("age", "62"),
        ("sex", "M"),
    )


def test_shorthand_age_sex_lowercase():
    assert extract_text_facts("Saw a 45f in resus") == (
        ("age", "45"),
        ("sex", "F"),
    )


def test_shorthand_age_sex_with_space():
    assert extract_text_facts("Patient is 70 M, fall at home") == (
        ("age", "70"),
        ("sex", "M"),
    )


def test_shorthand_does_not_match_embedded_letter():
    # "15Male" has no word boundary after M, so this must not match the
    # shorthand pattern. With no "year old" phrasing either, the
    # extractor should refuse to invent facts.
    assert extract_text_facts("Code 15Male tube available") == ()


# --- "Year old" phrasing -------------------------------------------------


def test_year_old_phrase_with_sex_word():
    assert extract_text_facts("62-year-old man with crushing chest pain") == (
        ("age", "62"),
        ("sex", "M"),
    )


def test_year_old_phrase_with_female_synonym():
    assert extract_text_facts("Reviewed a 78 year old lady with sepsis") == (
        ("age", "78"),
        ("sex", "F"),
    )


def test_year_old_phrase_without_nearby_sex_keeps_age_only():
    assert extract_text_facts("82 year old presenting with dyspnoea") == (
        ("age", "82"),
    )


def test_year_old_sex_word_too_far_away_is_dropped():
    # Sex word must appear within the next 40 chars of the age match;
    # otherwise we refuse to associate it.
    text = (
        "82 year old presenting with dyspnoea, hypoxia, escalating "
        "oxygen requirement; eventually escalated to NIV. He has COPD."
    )
    assert extract_text_facts(text) == (("age", "82"),)


# --- Refusal / safety --------------------------------------------------


def test_empty_text_returns_empty_tuple():
    assert extract_text_facts("") == ()


def test_whitespace_only_text_returns_empty_tuple():
    assert extract_text_facts("   \n\t ") == ()


def test_text_without_demographics_returns_empty_tuple():
    assert extract_text_facts(
        "Had a difficult airway case in resus, managed RSI with the consultant."
    ) == ()


def test_implausible_age_is_refused():
    # Three-digit numbers above 120 are not plausible ages.
    assert extract_text_facts("Saw 250M presenting with cough") == ()


def test_portfolio_question_with_age_is_still_safe():
    # The extractor is pure; the adapter is the gate that decides whether
    # to call it. When called, it should only emit literal matches.
    assert extract_text_facts(
        "What forms support a 62M chest pain case?"
    ) == (
        ("age", "62"),
        ("sex", "M"),
    )


# --- Non-fabrication invariant -----------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Difficult airway, consultant supervised RSI.",
        "STEMI on ECG, activated cath lab.",
        "Fascia iliaca block under ultrasound guidance.",
        "Reflective practice log on missed sepsis.",
        "Just thinking about portfolio gaps before ARCP.",
    ],
)
def test_clinical_narrative_without_demographics_yields_no_facts(text):
    assert extract_text_facts(text) == ()


def test_extracted_values_appear_verbatim_in_source():
    text = "Saw 62M with chest pain in ED."
    facts = extract_text_facts(text)
    assert facts  # sanity
    for _, value in facts:
        if value in {"M", "F"}:
            # Sex is normalised from the source spelling (m/f/male/...);
            # verify the upper-cased letter appears in the text in some
            # form, not that the literal "M" appears.
            assert value.lower() in text.lower()
        else:
            assert value in text
