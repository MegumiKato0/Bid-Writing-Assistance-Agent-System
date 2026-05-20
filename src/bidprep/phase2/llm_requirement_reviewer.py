from __future__ import annotations

from typing import Callable

from bidprep.agents.llm import chat_json
from bidprep.llm_runtime import LLMRuntimeConfig
from bidprep.phase2.file_profile_schemas import AgentBBatchOutput, AgentBRequirementOutput, ClientDocumentProfile
from bidprep.phase2.schemas import Requirement

AGENT_B_SYSTEM = """你是政府采购投标材料总审助手（Agent B）。你已收到**全部**客户文件的「程序侧 + Agent A 合并后的文件画像」
（含摘录与关键词等），**没有**原始二进制，也**没有**仅压缩后的 verification 一行结论。
你的任务：
1. 对照每一条招标材料要求（requirement），判断哪些客户文件**可能**满足或佐证该要求，输出 matched_file_ids（较强）与 probable_file_ids（较弱）。
2. 所有文件 ID 必须从题目给出的列表中选择，禁止编造 ID。
3. 输出 unused_file_ids：任何你认为**未被任何材料项有效引用或仅为重复/无关**的客户文件 ID。
4. 豁免类（招标已标明不需要）的材料项可给空匹配并在 review_notes 说明。
5. 你**不**做最终法律效力判断；程序会结合规则引擎与 Agent A 结果做合并。"""

_MAX_CHARS = 36000
_BATCH_REQ = 25


def _req_block(r: Requirement) -> str:
    return (
        f"- id={r.id} 序号={r.ordinal} 类别={r.category} 名称={r.name} "
        f"招标是否具备={r.tender_status} 备注={r.remark} 优先级={r.priority.value}"
    )


def _profile_block_doc(p: ClientDocumentProfile) -> str:
    kw = "、".join(p.keywords[:24])
    poss = "、".join(p.possible_requirements[:18])
    pn = "、".join(p.person_names[:12])
    exc = (p.text_excerpt or p.effective_text or "").strip()
    if len(exc) > 9000:
        exc = exc[:4500] + "\n…(省略)…\n" + exc[-3500:]
    llm_part = (p.llm_comment or "").strip()
    if len(llm_part) > 1200:
        llm_part = llm_part[:1200] + "…"
    return (
        f"### {p.file_id} 文件={p.original_name}\n"
        f"doc_category: {p.doc_category or ''}\n"
        f"keywords: {kw}\n"
        f"possible_requirements(规则/AgentA): {poss}\n"
        f"company: {p.company_name or ''}  persons: {pn}\n"
        f"agent_a/画像备注: {llm_part}\n"
        f"text_excerpt:\n{exc or '（无文本）'}\n"
    )


def run_agent_b_requirement_review(
    requirements: list[Requirement],
    profiles_by_id: dict[str, ClientDocumentProfile],
    valid_file_ids: set[str],
    llm: LLMRuntimeConfig,
    *,
    progress: Callable[[str], None] | None = None,
) -> AgentBBatchOutput:
    def log(msg: str) -> None:
        if progress:
            progress(msg)

    if not requirements:
        return AgentBBatchOutput()

    prof_lines = [_profile_block_doc(p) for p in profiles_by_id.values()]
    prof_blob = "\n".join(prof_lines)
    id_list = ", ".join(sorted(valid_file_ids))

    merged_items: list[AgentBRequirementOutput] = []
    merged_unused: set[str] = set()
    global_chunks: list[str] = []

    req_batches: list[list[Requirement]] = []
    cur: list[Requirement] = []
    cur_chars = 0
    for r in requirements:
        b = _req_block(r)
        if cur and (len(cur) >= _BATCH_REQ or cur_chars + len(b) + len(prof_blob) > _MAX_CHARS):
            req_batches.append(cur)
            cur = []
            cur_chars = 0
        cur.append(r)
        cur_chars += len(b)
    if cur:
        req_batches.append(cur)

    for bi, rq in enumerate(req_batches):
        req_part = "\n".join(_req_block(r) for r in rq)
        user = (
            f"## 全部客户文件画像（共 {len(profiles_by_id)} 个）\n{prof_blob}\n\n"
            f"## 有效文件 ID 全集\n{id_list}\n\n"
            f"## 本批招标材料项\n{req_part}\n\n"
            "请输出 JSON：items 仅包含本批 requirement_id；并给出本批可见范围内的 unused_file_ids（可为空，最终由程序与全局汇总）。"
        )
        try:
            part = chat_json(
                AGENT_B_SYSTEM,
                user,
                AgentBBatchOutput,
                temperature=0.15,
                llm=llm,
            )
            merged_items.extend(part.items)
            for u in part.unused_file_ids:
                if u in valid_file_ids:
                    merged_unused.add(u)
            if part.global_notes:
                global_chunks.append(part.global_notes)
        except Exception as e:
            log(f"Agent B：批次 {bi + 1}/{len(req_batches)} 失败：{e}")
            for r in rq:
                merged_items.append(
                    AgentBRequirementOutput(
                        requirement_id=r.id,
                        review_notes=f"[Agent B 调用失败] {e}",
                        confidence=0.0,
                    )
                )

    by_rid: dict[str, AgentBRequirementOutput] = {}
    for it in merged_items:
        rid = (it.requirement_id or "").strip()
        if rid:
            by_rid[rid] = it

    final_items = [
        by_rid.get(r.id, AgentBRequirementOutput(requirement_id=r.id, review_notes="Agent B 未返回该行"))
        for r in requirements
    ]

    program_unused_guess = sorted(merged_unused & valid_file_ids)
    return AgentBBatchOutput(
        items=final_items,
        unused_file_ids=program_unused_guess,
        global_notes="\n---\n".join(global_chunks)[:8000],
    )
