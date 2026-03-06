from pydantic import BaseModel
from typing import Optional, List, Literal


class CBDData(BaseModel):
    form_type: Literal["CBD"] = "CBD"
    date_of_encounter: str           # YYYY-MM-DD
    patient_age: str                 # e.g. "45-year-old"
    patient_presentation: str        # chief complaint
    clinical_setting: str            # e.g. "Emergency Department - Resus"
    stage_of_training: str           # "Intermediate/ST3" | "Higher/ST4-ST6" | "PEM" | "ACCS"
    trainee_role: str                # what the trainee did
    clinical_reasoning: str          # maps to "Case to be discussed" field
    reflection: str                  # maps to "Reflection of event" field
    level_of_supervision: str        # "Direct" | "Indirect" | "Distant"
    supervisor_name: Optional[str] = None   # name or email
    curriculum_links: List[str] = []        # SLO labels e.g. ["SLO3", "SLO6"]


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
