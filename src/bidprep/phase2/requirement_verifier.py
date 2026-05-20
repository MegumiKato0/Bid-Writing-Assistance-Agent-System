from __future__ import annotations

import re
from typing import Callable

from bidprep.agents.llm import chat_json
from bidprep.llm_runtime import LLMRuntimeConfig
from bidprep.phase2.file_profile_schemas import ClientDocumentProfile, RequirementReviewBatch, RequirementReviewItem
from bidprep.phase2.schemas import Requirement

AGENT_B_SYSTEM = """你是投标材料阶段二的 Agent B。
你只能读取 ClientDocumentProfile，不读取原始二进制文件。
你的职责是基于 requirement 与文件画像，给出每条 requirement 的匹配文件候选，不负责最终归档、替代逻辑、条件逻辑和回填。"""

_MAX_CHARS = 36000
_BATCH_REQ = 24


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def _score_requirement_against_profile(requirement: Requirement, profile: ClientDocumentProfile) -> float:
    req_name = _norm(requirement.name)
    req_cat = _norm(requirement.category)
    haystack = _norm(
        "\n".join(
            [
                profile.original_name,
                profile.doc_category or "",
                " ".join(profile.detected_material_types),
                " ".join(profile.keywords[:24]),
                " ".join(profile.candidate_requirement_names[:12]),
                profile.effective_text[:10000],
            ]
        )
    )
    if req_name and req_name in haystack:
        return 0.96

    score = 0.0
    for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,12}", requirement.name):
        if _norm(token) in haystack:
            score += min(0.28, 0.05 * len(token))
    if req_cat and req_cat in haystack:
        score += 0.12
    for candidate_name in profile.candidate_requirement_names:
        if _norm(candidate_name) == req_name:
            score += 0.25
    for candidate_id in profile.candidate_requirement_ids:
        if candidate_id == requirement.id:
            score += 0.25
    return min(0.92, score)


def _heuristic_verify(
    requirements: list[Requirement],
    profiles_by_id: dict[str, ClientDocumentProfile],
    valid_file_ids: set[str],
) -> RequirementReviewBatch:
    items: list[RequirementReviewItem] = []
    used_file_ids: set[str] = set()

    for requirement in requirements:
        matched: list[str] = []
        probable: list[str] = []
        notes: list[str] = []
        top_score = 0.0
        for file_id in sorted(valid_file_ids):
            profile = profiles_by_id[file_id]
            score = _score_requirement_against_profile(requirement, profile)
            top_score = max(top_score, score)
            if score >= 0.72:
                matched.append(file_id)
                used_file_ids.add(file_id)
            elif score >= 0.35:
                probable.append(file_id)
        if matched:
            notes.append("基于 requirement 名称、文件画像和关键词命中")
        elif probable:
            notes.append("存在候选文件，但证据不足")
        items.append(
            RequirementReviewItem(
                requirement_id=requirement.id,
                matched_file_ids=matched,
                probable_file_ids=probable,
                notes="；".join(notes),
                confidence=top_score or 0.0,
            )
        )

    unused = sorted(valid_file_ids - used_file_ids)
    return RequirementReviewBatch(items=items, unused_file_ids=unused)


def _profile_block(profile: ClientDocumentProfile) -> str:
    excerpt = profile.text_excerpt or profile.effective_text
    if len(excerpt) > 8000:
        excerpt = excerpt[:4000] + "\n...(omitted)...\n" + excerpt[-3000:]
    return "\n".join(
        [
            f"### {profile.file_id}",
            f"文件名: {profile.original_name}",
            f"材料类别: {profile.doc_category or ''}",
            f"检测类型: {'、'.join(profile.detected_material_types)}",
            f"候选 requirement: {'、'.join(profile.candidate_requirement_names[:10])}",
            f"关键信息: {' | '.join(profile.key_information[:8])}",
            "文本摘录:",
            excerpt or "(empty)",
        ]
    )


def _build_user_message(requirements: list[Requirement], profiles_by_id: dict[str, ClientDocumentProfile], valid_file_ids: set[str]) -> str:
    req_block = "\n".join(
        [
            f"- {req.id} | 序号={req.ordinal} | 类别={req.category} | 名称={req.name} | 招标状态={req.tender_status} | 备注={req.remark}"
            for req in requirements
        ]
    )
    profile_block = "\n\n".join(_profile_block(profile) for profile in profiles_by_id.values())
    return "\n".join(
        [
            "## 有效文件 ID",
            ", ".join(sorted(valid_file_ids)),
            "",
            "## Requirement 列表",
            req_block,
            "",
            "## ClientDocumentProfile 列表",
            profile_block,
            "",
            '请只输出 JSON: {"items":[{"requirement_id":"R001","matched_file_ids":["..."],"probable_file_ids":["..."],"notes":"...","confidence":0.8}],"unused_file_ids":["..."],"global_notes":"..."}',
        ]
    )


def run_requirement_verifier(
    requirements: list[Requirement],
    profiles_by_id: dict[str, ClientDocumentProfile],
    valid_file_ids: set[str],
    llm: LLMRuntimeConfig | None,
    *,
    progress: Callable[[str], None] | None = None,
) -> RequirementReviewBatch:
    def log(message: str) -> None:
        if progress:
            progress(message)

    if not requirements or not profiles_by_id:
        return RequirementReviewBatch()

    if llm is None:
        return _heuristic_verify(requirements, profiles_by_id, valid_file_ids)

    req_batches: list[list[Requirement]] = []
    current: list[Requirement] = []
    current_len = 0
    for requirement in requirements:
        line = f"{requirement.id} {requirement.name} {requirement.category} {requirement.remark}"
        if current and (len(current) >= _BATCH_REQ or current_len + len(line) > _MAX_CHARS):
            req_batches.append(current)
            current = []
            current_len = 0
        current.append(requirement)
        current_len += len(line)
    if current:
        req_batches.append(current)

    merged_items: dict[str, RequirementReviewItem] = {}
    merged_unused: set[str] = set()
    notes: list[str] = []
    for index, batch in enumerate(req_batches, start=1):
        try:
            result = chat_json(
                AGENT_B_SYSTEM,
                _build_user_message(batch, profiles_by_id, valid_file_ids),
                RequirementReviewBatch,
                temperature=0.1,
                llm=llm,
            )
            for item in result.items:
                if item.requirement_id:
                    merged_items[item.requirement_id] = item
            merged_unused.update(file_id for file_id in result.unused_file_ids if file_id in valid_file_ids)
            if result.global_notes:
                notes.append(result.global_notes)
        except Exception as exc:
            log(f"Agent B batch {index}/{len(req_batches)} failed, fallback to heuristic: {exc}")
            fallback = _heuristic_verify(batch, profiles_by_id, valid_file_ids)
            for item in fallback.items:
                merged_items[item.requirement_id] = item
            merged_unused.update(fallback.unused_file_ids)

    final_items = [merged_items.get(req.id, RequirementReviewItem(requirement_id=req.id)) for req in requirements]
    return RequirementReviewBatch(
        items=final_items,
        unused_file_ids=sorted(merged_unused),
        global_notes="\n".join(notes),
    )
