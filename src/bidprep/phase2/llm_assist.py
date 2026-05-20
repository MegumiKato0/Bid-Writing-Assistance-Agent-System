from __future__ import annotations

from typing import Callable

from bidprep.agents.llm import chat_json
from bidprep.llm_runtime import LLMRuntimeConfig
from bidprep.phase2.llm_assist_schemas import Phase2AssistBatch, Phase2AssistRow
from bidprep.phase2.schemas import ClientFile, RequirementVerification, VerificationStatus

PHASE2_ASSIST_SYSTEM = """你是政府采购/投标材料核验助手（可选第三层灰区辅助）。
输入为「招标材料项说明」+ 每条关联客户文件的**程序侧文件画像摘录**（含 text_excerpt / 关键词等）；**不要**仅依赖一行式的核验状态结论做判断。
若某文件提供了 document_profile，请优先依据画像摘录与关键词，不要编造未出现在摘录中的内容。
对每一条材料项，从下列结论中选一项：
- satisfied：客户材料能够合理满足该条招标材料要求；
- not_satisfied：明显不满足或材料与该项无关；
- unclear：信息不足，需人工复核。

同时给出 confidence（0 到 1 的小数）。若判定为 satisfied，可在 suggested_matched_file_ids 中列出你认为满足该项的客户文件 ID（必须从题目给出的 ID 中选择，不要编造 ID）。"""

_BATCH_MAX_ITEMS = 8
_EXCERPT_HEAD = 2200
_EXCERPT_TAIL = 800
_MAX_BATCH_CHARS = 28000


def _text_excerpt(text: str, head: int = _EXCERPT_HEAD, tail: int = _EXCERPT_TAIL) -> str:
    t = (text or "").strip()
    if len(t) <= head + tail + 40:
        return t
    return f"{t[:head]}\n…(中间省略)…\n{t[-tail:]}"


def _verification_needs_assist(v: RequirementVerification) -> bool:
    if v.status in (VerificationStatus.matched, VerificationStatus.substituted):
        return False
    if v.priority.value == "exempt":
        return False
    if v.status == VerificationStatus.probable_match:
        return bool(v.probable_file_ids)
    if v.status == VerificationStatus.manual_review:
        return bool(v.matched_file_ids or v.probable_file_ids)
    if v.status == VerificationStatus.missing:
        return bool(v.probable_file_ids)
    return False


def _allowed_ids_for_row(v: RequirementVerification) -> set[str]:
    return set(v.matched_file_ids) | set(v.probable_file_ids)


def _build_user_block(
    v: RequirementVerification,
    clients_by_id: dict[str, ClientFile],
) -> tuple[str, int]:
    allowed = _allowed_ids_for_row(v)
    lines: list[str] = [
        f"### requirement_id: {v.requirement_id}",
        f"- 序号: {v.ordinal}",
        f"- 类别: {v.category}",
        f"- 标准材料名称: {v.name}",
        f"- 招标「是否具备」: {v.tender_status}",
        f"- 备注: {v.remark}",
        "- 程序预筛（仅供参考，非最终依据）:",
        f"  - 当前状态码: {v.status.value}",
        f"  - 备注: {v.notes or '（无）'}",
        "- 客户文件（仅可引用下列 ID；**优先阅读「文件画像」**）:",
    ]
    nchars = sum(len(x) for x in lines)
    for fid in sorted(allowed):
        c = clients_by_id.get(fid)
        if not c:
            continue
        dp = c.document_profile
        if dp is not None:
            exc = (dp.text_excerpt or dp.effective_text or "").strip()
            if len(exc) > 5000:
                exc = exc[:2500] + "\n…(省略)…\n" + exc[-2000:]
            kw = "、".join(dp.keywords[:30])
            poss = "、".join(dp.possible_requirements[:12])
            chunk = (
                f"  - id={fid} 文件名={c.original_filename}\n"
                f"    画像: doc_category={dp.doc_category or ''} keywords={kw}\n"
                f"    可能材料项: {poss}\n"
                f"    画像备注: {(dp.llm_comment or '')[:800]}\n"
                f"    摘录:\n{exc or '（无）'}"
            )
        else:
            ex = _text_excerpt(c.extracted_text or "")
            chunk = (
                f"  - id={fid} 文件名={c.original_filename} 扩展名={c.extension}（无 document_profile，退回正文摘录）\n"
                f"    摘录:\n{ex}"
            )
        lines.append(chunk)
        nchars += len(chunk)
    return "\n".join(lines), nchars


def _assessment_to_suggested_status(a: str) -> VerificationStatus:
    if a == "satisfied":
        return VerificationStatus.matched
    if a == "not_satisfied":
        return VerificationStatus.missing
    return VerificationStatus.manual_review


def _apply_row_suggestion(
    v: RequirementVerification,
    row: Phase2AssistRow,
    *,
    apply_suggestions: bool,
    min_confidence: float,
) -> RequirementVerification:
    v2 = v.model_copy(deep=True)
    v2.llm_reviewed = True
    v2.llm_comment = row.rationale.strip()
    v2.llm_confidence = row.confidence
    v2.llm_suggested_status = _assessment_to_suggested_status(row.assessment)

    allowed = _allowed_ids_for_row(v)
    suggested = [x for x in row.suggested_matched_file_ids if x in allowed]

    if not apply_suggestions:
        return v2
    if row.assessment != "satisfied":
        return v2
    if row.confidence < min_confidence:
        return v2
    if not suggested:
        return v2

    tag = "【大模型辅助裁定（已应用）】"
    new_matched = sorted(set(v2.matched_file_ids) | set(suggested))
    new_probable = sorted(set(v2.probable_file_ids) - set(suggested))
    v2.matched_file_ids = new_matched
    v2.probable_file_ids = new_probable
    v2.status = VerificationStatus.matched
    if tag not in (v2.notes or ""):
        v2.notes = (v2.notes + "\n" if v2.notes else "") + tag
    return v2


def run_llm_assist_review(
    verifications: list[RequirementVerification],
    clients_by_id: dict[str, ClientFile],
    llm: LLMRuntimeConfig,
    *,
    apply_suggestions: bool = False,
    min_confidence: float = 0.85,
    progress: Callable[[str], None] | None = None,
) -> list[RequirementVerification]:
    def log(msg: str) -> None:
        if progress:
            progress(msg)

    targets = [v for v in verifications if _verification_needs_assist(v)]
    if not targets:
        log("大模型辅助：无待审查条目，跳过。")
        return verifications

    batches: list[list[RequirementVerification]] = []
    cur: list[RequirementVerification] = []
    cur_chars = 0
    for v in targets:
        block, n = _build_user_block(v, clients_by_id)
        if cur and (
            len(cur) >= _BATCH_MAX_ITEMS or cur_chars + n > _MAX_BATCH_CHARS
        ):
            batches.append(cur)
            cur = []
            cur_chars = 0
        cur.append(v)
        cur_chars += n
    if cur:
        batches.append(cur)

    advice_by_req: dict[str, Phase2AssistRow] = {}

    for bi, batch in enumerate(batches):
        parts: list[str] = [
            "请对下列材料项分别给出 JSON 中的 items 数组元素，requirement_id 必须与下文一致。",
            "",
        ]
        for v in batch:
            block, _ = _build_user_block(v, clients_by_id)
            parts.append(block)
            parts.append("")
        user = "\n".join(parts)
        try:
            out = chat_json(PHASE2_ASSIST_SYSTEM, user, Phase2AssistBatch, temperature=0.1, llm=llm)
            for item in out.items:
                rid = (item.requirement_id or "").strip()
                if rid and rid in {x.requirement_id for x in batch}:
                    advice_by_req[rid] = item
        except Exception as e:
            err = f"[LLM 调用失败] {e}"
            log(f"大模型辅助：批次 {bi + 1}/{len(batches)} 失败：{e}")
            for v in batch:
                advice_by_req[v.requirement_id] = Phase2AssistRow(
                    requirement_id=v.requirement_id,
                    assessment="unclear",
                    confidence=0.0,
                    rationale=err,
                    suggested_matched_file_ids=[],
                )

    out_list: list[RequirementVerification] = []
    for v in verifications:
        if v.requirement_id not in advice_by_req:
            out_list.append(v)
            continue
        row = advice_by_req[v.requirement_id]
        if row.requirement_id != v.requirement_id:
            row = row.model_copy(update={"requirement_id": v.requirement_id})
        out_list.append(
            _apply_row_suggestion(
                v,
                row,
                apply_suggestions=apply_suggestions,
                min_confidence=min_confidence,
            )
        )

    log(f"大模型辅助：已处理 {len(targets)} 条，分 {len(batches)} 批。")
    return out_list
