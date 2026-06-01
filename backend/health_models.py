from datetime import date, datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class HealthDomain(str, Enum):
    clinical = "clinical"
    cpd = "cpd"
    qi = "qi"
    teaching = "teaching"
    leadership = "leadership"
    reflection = "reflection"


class Pathway(str, Enum):
    training_arcp = "training_arcp"
    cesr_portfolio = "cesr_portfolio"


class HealthScore(str, Enum):
    green = "green"
    amber = "amber"
    red = "red"
    grey = "grey"


EvidenceType = Literal[
    "wpba",
    "course",
    "audit",
    "teaching_session",
    "project",
    "reflection_log",
    "other",
]
EvidenceSource = Literal["kaizen_filed", "pg_draft", "manual_entry", "file_upload"]
EvidenceStatus = Literal["drafted", "filed", "reviewed", "accepted", "needs_work"]


class EvidenceItem(BaseModel):
    id: str
    user_id: str
    domain: HealthDomain
    evidence_type: EvidenceType
    form_type: Optional[str] = None
    title: str
    summary: str
    event_date: date
    source: EvidenceSource
    source_ref: Optional[str] = None
    status: EvidenceStatus
    created_at: datetime
    updated_at: datetime


class HealthProfile(BaseModel):
    user_id: str
    pathway: Pathway
    pathway_config: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class HealthSnapshot(BaseModel):
    user_id: str
    computed_at: datetime
    pathway: Pathway
    health_score: HealthScore
    domain_counts: dict[HealthDomain, int]
    pathway_readiness: dict[str, Any]
    gap_summary: list[str]
    next_actions: list[str]
