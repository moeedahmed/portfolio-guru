from pydantic import BaseModel
from typing import Optional, List, Literal


class CBDData(BaseModel):
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


class FormDraft(BaseModel):
    """Generic draft — holds any form's extracted field values as a flat dict."""
    form_type: str
    fields: dict        # key → extracted value, keyed by schema field key
    uuid: Optional[str] = None


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
    save_as_draft: bool = True


class KaizenFillResponse(BaseModel):
    status: str  # "success" | "partial" | "failed"
    filled: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []
    screenshot_path: Optional[str] = None
