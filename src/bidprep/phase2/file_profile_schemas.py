from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _none_to_empty_str(v: object) -> str:
    return "" if v is None else v if isinstance(v, str) else str(v)


def _clean_str_list(v: object) -> list[str]:
    if v is None or not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if str(x).strip()]


class ClientFileProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    client_file_id: str = ""
    original_filename: str = ""
    summary: str = ""
    document_types: list[str] = Field(default_factory=list)
    key_entities: list[str] = Field(default_factory=list)
    language_hint: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    model_notes: str = ""

    @field_validator(
        "client_file_id",
        "original_filename",
        "summary",
        "language_hint",
        "model_notes",
        mode="before",
    )
    @classmethod
    def _coerce_str(cls, v: object) -> str:
        return _none_to_empty_str(v)

    @field_validator("document_types", "key_entities", mode="before")
    @classmethod
    def _coerce_list(cls, v: object) -> list[str]:
        return _clean_str_list(v)

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v: object) -> float:
        if v is None or v == "":
            return 0.0
        try:
            value = float(v)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, value))


class ClientDocumentProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file_id: str = ""
    original_name: str = ""
    rel_path: str = ""
    file_path: str = ""
    ext: str = ""
    mime_type: str | None = None
    pages: int | None = None
    file_size: int | None = None
    hash: str | None = None
    parsed_text: str = ""
    ocr_text: str = ""
    effective_text: str = ""
    text_excerpt: str = ""
    coarse_category: str | None = None
    doc_category: str | None = None
    detected_material_types: list[str] = Field(default_factory=list)
    possible_requirements: list[str] = Field(default_factory=list)
    candidate_requirement_ids: list[str] = Field(default_factory=list)
    candidate_requirement_names: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    company_name: str | None = None
    person_names: list[str] = Field(default_factory=list)
    document_date: str | None = None
    key_information: list[str] = Field(default_factory=list)
    llm_reviewed: bool = False
    llm_summary: str | None = None
    llm_evidence: list[str] = Field(default_factory=list)
    llm_comment: str | None = None
    rule_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source_abbr: str | None = None
    preferred_archive_category: str | None = None
    should_render_png: bool = False
    preferred_png_pages: list[int] = Field(default_factory=list)

    @field_validator(
        "file_id",
        "original_name",
        "rel_path",
        "file_path",
        "ext",
        "parsed_text",
        "ocr_text",
        "effective_text",
        "text_excerpt",
        mode="before",
    )
    @classmethod
    def _coerce_str(cls, v: object) -> str:
        return _none_to_empty_str(v)

    @field_validator(
        "mime_type",
        "coarse_category",
        "doc_category",
        "company_name",
        "document_date",
        "llm_summary",
        "llm_comment",
        "hash",
        "source_abbr",
        "preferred_archive_category",
        mode="before",
    )
    @classmethod
    def _coerce_opt_str(cls, v: object) -> str | None:
        if v is None:
            return None
        text = str(v).strip()
        return text or None

    @field_validator(
        "detected_material_types",
        "possible_requirements",
        "candidate_requirement_ids",
        "candidate_requirement_names",
        "keywords",
        "person_names",
        "key_information",
        "llm_evidence",
        mode="before",
    )
    @classmethod
    def _coerce_list(cls, v: object) -> list[str]:
        return _clean_str_list(v)

    @field_validator("preferred_png_pages", mode="before")
    @classmethod
    def _coerce_int_list(cls, v: object) -> list[int]:
        if v is None or not isinstance(v, list):
            return []
        out: list[int] = []
        for item in v:
            try:
                value = int(item)
            except (TypeError, ValueError):
                continue
            if value > 0:
                out.append(value)
        return out

    @field_validator("pages", "file_size", mode="before")
    @classmethod
    def _coerce_opt_int(cls, v: object) -> int | None:
        if v is None or v == "":
            return None
        try:
            value = int(v)
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    @field_validator("rule_confidence", mode="before")
    @classmethod
    def _coerce_opt_conf(cls, v: object) -> float | None:
        if v is None or v == "":
            return None
        try:
            value = float(v)
        except (TypeError, ValueError):
            return None
        return max(0.0, min(1.0, value))


class FileProfileBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    files: list[ClientDocumentProfile] = Field(default_factory=list)

    @field_validator("files", mode="before")
    @classmethod
    def _coerce_files(cls, v: object) -> list:
        if v is None or not isinstance(v, list):
            return []
        return v


class LlmFileReadingResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    file_id: str = ""
    suggested_doc_category: str | None = None
    suggested_material_types: list[str] = Field(default_factory=list)
    suggested_requirement_ids: list[str] = Field(default_factory=list)
    suggested_requirement_names: list[str] = Field(default_factory=list)
    key_evidence: list[str] = Field(default_factory=list)
    extracted_key_information: list[str] = Field(default_factory=list)
    summary: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reviewed: bool = False

    @field_validator("file_id", mode="before")
    @classmethod
    def _coerce_file_id(cls, v: object) -> str:
        return _none_to_empty_str(v)

    @field_validator("suggested_doc_category", "summary", mode="before")
    @classmethod
    def _coerce_opt_str(cls, v: object) -> str | None:
        if v is None:
            return None
        text = str(v).strip()
        return text or None

    @field_validator(
        "suggested_material_types",
        "suggested_requirement_ids",
        "suggested_requirement_names",
        "key_evidence",
        "extracted_key_information",
        mode="before",
    )
    @classmethod
    def _coerce_list(cls, v: object) -> list[str]:
        return _clean_str_list(v)

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v: object) -> float | None:
        if v is None or v == "":
            return None
        try:
            value = float(v)
        except (TypeError, ValueError):
            return None
        return max(0.0, min(1.0, value))


class LlmFileReadingBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rows: list[LlmFileReadingResult] = Field(default_factory=list)

    @field_validator("rows", mode="before")
    @classmethod
    def _coerce_rows(cls, v: object) -> list:
        if v is None or not isinstance(v, list):
            return []
        return v


class RequirementReviewItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    requirement_id: str = ""
    matched_file_ids: list[str] = Field(default_factory=list)
    probable_file_ids: list[str] = Field(default_factory=list)
    notes: str = ""
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("requirement_id", "notes", mode="before")
    @classmethod
    def _coerce_str(cls, v: object) -> str:
        return _none_to_empty_str(v)

    @field_validator("matched_file_ids", "probable_file_ids", mode="before")
    @classmethod
    def _coerce_list(cls, v: object) -> list[str]:
        return _clean_str_list(v)

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v: object) -> float | None:
        if v is None or v == "":
            return None
        try:
            value = float(v)
        except (TypeError, ValueError):
            return None
        return max(0.0, min(1.0, value))


class RequirementReviewBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[RequirementReviewItem] = Field(default_factory=list)
    unused_file_ids: list[str] = Field(default_factory=list)
    global_notes: str = ""

    @field_validator("items", mode="before")
    @classmethod
    def _coerce_items(cls, v: object) -> list:
        if v is None or not isinstance(v, list):
            return []
        return v

    @field_validator("unused_file_ids", mode="before")
    @classmethod
    def _coerce_unused(cls, v: object) -> list[str]:
        return _clean_str_list(v)

    @field_validator("global_notes", mode="before")
    @classmethod
    def _coerce_global_notes(cls, v: object) -> str:
        return _none_to_empty_str(v)


class Phase2CoverageSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    all_client_file_ids: list[str] = Field(default_factory=list)
    used_file_ids: list[str] = Field(default_factory=list)
    unused_file_ids: list[str] = Field(default_factory=list)
    requirement_file_map: dict[str, list[str]] = Field(default_factory=dict)
    agent_b_unused_file_ids: list[str] = Field(default_factory=list)
    program_unused_file_ids: list[str] = Field(default_factory=list)
    unmatched_requirement_ids: list[str] = Field(default_factory=list)
