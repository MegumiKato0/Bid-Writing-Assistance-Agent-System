from __future__ import annotations

from bidprep.agents.llm import chat_json
from bidprep.config import settings
from bidprep.llm_runtime import LLMRuntimeConfig
from bidprep.schemas import PartialExtraction, TenderExtraction


EXTRACT_SYSTEM = """你是政府采购/工程招标文件的解析助手。只依据给定文本抽取信息，不要编造。
材料清单的 category 可归类为：响应文件组成、资格性要求、商务资料、技术方案、合同文件、其他材料 等。
status 使用：☐ 需准备、☑ 已具备、☐ 不需要、☐ 可选 之一（与招标文件语气一致）。
每条 material 尽量给出 source_page（该信息所在页码，整数）。若无法确定可省略。
fill_items 列出需要供应商填写的字段（公司信息、授权代表、报价等）。
purchaser_contact 与 agency_contact 填写采购人、代理机构的单位、地址、联系人、电话、邮箱（仅依据文本）。"""


def _chunks(text: str, max_chars: int, overlap: int) -> list[tuple[int, int, str]]:
    """返回 (start, end, chunk_text) 字符区间。"""
    if len(text) <= max_chars:
        return [(0, len(text), text)]
    out: list[tuple[int, int, str]] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + max_chars)
        out.append((start, end, text[start:end]))
        if end >= n:
            break
        start = max(0, end - overlap)
    return out


def extract_from_text(full_text: str, llm: LLMRuntimeConfig | None = None) -> TenderExtraction:
    chunks = _chunks(
        full_text,
        settings.extract_chunk_chars,
        settings.extract_chunk_overlap,
    )
    merged = PartialExtraction()
    for start, end, ch in chunks:
        header = f"【文本片段 字符{start}-{end}，共{len(full_text)}】\n"
        user = header + ch
        part = chat_json(EXTRACT_SYSTEM, user, PartialExtraction, llm=llm)
        merged = _merge_partial(merged, part)

    return _partial_to_tender(merged)


def _merge_partial(a: PartialExtraction, b: PartialExtraction) -> PartialExtraction:
    # project_basic: 后者非空则覆盖
    pb = a.project_basic.model_copy()
    for k, v in b.project_basic.model_dump().items():
        if v not in (None, "", []):
            setattr(pb, k, v)
    pc_a, pc_b = a.purchaser_contact, b.purchaser_contact
    pc = _merge_contact(pc_a, pc_b)
    ac = _merge_contact(a.agency_contact, b.agency_contact)
    mats = _dedupe_materials(a.materials + b.materials)
    fills = _dedupe_fill(a.fill_items + b.fill_items)
    cautions = _dedupe_str(a.cautions + b.cautions)
    parts = _dedupe_str(a.response_file_parts + b.response_file_parts)
    notes = _dedupe_str(a.raw_notes + b.raw_notes)
    ss = a.service_summary.model_copy()
    ss.scope_bullets = _dedupe_str(ss.scope_bullets + b.service_summary.scope_bullets)
    ss.key_dates = _dedupe_str(ss.key_dates + b.service_summary.key_dates)
    ss.focus_areas = _dedupe_str(ss.focus_areas + b.service_summary.focus_areas)
    return PartialExtraction(
        project_basic=pb,
        purchaser_contact=pc,
        agency_contact=ac,
        materials=mats,
        fill_items=fills,
        cautions=cautions,
        response_file_parts=parts,
        service_summary=ss,
        raw_notes=notes,
    )


def _dedupe_str(xs: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in xs:
        x = (x or "").strip()
        if not x or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _dedupe_materials(rows: list) -> list:
    from bidprep.schemas import MaterialRow

    seen: set[tuple[str, str]] = set()
    out: list[MaterialRow] = []
    for r in rows:
        key = (r.category.strip(), r.name.strip())
        if not key[1]:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _dedupe_fill(rows: list) -> list:
    from bidprep.schemas import FillItem

    seen: set[tuple[str, str]] = set()
    out: list[FillItem] = []
    for r in rows:
        key = (r.section.strip(), r.field_name.strip())
        if not key[1]:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _merge_contact(a, b):
    from bidprep.schemas import ContactBlock

    out = a.model_copy()
    for field in ContactBlock.model_fields:
        bv = getattr(b, field)
        if bv not in (None, "", []):
            setattr(out, field, bv)
    return out


def _partial_to_tender(p: PartialExtraction) -> TenderExtraction:
    collection: list[str] = []
    for f in p.fill_items:
        label = f.field_name or f.description
        if label:
            collection.append(label)
    collection = _dedupe_str(collection + p.cautions[:20])
    return TenderExtraction(
        project_basic=p.project_basic,
        purchaser_contact=p.purchaser_contact,
        agency_contact=p.agency_contact,
        materials=p.materials,
        fill_items=p.fill_items,
        cautions=p.cautions,
        response_file_parts=p.response_file_parts,
        service_summary=p.service_summary,
        collection_checklist=collection[:80],
    )
