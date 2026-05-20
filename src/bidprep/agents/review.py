from __future__ import annotations

import json

from bidprep.agents.llm import chat_json_raw
from bidprep.config import settings
from bidprep.llm_runtime import LLMRuntimeConfig
from bidprep.schemas import ReviewIssue, ReviewResult, TenderExtraction


REVIEW_SYSTEM = """你是招标文件抽取结果的审查员。你只能根据「原文摘录」判断抽取 JSON 是否有明显错误、幻觉或与原文矛盾。
输出 JSON：{"approved": bool, "issues": [{"severity":"low|medium|high","code":"","message":"","field_path":""}], "patched_extraction": null 或完整修正后的抽取对象}
若问题轻微可 approved=true；严重幻觉或关键日期/金额错误则 approved=false。
patched_extraction 若提供，必须与 TenderExtraction 结构一致。"""


def review_extraction(
    extraction: TenderExtraction,
    evidence_text: str,
    llm: LLMRuntimeConfig | None = None,
) -> ReviewResult:
    payload = {
        "extraction": extraction.model_dump(),
        "evidence_text": evidence_text[: settings.extract_chunk_chars + 2000],
    }
    user = json.dumps(payload, ensure_ascii=False)
    data = chat_json_raw(REVIEW_SYSTEM, user, temperature=0.1, llm=llm)
    issues = data.get("issues") or []
    patched = data.get("patched_extraction")
    approved = bool(data.get("approved", True))
    pr = None
    if patched and isinstance(patched, dict):
        try:
            pr = TenderExtraction.model_validate(patched)
        except Exception:
            pr = None
    parsed_issues: list[ReviewIssue] = []
    for it in issues:
        if not isinstance(it, dict):
            continue
        sev = it.get("severity") or "medium"
        if sev not in ("low", "medium", "high"):
            sev = "medium"
        parsed_issues.append(
            ReviewIssue(
                severity=sev,
                code=str(it.get("code") or ""),
                message=str(it.get("message") or ""),
                field_path=it.get("field_path"),
            )
        )
    return ReviewResult(approved=approved, issues=parsed_issues, patched_extraction=pr)
