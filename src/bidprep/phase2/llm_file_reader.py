from __future__ import annotations

import re
from typing import Callable

from bidprep.agents.llm import chat_json
from bidprep.llm_runtime import LLMRuntimeConfig
from bidprep.phase2.file_profile_schemas import (
    ClientDocumentProfile,
    ClientFileProfile,
    LlmFileReadingBatch,
    LlmFileReadingResult,
)
from bidprep.phase2.schemas import ClientFile

LLM_FILE_READER_SYSTEM = """你是投标材料阶段二的 Agent A。
你的职责只有逐文件阅读与理解，不负责最终 requirement 状态裁定。

请基于给定的文件画像，为每个文件输出：
1. 建议的材料类别
2. 可能对应的 requirement ID 和名称
3. 关键证据
4. 提取到的关键信息
5. 简短摘要与置信度

不要输出最终“已满足/未满足/缺失”的结论。"""

AGENT_A_SYSTEM = """你是投标材料文件阅读助手。请只根据文件名和文本摘录给出单文件摘要，不要编造内容。"""

_BATCH_MAX_FILES = 4
_MAX_BATCH_CHARS = 28000

_CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("身份证", ("身份证", "居民身份证")),
    ("营业执照", ("营业执照",)),
    ("执业许可证", ("执业许可证", "许可证")),
    ("证书类", ("证书", "资格证")),
    ("授权书", ("授权书", "委托书", "授权委托")),
    ("声明函", ("声明函",)),
    ("承诺函", ("承诺函", "资格承诺", "信用承诺")),
    ("盖章页", ("盖章", "公章")),
    ("签字页", ("签字", "签署")),
]


def _score_requirement_name(profile: ClientDocumentProfile, requirement_line: str) -> float:
    line = requirement_line.strip()
    if not line:
        return 0.0
    parts = line.split(maxsplit=1)
    name = parts[1] if len(parts) == 2 else parts[0]
    text = f"{profile.original_name}\n{profile.effective_text[:6000]}\n{' '.join(profile.keywords[:20])}"
    compact_text = re.sub(r"\s+", "", text.lower())
    compact_name = re.sub(r"\s+", "", name.lower())
    if compact_name and compact_name in compact_text:
        return 0.95

    score = 0.0
    for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,12}", name):
        if token.lower() in compact_text:
            score += min(0.25, 0.06 * len(token))
    return min(0.88, score)


def _heuristic_reading(profile: ClientDocumentProfile, requirements_brief: str = "") -> LlmFileReadingResult:
    haystack = f"{profile.original_name}\n{profile.effective_text[:12000]}"
    material_types: list[str] = list(profile.detected_material_types)
    category = profile.doc_category or "unknown"

    for label, patterns in _CATEGORY_KEYWORDS:
        if any(pattern in haystack for pattern in patterns):
            if label not in material_types:
                material_types.append(label)
            if category in ("", "unknown", None):
                category = label

    suggested_ids: list[str] = []
    suggested_names: list[str] = []
    for line in requirements_brief.splitlines():
        score = _score_requirement_name(profile, line)
        if score < 0.45:
            continue
        parts = line.split(maxsplit=1)
        req_id = parts[0].strip()
        req_name = parts[1].strip() if len(parts) == 2 else req_id
        suggested_ids.append(req_id)
        suggested_names.append(req_name)
        if len(suggested_ids) >= 5:
            break

    evidence: list[str] = []
    for token in material_types + profile.key_information + profile.keywords:
        if token and token not in evidence:
            evidence.append(token)
        if len(evidence) >= 8:
            break

    summary_parts: list[str] = []
    if category and category != "unknown":
        summary_parts.append(f"识别为{category}")
    if suggested_names:
        summary_parts.append("可能对应: " + "、".join(suggested_names[:3]))
    if profile.company_name:
        summary_parts.append(f"公司名称: {profile.company_name}")

    return LlmFileReadingResult(
        file_id=profile.file_id,
        suggested_doc_category=category,
        suggested_material_types=material_types,
        suggested_requirement_ids=suggested_ids,
        suggested_requirement_names=suggested_names,
        key_evidence=evidence[:8],
        extracted_key_information=profile.key_information[:8],
        summary="；".join(summary_parts) if summary_parts else "未识别出明确材料类型",
        confidence=0.55 if material_types or suggested_ids else 0.2,
        reviewed=True,
    )


def build_llm_file_reader_messages(
    batch: list[ClientDocumentProfile],
    *,
    requirements_brief: str = "",
) -> tuple[str, str]:
    blocks: list[str] = []
    if requirements_brief.strip():
        blocks.extend(["## Requirements", requirements_brief.strip(), ""])
    for profile in batch:
        blocks.extend(
            [
                f"### file_id: {profile.file_id}",
                f"文件名: {profile.original_name}",
                f"扩展名: {profile.ext}",
                f"分类候选: {profile.doc_category or ''}",
                f"关键词: {'、'.join(profile.keywords[:24])}",
                f"关键信息: {' | '.join(profile.key_information[:8])}",
                "文本摘录:",
                (profile.text_excerpt or profile.effective_text or "")[:14000] or "(empty)",
                "",
            ]
        )
    blocks.extend(
        [
            "请只输出 JSON，格式如下：",
            '{"rows":[{"file_id":"F001","suggested_doc_category":"营业执照","suggested_material_types":["营业执照"],"suggested_requirement_ids":["R001"],"suggested_requirement_names":["营业执照"],"key_evidence":["营业执照"],"extracted_key_information":["公司名称: xx"],"summary":"...","confidence":0.9,"reviewed":true}]}',
        ]
    )
    return LLM_FILE_READER_SYSTEM, "\n".join(blocks)


def run_llm_file_reader(
    profiles: list[ClientDocumentProfile],
    llm: LLMRuntimeConfig | None,
    *,
    requirements_brief: str = "",
    rule_confidence_below: float | None = None,
    effective_text_shorter_than: int | None = None,
    batch_max_files: int | None = None,
    max_batch_chars: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, LlmFileReadingResult]:
    def log(message: str) -> None:
        if progress:
            progress(message)

    selected = list(profiles)
    if rule_confidence_below is not None:
        selected = [p for p in selected if p.rule_confidence is None or p.rule_confidence < rule_confidence_below]
    if effective_text_shorter_than is not None:
        selected = [p for p in selected if len((p.effective_text or "").strip()) < effective_text_shorter_than]

    if not selected:
        return {}

    if llm is None:
        return {profile.file_id: _heuristic_reading(profile, requirements_brief) for profile in selected}

    batch_limit = max(1, min(32, int(batch_max_files or _BATCH_MAX_FILES)))
    char_limit = max(4000, min(200000, int(max_batch_chars or _MAX_BATCH_CHARS)))
    batches: list[list[ClientDocumentProfile]] = []
    current: list[ClientDocumentProfile] = []
    current_chars = 0
    for profile in selected:
        _, user = build_llm_file_reader_messages([profile], requirements_brief=requirements_brief)
        user_len = len(user)
        if current and (len(current) >= batch_limit or current_chars + user_len > char_limit):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(profile)
        current_chars += user_len
    if current:
        batches.append(current)

    out: dict[str, LlmFileReadingResult] = {}
    for index, batch in enumerate(batches, start=1):
        system, user = build_llm_file_reader_messages(batch, requirements_brief=requirements_brief)
        try:
            raw = chat_json(system, user, LlmFileReadingBatch, temperature=0.1, llm=llm)
            by_id = {row.file_id: row for row in raw.rows if row.file_id}
            for profile in batch:
                out[profile.file_id] = by_id.get(profile.file_id) or _heuristic_reading(profile, requirements_brief)
        except Exception as exc:
            log(f"Agent A batch {index}/{len(batches)} failed, fallback to heuristic: {exc}")
            for profile in batch:
                out[profile.file_id] = _heuristic_reading(profile, requirements_brief)
    return out


def merge_llm_file_reading_into_profiles(
    profiles: list[ClientDocumentProfile],
    reading_by_id: dict[str, LlmFileReadingResult],
) -> list[ClientDocumentProfile]:
    merged: list[ClientDocumentProfile] = []
    for profile in profiles:
        reading = reading_by_id.get(profile.file_id)
        if reading is None:
            merged.append(profile)
            continue

        category = reading.suggested_doc_category or profile.doc_category
        material_types = list(profile.detected_material_types)
        for item in reading.suggested_material_types:
            if item not in material_types:
                material_types.append(item)

        candidate_ids = list(profile.candidate_requirement_ids)
        for item in reading.suggested_requirement_ids:
            if item not in candidate_ids:
                candidate_ids.append(item)

        candidate_names = list(profile.candidate_requirement_names)
        for item in reading.suggested_requirement_names:
            if item not in candidate_names:
                candidate_names.append(item)

        key_information = list(profile.key_information)
        for item in reading.extracted_key_information:
            if item not in key_information:
                key_information.append(item)

        should_render_png = profile.should_render_png or any(
            token in {"身份证", "营业执照", "执业许可证", "证书类", "授权书", "声明函", "承诺函", "盖章页", "签字页"}
            for token in material_types
        )

        merged.append(
            profile.model_copy(
                update={
                    "doc_category": category,
                    "detected_material_types": material_types,
                    "possible_requirements": candidate_names,
                    "candidate_requirement_ids": candidate_ids,
                    "candidate_requirement_names": candidate_names,
                    "key_information": key_information,
                    "llm_reviewed": bool(reading.reviewed),
                    "llm_summary": reading.summary,
                    "llm_evidence": reading.key_evidence,
                    "llm_comment": "\n".join(
                        part for part in [
                            reading.summary or "",
                            "证据: " + "、".join(reading.key_evidence[:8]) if reading.key_evidence else "",
                        ] if part
                    )
                    or None,
                    "rule_confidence": max(profile.rule_confidence or 0.0, reading.confidence or 0.0),
                    "should_render_png": should_render_png,
                }
            )
        )
    return merged


def run_agent_a_file_profiles(
    clients: list[ClientFile],
    llm: LLMRuntimeConfig | None,
    *,
    progress: Callable[[str], None] | None = None,
) -> dict[str, ClientFileProfile]:
    profiles: dict[str, ClientFileProfile] = {}
    for client in clients:
        text = (client.extracted_text or "").strip()
        summary = text[:300] if text else client.original_filename
        doc_types: list[str] = []
        for label, patterns in _CATEGORY_KEYWORDS:
            if any(pattern in f"{client.original_filename}\n{text[:4000]}" for pattern in patterns):
                doc_types.append(label)
        profiles[client.id] = ClientFileProfile(
            client_file_id=client.id,
            original_filename=client.original_filename,
            summary=summary,
            document_types=doc_types,
            key_entities=[],
            language_hint="zh",
            confidence=0.5 if summary else 0.0,
            model_notes="heuristic profile" if llm is None else "",
        )
    if progress:
        progress(f"Agent A legacy profiles built for {len(profiles)} files")
    return profiles
