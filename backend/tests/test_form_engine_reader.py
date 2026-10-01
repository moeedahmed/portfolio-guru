import json
from dataclasses import replace
from pathlib import Path

import pytest

from form_engine import (FormMap, import_kaizen, read_html, reread,
                                 save_map, load_map, suggest, verify)

FIXTURES = Path(__file__).parent / "fixtures/form_engine"


def sample(name="simple"):
    return read_html((FIXTURES / f"{name}.html").read_text(), "synthetic", name)


def test_reader_kinds_labels_options_sections_and_hints():
    fields = {f.key: f for f in sample("choices").fields}
    assert fields["stage"].kind == "select"
    assert [(o.value, o.label) for o in fields["stage"].options][:3] == [
        ("", "Choose"), ("early", "Early"), ("higher", "Higher")]
    assert fields["stage"].required and fields["stage"].section == "Training"
    assert fields["stage"].selectors == {"id": "stage", "label": "Stage of training"}
    assert fields["date"].kind == "date"
    assert fields["retained"].kind == "checkbox"
    assert fields["setting"].kind == "radio" and fields["setting"].required
    assert [o.label for o in fields["setting"].options] == ["Simulation", "Clinical"]
    assert fields["setting"].label == "Setting"
    assert {f.kind for f in sample().fields} == {"textarea", "text", "file"}


def test_alternative_labels_never_suggest_curriculum():
    fields = {f.key: f for f in sample("other_platform").fields}
    assert [(fields[k].label, fields[k].concept) for k in ("event_date", "story", "thoughts")] == [
        ("Date", "date"), ("Description", "description"), ("Reflection", "reflection")]
    assert fields["plain"].kind == "text"
    assert fields["capability"].state == "unmapped"
    assert fields["capability"].concept is None
    assert "token" not in fields and "password" not in fields
    assert all(f.section == "Learning" for f in fields.values())


def test_kaizen_real_map_ingestion_is_unverified_and_does_not_invent_options():
    path = Path(__file__).parents[1] / "unified_dom_map.json"
    legacy = json.loads(path.read_text())
    for form_id in legacy:
        form = import_kaizen(legacy, form_id)
        assert isinstance(form, FormMap) and form.platform == "kaizen"
        assert not any(f.state == "verified" for f in form.fields)
    cbd = import_kaizen(legacy, "CBD")
    assert next(f for f in cbd.fields if f.key == "startDate").concept == "date"
    assert next(f for f in cbd.fields if f.kind == "select").options == ()


def test_explicit_transitions_and_versioned_storage(tmp_path):
    form = sample()
    with pytest.raises(ValueError):
        verify(form, "mystery")
    form = suggest(form, "mystery", "evidence_note")
    assert next(f for f in form.fields if f.key == "mystery").state == "candidate"
    form = verify(form, "mystery")
    with pytest.raises(ValueError):
        suggest(form, "mystery", "description")
    path = save_map(form, tmp_path)
    assert path == tmp_path / "synthetic/simple.json"
    assert load_map(path) == form
    payload = json.loads(path.read_text())
    payload["schema_version"] = 99
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="schema"):
        load_map(path)
    with pytest.raises(ValueError):
        save_map(replace(form, platform="../escape"), tmp_path)


def test_reread_changed_and_missing_fields_demote_only_affected():
    old = verify(verify(sample(), "description"), "reflection")
    html = (FIXTURES / "simple.html").read_text()
    fresh = read_html(html.replace("required", "").replace('name="reflection"', 'name="new_reflection"'),
                      old.platform, old.form_id)
    updated = reread(old, fresh)
    fields = {f.key: f for f in updated.fields}
    assert fields["description"].state == fields["reflection"].state == "candidate"
    assert fields["description"].drift == "changed" and fields["reflection"].drift == "missing"
    assert updated.source_hash != old.source_hash
    assert reread(updated, fresh) == updated  # drift cannot clear itself
    assert reread(old, sample()) == old
    assert verify(updated, "description").fields[0].drift == ""
    with pytest.raises(ValueError):
        verify(updated, "reflection")


def test_structure_hash_ignores_values_but_detects_option_and_selector_drift():
    html = (FIXTURES / "choices.html").read_text()
    form = sample("choices")
    assert read_html(html.replace('type="date"', 'type="date" value="2026-01-01"'),
                     form.platform, form.form_id).source_hash == form.source_hash
    for changed in (html.replace("Higher", "Advanced"), html.replace('id="date"', 'id="new_date"')):
        assert read_html(changed, form.platform, form.form_id).source_hash != form.source_hash


def test_duplicate_controls_fail_closed_and_unlabelled_keys_are_stable():
    with pytest.raises(ValueError, match="Duplicate"):
        read_html('<input id="x"><input id="x">', "x", "x")
    assert read_html('<label>Title<input></label>', "x", "x").fields[0].key == read_html(
        '<label>Title  <input value="different"></label>', "x", "x").fields[0].key


def test_reread_does_not_promote_unmapped_and_preserves_unaffected_verified():
    old = verify(sample(), "reflection")
    html = (FIXTURES / "simple.html").read_text().replace("Unclassified evidence", "Description")
    updated = reread(old, read_html(html, old.platform, old.form_id))
    fields = {f.key: f for f in updated.fields}
    assert fields["reflection"].state == "verified"
    assert fields["mystery"].state == "unmapped" and fields["mystery"].concept is None


def test_prefilled_wrapping_labels_do_not_change_structure_and_radio_forms_are_not_merged():
    empty = '<label>Reflection<textarea id="r"></textarea></label>'
    prefilled = empty.replace('</textarea>', 'Synthetic note.</textarea>')
    assert read_html(empty, "x", "x").source_hash == read_html(prefilled, "x", "x").source_hash
    with pytest.raises(ValueError, match="Duplicate"):
        read_html('<form><input type="radio" name="x"></form><form><input type="radio" name="x"></form>', "x", "x")
