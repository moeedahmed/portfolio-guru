from pydantic import BaseModel, model_validator
from curriculum import canonical_kc, canonical_kcs, validate_curriculum
from typing import Optional, List, Literal


class CurriculumDraft(BaseModel):
    # Doctor-owned exclusions are independent of the current default extra.
    excluded_key_capabilities: List[str] = []

    @model_validator(mode="before")
    @classmethod
    def validate_draft_curriculum(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        excluded = canonical_kcs(data.get("excluded_key_capabilities") or [])
        possible = data.get("possible_key_capability")
        capability = canonical_kc(possible.get("capability")) if isinstance(possible, dict) else None
        # Migrate legacy removed metadata when reconstructing existing drafts.
        if capability and possible.get("removed") is True and capability not in excluded:
            excluded.append(capability)
        data["excluded_key_capabilities"] = excluded
        if capability in excluded:
            data["possible_key_capability"] = {"capability": capability, "removed": True}
        target = dict(data["fields"]) if isinstance(data.get("fields"), dict) else data
        if excluded and "key_capabilities" in target:
            values = target.get("key_capabilities") or []
            if not isinstance(values, list):
                values = [values]
            # Filter before the three-KC cap, so excluded links cannot crowd out
            # valid supported links or a new default extra.
            target["key_capabilities"] = [kc for kc in values if canonical_kc(kc) not in excluded]
            target["curriculum_links"] = list(dict.fromkeys(
                kc.split()[0] for kc in canonical_kcs(target["key_capabilities"])))
        if isinstance(data.get("fields"), dict):
            data["fields"] = target
            data = validate_curriculum(data)
            data["fields"] = validate_curriculum(
                {**data["fields"], "possible_key_capability": data.get("possible_key_capability")},
                select_possible=True,
            )
            # Removal identity stays in draft metadata, never filing fields.
            data["fields"].pop("possible_key_capability", None)
        else:
            data = validate_curriculum(data, select_possible=True)
        return data

    def model_copy(self, *, update=None, deep=False):
        # Pydantic's default copy trusts updates, bypassing model validators.
        copied = super().model_copy(update=update, deep=deep)
        return type(self).model_validate(copied.model_dump())


class CBDData(CurriculumDraft):
    form_type: Literal["CBD"] = "CBD"
    date_of_encounter: str = ""              # YYYY-MM-DD
    patient_age: Optional[str] = None        # e.g. "45-year-old"
    patient_presentation: str = ""           # chief complaint
    clinical_setting: Optional[str] = None   # e.g. "Emergency Department - Resus"
    stage_of_training: Optional[str] = None  # "Intermediate/ST3" | "Higher/ST4-ST6" | "PEM" | "ACCS" | None if unknown
    trainee_role: str = ""                   # what the trainee did
    clinical_reasoning: str = ""             # maps to "Case to be discussed" field
    reflection: str = ""                     # maps to "Reflection of event" field
    level_of_supervision: Optional[str] = None  # "Direct" | "Indirect" | "Distant"
    supervisor_name: Optional[str] = None   # name or email
    curriculum_links: List[str] = []        # SLO labels e.g. ["SLO3", "SLO6"]
    key_capabilities: List[str] = []        # KC strings e.g. ["SLO1 KC1", "SLO6 KC2"]
    possible_key_capability: Optional[dict] = None  # auto-selected KC identity and optional removed flag


class FormDraft(CurriculumDraft):
    """Generic draft — holds any form's extracted field values as a flat dict."""
    form_type: str
    fields: dict        # key → extracted value, keyed by schema field key
    uuid: Optional[str] = None
    possible_key_capability: Optional[dict] = None  # never part of the filing fields


class DraftPreviewField(BaseModel):
    label: str
    value: str
    field_type: str     # "text", "date", "dropdown", "kc_tick", "multi_select"


class FormTypeRecommendation(BaseModel):
    form_type: str          # "CBD", "DOPS", "LAT", etc.
    rationale: str          # one-line reason why this form fits
    uuid: Optional[str]     # Kaizen form UUID (None if not yet verified)


class FileRequest(BaseModel):
    case_description: str
    telegram_user_id: Optional[int] = None  # if set, fetch creds from credential store
    dry_run: bool = False


class ActionStep(BaseModel):
    step: int
    action: str
    success: bool
    detail: Optional[str] = None


class FileResponse(BaseModel):
    status: str   # "success" | "partial" | "failed" | "dry_run"
    extracted_data: Optional[CBDData] = None
    action_log: List[ActionStep] = []
    screenshot_url: Optional[str] = None
    error: Optional[str] = None
    assessor_warning: Optional[str] = None  # set if assessor lookup failed


class KaizenFillRequest(BaseModel):
    form_type: str
    fields: dict
    draft_uuid: Optional[str] = None
    # Draft-only is the product invariant for non-bot entrypoints. Pydantic
    # rejects any payload that tries to flip this to False.
    save_as_draft: Literal[True] = True


class KaizenFillResponse(BaseModel):
    status: str  # "success" | "partial" | "failed"
    filled: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []
    screenshot_path: Optional[str] = None
