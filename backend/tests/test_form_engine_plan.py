import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from form_engine import (FillStep, execute, make_plan, read_html, reread, suggest, verify)
from form_engine.models import Action

FIXTURES = Path(__file__).parent / "fixtures/form_engine"


def mapped(name="simple"):
    form = read_html((FIXTURES / f"{name}.html").read_text(), "synthetic", name)
    for field in form.fields:
        if field.state == "candidate":
            form = verify(form, field.key)
    return form


def test_deterministic_plan_has_fixed_order_and_preserves_only_supplied_text():
    form = mapped()
    values = {"reflection": "I practised explaining the plan.", "description": "Simulation exercise."}
    first = make_plan(form, values)
    assert first.to_json() == make_plan(form, dict(reversed(list(values.items())))).to_json()
    assert [s.field_key for s in first.steps] == ["description", "reflection"]
    assert [s.value for s in first.steps] == [values["description"], values["reflection"]]


def test_unmapped_candidate_unknown_and_missing_values_are_reported():
    form = read_html((FIXTURES / "simple.html").read_text(), "x", "x")
    values = {"description": "Given", "curriculum": "Not inferred"}
    plan = make_plan(form, values)
    reasons = {s.reason for s in plan.skipped}
    assert not plan.steps and {"candidate_not_allowed", "unmapped", "unknown_value"} <= reasons
    allowed = make_plan(form, values, allow_candidates=True)
    assert [s.field_key for s in allowed.steps] == ["description"]
    assert "missing_value" in {s.reason for s in allowed.skipped}


def test_plan_model_and_executor_cannot_submit_sign_send_or_approve():
    assert {a.value for a in Action} == {"fill", "select", "choose", "check", "set_file"}
    for forbidden in ("submit", "sign", "send", "approve", "click"):
        with pytest.raises(ValueError):
            FillStep("x", {"by": "id", "value": "x"}, forbidden, "x")
    assert not any(s.action in {"submit", "sign", "send", "approve"}
                   for s in make_plan(mapped(), {"description": "Given"}).steps)


@pytest.mark.parametrize("url", ["https://kaizenep.com/form", "http://example.org", "file://remote/form.html",
                                   "//remote/form.html", "file://user@localhost/a", "http://localhost/form"])
def test_executor_refuses_urls_without_opening_a_browser(url):
    with pytest.raises(ValueError):
        execute(make_plan(mapped(), {}), url)


def test_round_trip_all_kinds_and_local_file_uri(tmp_path):
    form = mapped("choices")
    for key, concept in (("setting", "setting"), ("retained", "retained")):
        form = verify(suggest(form, key, concept), key)
    values = {"stage_of_training": "higher", "date": "2026-01-02", "setting": "simulation", "retained": True}
    plan = make_plan(form, values)
    assert not plan.skipped
    result = execute(plan, (FIXTURES / "choices.html").resolve().as_uri())
    assert result == {"stage": "higher", "date": "2026-01-02", "setting": "simulation", "retained": True}
    simple = verify(suggest(mapped(), "evidence", "evidence_file"), "evidence")
    simple_plan = make_plan(simple, {"description": "Simulation.", "reflection": "Practise.",
                                    "evidence_file": "synthetic.pdf"})
    filled = execute(simple_plan, FIXTURES / "simple.html")
    assert filled["evidence"] == "synthetic.pdf" and filled["reflection"] == "Practise."


@pytest.mark.parametrize("values", [{"stage_of_training": "Higher"}, {"stage_of_training": "retired"},
                                     {"date": "02/01/2026"}, {"date": "2026-02-30"}])
def test_invalid_values_are_skipped_not_guessed(values):
    plan = make_plan(mapped("choices"), values)
    assert not plan.steps and "invalid_value" in {s.reason for s in plan.skipped}


def test_stale_plan_and_changed_missing_mapping_cannot_execute(tmp_path):
    form = mapped()
    plan = make_plan(form, {"description": "Given"})
    changed = (FIXTURES / "simple.html").read_text().replace("required", "")
    path = tmp_path / "changed.html"
    path.write_text(changed)
    with pytest.raises(ValueError, match="structure"):
        execute(plan, path)
    fresh = read_html(changed, form.platform, form.form_id)
    updated = reread(form, fresh)
    assert not make_plan(updated, {"description": "Given"}, allow_candidates=True).steps
    forged = replace(plan, steps=(replace(plan.steps[0], selector={"by": "id", "value": "mystery"}),))
    with pytest.raises(ValueError):
        execute(forged, FIXTURES / "simple.html")


def test_disabled_readonly_multiple_and_ambiguous_concepts_are_not_filled():
    html = '<input id="a" aria-label="Description" disabled><textarea id="b" aria-label="Reflection" readonly></textarea><select id="c" aria-label="Stage of training" multiple><option value="higher">Higher</option></select>'
    form = read_html(html, "x", "x")
    for f in form.fields:
        form = verify(form, f.key)
    assert not make_plan(form, {"description": "Given", "reflection": "Given", "stage_of_training": "higher"}).steps
    form = read_html('<input id="a" aria-label="Description"><input id="b" aria-label="Description">', "x", "x")
    for f in form.fields:
        form = verify(form, f.key)
    assert {s.reason for s in make_plan(form, {"description": "Given"}).skipped} == {"ambiguous_concept"}


def test_cli_read_verify_and_plan_without_environment_or_live_imports(tmp_path):
    root = Path(__file__).parents[2]
    def cli(*args):
        return subprocess.run([sys.executable, "-m", "backend.form_engine", *map(str, args)],
                              cwd=root, capture_output=True, text=True)
    read = cli("read", FIXTURES / "simple.html", "--platform", "synthetic", "--form", "simple",
               "--output-dir", tmp_path)
    assert read.returncode == 0, read.stderr
    path = tmp_path / "synthetic/simple.json"
    assert cli("verify", path, "--field", "description").returncode == 0
    assert cli("verify", path, "--field", "mystery", "--concept", "evidence_note").returncode == 0
    values = tmp_path / "values.json"
    values.write_text(json.dumps({"description": "Synthetic exercise."}))
    plan = cli("plan", path, values)
    assert plan.returncode == 0 and json.loads(plan.stdout)["steps"][0]["value"] == "Synthetic exercise."
    isolated = subprocess.run([sys.executable, "-c", "import sys; import backend.form_engine; "
                               "assert not {'bot','credentials','kaizen_form_filer'} & set(sys.modules)"],
                              cwd=root, capture_output=True)
    assert isolated.returncode == 0


def test_protected_checkbox_and_wrong_checkbox_type_are_never_filled():
    form = read_html('<label>Approve<input id="approve" type="checkbox"></label>', "x", "x")
    form = verify(suggest(form, "approve", "given_approval"), "approve")
    assert not make_plan(form, {"given_approval": True}).steps
    form = read_html('<label>Retained<input id="r" type="checkbox" checked></label>', "x", "x")
    form = verify(suggest(form, "r", "retained"), "r")
    assert not make_plan(form, {"retained": "false"}).steps


def test_cli_reread_keeps_reviews_and_flags_drift(tmp_path):
    root = Path(__file__).parents[2]
    source = tmp_path / "form.html"
    source.write_text('<label for="d">Description</label><input id="d" required>')
    def cli(*args):
        result = subprocess.run([sys.executable, "-m", "backend.form_engine", *map(str, args)],
                                cwd=root, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    cli("read", source, "--platform", "x", "--form", "y", "--output-dir", tmp_path)
    cli("verify", tmp_path / "x/y.json", "--field", "d")
    source.write_text(source.read_text().replace(" required", ""))
    updated = cli("read", source, "--platform", "x", "--form", "y", "--output-dir", tmp_path)
    assert updated["fields"][0]["state"] == "candidate" and updated["fields"][0]["drift"] == "changed"
