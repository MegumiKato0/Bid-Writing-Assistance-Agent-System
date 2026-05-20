from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _none_to_empty_str(v: object) -> str:
    return "" if v is None else v if isinstance(v, str) else str(v)


class ProjectBasicInfo(BaseModel):
    project_name: Optional[str] = None
    project_number: Optional[str] = None
    purchaser: Optional[str] = None
    agency: Optional[str] = None
    max_price: Optional[str] = None
    agency_fee: Optional[str] = None
    bid_bond: Optional[str] = None
    bid_deadline: Optional[str] = None
    submit_location: Optional[str] = None
    negotiation_time: Optional[str] = None


class ContactBlock(BaseModel):
    unit: Optional[str] = None
    address: Optional[str] = None
    contact: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None


class MaterialRow(BaseModel):
    category: str = ""
    name: str = ""
    status: str = "☐ 需准备"
    remark: str = ""
    source_page: Optional[int] = None

    @field_validator("category", "name", "remark", mode="before")
    @classmethod
    def _coerce_str(cls, v: object) -> str:
        return _none_to_empty_str(v)

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, v: object) -> str:
        if v is None:
            return "☐ 需准备"
        s = v if isinstance(v, str) else str(v)
        return s if s.strip() else "☐ 需准备"


class FillItem(BaseModel):
    field_name: str = ""
    section: str = ""
    description: str = ""
    required: bool = True
    quote: str = ""
    page: Optional[int] = None

    @field_validator("field_name", "section", "description", "quote", mode="before")
    @classmethod
    def _coerce_str(cls, v: object) -> str:
        return _none_to_empty_str(v)

    @field_validator("required", mode="before")
    @classmethod
    def _coerce_required(cls, v: object) -> bool:
        if v is None:
            return True
        return bool(v)


class ServiceSummary(BaseModel):
    scope_bullets: list[str] = Field(default_factory=list)
    key_dates: list[str] = Field(default_factory=list)
    focus_areas: list[str] = Field(default_factory=list)

    @field_validator("scope_bullets", "key_dates", "focus_areas", mode="before")
    @classmethod
    def _clean_str_list(cls, v: object) -> list[str]:
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        out: list[str] = []
        for x in v:
            if x is None:
                continue
            out.append(x if isinstance(x, str) else str(x))
        return out


class PartialExtraction(BaseModel):
    """单块抽取结果，便于合并。"""

    model_config = ConfigDict(extra="ignore")

    project_basic: ProjectBasicInfo = Field(default_factory=ProjectBasicInfo)
    purchaser_contact: ContactBlock = Field(default_factory=ContactBlock)
    agency_contact: ContactBlock = Field(default_factory=ContactBlock)
    materials: list[MaterialRow] = Field(default_factory=list)
    fill_items: list[FillItem] = Field(default_factory=list)
    cautions: list[str] = Field(default_factory=list)
    response_file_parts: list[str] = Field(default_factory=list)
    service_summary: ServiceSummary = Field(default_factory=ServiceSummary)
    raw_notes: list[str] = Field(default_factory=list)

    @field_validator(
        "project_basic",
        "purchaser_contact",
        "agency_contact",
        "service_summary",
        mode="before",
    )
    @classmethod
    def _nested_none_to_empty_dict(cls, v: object) -> object:
        return {} if v is None else v

    @field_validator("materials", "fill_items", mode="before")
    @classmethod
    def _materials_fill_none_to_empty_list(cls, v: object) -> object:
        return [] if v is None else v

    @field_validator("cautions", "response_file_parts", "raw_notes", mode="before")
    @classmethod
    def _clean_str_lists(cls, v: object) -> list[str]:
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        out: list[str] = []
        for x in v:
            if x is None:
                continue
            out.append(x if isinstance(x, str) else str(x))
        return out


class TenderExtraction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    project_basic: ProjectBasicInfo = Field(default_factory=ProjectBasicInfo)
    purchaser_contact: ContactBlock = Field(default_factory=ContactBlock)
    agency_contact: ContactBlock = Field(default_factory=ContactBlock)
    materials: list[MaterialRow] = Field(default_factory=list)
    fill_items: list[FillItem] = Field(default_factory=list)
    cautions: list[str] = Field(default_factory=list)
    response_file_parts: list[str] = Field(default_factory=list)
    service_summary: ServiceSummary = Field(default_factory=ServiceSummary)
    collection_checklist: list[str] = Field(default_factory=list)

    @field_validator(
        "project_basic",
        "purchaser_contact",
        "agency_contact",
        "service_summary",
        mode="before",
    )
    @classmethod
    def _nested_none_to_empty_dict(cls, v: object) -> object:
        return {} if v is None else v

    @field_validator("materials", "fill_items", mode="before")
    @classmethod
    def _materials_fill_none_to_empty_list(cls, v: object) -> object:
        return [] if v is None else v

    @field_validator("cautions", "response_file_parts", "collection_checklist", mode="before")
    @classmethod
    def _clean_str_lists(cls, v: object) -> list[str]:
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        out: list[str] = []
        for x in v:
            if x is None:
                continue
            out.append(x if isinstance(x, str) else str(x))
        return out


class ReviewIssue(BaseModel):
    severity: Literal["low", "medium", "high"] = "medium"
    code: str = ""
    message: str = ""
    field_path: Optional[str] = None

    @field_validator("code", "message", mode="before")
    @classmethod
    def _coerce_str(cls, v: object) -> str:
        return _none_to_empty_str(v)


class ReviewResult(BaseModel):
    approved: bool = True
    issues: list[ReviewIssue] = Field(default_factory=list)
    patched_extraction: Optional[TenderExtraction] = None


class FieldProvenance(BaseModel):
    field: str
    source: Literal["rule", "agent", "review", "default"] = "agent"
    detail: str = ""

    @field_validator("detail", mode="before")
    @classmethod
    def _coerce_detail(cls, v: object) -> str:
        return _none_to_empty_str(v)


class MergeMeta(BaseModel):
    provenance: list[FieldProvenance] = Field(default_factory=list)
    review: Optional[ReviewResult] = None
    rules_snapshot: dict[str, Any] = Field(default_factory=dict)
