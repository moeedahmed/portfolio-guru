from pydantic import BaseModel
from typing import Optional, List


class CBDData(BaseModel):
    patient_age: str
    patient_presentation: str  # presenting complaint / chief complaint
    clinical_setting: str  # e.g. "Emergency Department - Resus"
    trainee_role: str  # e.g. "Primary clinician with indirect supervision"
    clinical_reasoning: str  # what the trainee thought/did/why
    learning_points: str  # what was learned from this case
    level_of_supervision: str  # "Direct" | "Indirect" | "Distant"
    supervisor_name: Optional[str] = None
    date_of_encounter: str  # ISO date string YYYY-MM-DD


class FileRequest(BaseModel):
    case_description: str
    dry_run: bool = False  # if True: extract only, no browser


class ActionStep(BaseModel):
    step: int
    action: str
    success: bool
    detail: Optional[str] = None


class FileResponse(BaseModel):
    status: str  # "success" | "partial" | "failed" | "dry_run"
    extracted_data: Optional[CBDData] = None
    action_log: List[ActionStep] = []
    screenshot_url: Optional[str] = None
    error: Optional[str] = None
