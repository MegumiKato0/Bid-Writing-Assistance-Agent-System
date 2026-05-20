from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _none_to_empty_str(v: object) -> str:
    return "" if v is None else v if isinstance(v, str) else str(v)


class Phase2AssistRow(BaseModel):
    """模型返回的单条辅助裁定（解析用）。"""

    model_config = ConfigDict(extra="ignore")

    requirement_id: str = ""
    assessment: Literal["satisfied", "not_satisfied", "unclear"] = "unclear"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = ""
    suggested_matched_file_ids: list[str] = Field(default_factory=list)

    @field_validator("requirement_id", "rationale", mode="before")
    @classmethod
    def _coerce_str(cls, v: object) -> str:
        return _none_to_empty_str(v)

    @field_validator("suggested_matched_file_ids", mode="before")
    @classmethod
    def _coerce_ids(cls, v: object) -> list[str]:
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        out: list[str] = []
        for x in v:
            if x is None:
                continue
            s = x if isinstance(x, str) else str(x)
            s = s.strip()
            if s:
                out.append(s)
        return out

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_conf(cls, v: object) -> float:
        if v is None:
            return 0.0
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, f))


class Phase2AssistBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[Phase2AssistRow] = Field(default_factory=list)

    @field_validator("items", mode="before")
    @classmethod
    def _coerce_items(cls, v: object) -> list:
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        return v
