from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from bidprep.phase2.file_profile_schemas import ClientDocumentProfile


def _none_to_empty_str(v: object) -> str:
    return "" if v is None else v if isinstance(v, str) else str(v)


class RequirementPriority(str, Enum):
    required = "required"
    optional = "optional"
    exempt = "exempt"


class BaselineFormat(str, Enum):
    json = "json"
    xlsx = "xlsx"
    pdf = "pdf"
    md = "md"


class BaselineStandardDocument(BaseModel):
    requirements: list[dict] = Field(default_factory=list)


class VerificationStatus(str, Enum):
    matched = "matched"
    probable_match = "probable_match"
    substituted = "substituted"
    conditional_match = "conditional_match"
    missing = "missing"
    manual_review = "manual_review"


class Requirement(BaseModel):
    id: str = Field(description="Stable requirement ID, for example R001")
    ordinal: int = Field(ge=0, description="Checklist row number")
    category: str = ""
    name: str = ""
    tender_status: str = ""
    remark: str = ""
    priority: RequirementPriority = RequirementPriority.required

    @field_validator("category", "name", "tender_status", "remark", mode="before")
    @classmethod
    def _coerce_str(cls, v: object) -> str:
        return _none_to_empty_str(v)


class ClientFile(BaseModel):
    id: str = Field(description="Stable ID, usually sha256 prefix")
    original_filename: str = ""
    rel_path: str = ""
    abs_path: str = ""
    sha256: str = ""
    extension: str = ""
    mime_hint: str = ""
    page_count: int = 0
    extracted_text: str = ""
    ocr_used: bool = False
    coarse_category: str = ""
    is_scanned_pdf: bool = False
    file_profile: object | None = None
    document_profile: ClientDocumentProfile | None = None

    @field_validator(
        "original_filename",
        "rel_path",
        "abs_path",
        "sha256",
        "extension",
        "mime_hint",
        "extracted_text",
        "coarse_category",
        mode="before",
    )
    @classmethod
    def _coerce_str(cls, v: object) -> str:
        return _none_to_empty_str(v)


class FileUsageResult(BaseModel):
    file_id: str = ""
    original_name: str = ""
    doc_category: str | None = None
    matched_requirement_ids: list[str] = Field(default_factory=list)
    matched_requirement_names: list[str] = Field(default_factory=list)
    used: bool = False
    status: Literal["used", "unused", "manual_review"] = "unused"

    @field_validator("file_id", "original_name", mode="before")
    @classmethod
    def _coerce_str(cls, v: object) -> str:
        return _none_to_empty_str(v)

    @field_validator("doc_category", mode="before")
    @classmethod
    def _coerce_opt_str(cls, v: object) -> str | None:
        if v is None:
            return None
        text = str(v).strip()
        return text or None

    @field_validator("matched_requirement_ids", "matched_requirement_names", mode="before")
    @classmethod
    def _coerce_list(cls, v: object) -> list[str]:
        if v is None or not isinstance(v, list):
            return []
        return [str(x).strip() for x in v if str(x).strip()]


class MatchResult(BaseModel):
    requirement_id: str
    client_file_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    match_kind: Literal["direct", "probable"] = "direct"
    rationale: str = ""
    keyword_hits: list[str] = Field(default_factory=list)

    @field_validator("rationale", mode="before")
    @classmethod
    def _coerce_str(cls, v: object) -> str:
        return _none_to_empty_str(v)

    @field_validator("keyword_hits", mode="before")
    @classmethod
    def _coerce_hits(cls, v: object) -> list[str]:
        if v is None or not isinstance(v, list):
            return []
        return [str(x).strip() for x in v if str(x).strip()]


class ProfileMatchOutcome(BaseModel):
    matches: list[MatchResult] = Field(default_factory=list)
    match_source_by_requirement: dict[str, str] = Field(default_factory=dict)
    file_usage: list[FileUsageResult] = Field(default_factory=list)
    unused_file_ids: list[str] = Field(default_factory=list)


class RequirementVerification(BaseModel):
    requirement_id: str
    ordinal: int
    category: str
    name: str
    tender_status: str
    remark: str
    priority: RequirementPriority
    status: VerificationStatus
    matched_file_ids: list[str] = Field(default_factory=list)
    probable_file_ids: list[str] = Field(default_factory=list)
    substitution_applied: bool = False
    substitution_source_file_ids: list[str] = Field(default_factory=list)
    conditional: bool = False
    condition_note: str = ""
    notes: str = ""
    candidate_file_ids: list[str] = Field(default_factory=list)
    match_source: str | None = None
    key_information: list[str] = Field(default_factory=list)
    archive_file_names: list[str] = Field(default_factory=list)
    archive_file_paths: list[str] = Field(default_factory=list)
    png_paths: list[str] = Field(default_factory=list)
    llm_comment: str = ""
    llm_suggested_status: VerificationStatus | None = None
    llm_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    llm_reviewed: bool = False

    @field_validator(
        "category",
        "name",
        "tender_status",
        "remark",
        "condition_note",
        "notes",
        "llm_comment",
        mode="before",
    )
    @classmethod
    def _coerce_str(cls, v: object) -> str:
        return _none_to_empty_str(v)

    @field_validator(
        "matched_file_ids",
        "probable_file_ids",
        "substitution_source_file_ids",
        "candidate_file_ids",
        "key_information",
        "archive_file_names",
        "archive_file_paths",
        "png_paths",
        mode="before",
    )
    @classmethod
    def _coerce_list(cls, v: object) -> list[str]:
        if v is None or not isinstance(v, list):
            return []
        return [str(x).strip() for x in v if str(x).strip()]

    @field_validator("match_source", mode="before")
    @classmethod
    def _coerce_match_source(cls, v: object) -> str | None:
        if v is None:
            return None
        text = str(v).strip()
        return text or None

    @field_validator("llm_confidence", mode="before")
    @classmethod
    def _coerce_conf(cls, v: object) -> float | None:
        if v is None or v == "":
            return None
        try:
            value = float(v)
        except (TypeError, ValueError):
            return None
        return max(0.0, min(1.0, value))


class ArchiveIndexEntry(BaseModel):
    original_filename: str
    client_file_id: str
    sha256: str
    original_copy_path: str = ""
    requirement_ids: list[str] = Field(default_factory=list)
    requirement_names: list[str] = Field(default_factory=list)
    normalized_paths: list[str] = Field(default_factory=list)
    standardized_filenames: list[str] = Field(default_factory=list)
    preview_png_paths: list[str] = Field(default_factory=list)
    text_extract_path: str = ""
    match_confidence_max: float = 0.0


class Phase2Artifacts(BaseModel):
    originals_dir: str = "originals"
    normalized_dir: str = "normalized"
    previews_png_dir: str = "previews_png"
    reports_dir: str = "reports"
