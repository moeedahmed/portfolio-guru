"""Versioned local data models; no publishing or browser actions exist here."""
import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum


def json_text(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


class Action(str, Enum):
    FILL = "fill"
    SELECT = "select"
    CHOOSE = "choose"
    CHECK = "check"
    SET_FILE = "set_file"


@dataclass(frozen=True)
class Option:
    value: str
    label: str
    disabled: bool = False
    selectors: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str
    options: tuple = ()
    required: bool = False
    selectors: dict = field(default_factory=dict)
    section: str = ""
    restrictions: tuple = ()
    state: str = "unmapped"
    concept: str | None = None
    drift: str = ""

    def __post_init__(self):
        if self.kind not in {"text", "textarea", "select", "radio", "checkbox", "date", "file"}:
            raise ValueError("Unsupported field kind")
        if self.state not in {"unmapped", "candidate", "verified"} or self.drift not in {"", "changed", "missing"}:
            raise ValueError("Invalid mapping state/drift")
        if (self.state == "unmapped") != (self.concept is None) or self.concept == "":
            raise ValueError("Mapping state requires a meaning")

    def structure(self):
        data = asdict(self)
        for key in ("state", "concept", "drift"):
            data.pop(key)
        return data


def structure_hash(fields):
    active = [f.structure() for f in fields if f.drift != "missing"]
    return hashlib.sha256(json_text(active).encode()).hexdigest()


@dataclass(frozen=True)
class FormMap:
    platform: str
    form_id: str
    fields: tuple
    schema_version: int = 1

    def __post_init__(self):
        if self.schema_version != 1:
            raise ValueError("Unsupported map schema version")
        keys = [f.key for f in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate field keys; ambiguous form")

    @property
    def source_hash(self):
        return structure_hash(self.fields)

    def to_json(self):
        return json_text({**asdict(self), "source_hash": self.source_hash})


@dataclass(frozen=True)
class FillStep:
    field_key: str
    selector: dict
    action: Action
    value: str | bool

    def __post_init__(self):
        object.__setattr__(self, "action", Action(self.action))


@dataclass(frozen=True)
class Skipped:
    field_key: str
    concept: str | None
    reason: str


@dataclass(frozen=True)
class FillPlan:
    platform: str
    form_id: str
    source_hash: str
    steps: tuple
    skipped: tuple

    def to_json(self):
        return json_text(asdict(self))
