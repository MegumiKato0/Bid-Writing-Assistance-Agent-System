from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from bidprep.agents.extract import extract_from_text
from bidprep.agents.review import review_extraction
from bidprep.config import settings
from bidprep.export_md import export_checklist_md
from bidprep.export_pdf import (
    export_checklist_pdf,
    export_fill_items_pdf,
    export_materials_pdf,
)
from bidprep.export_xlsx import export_fill_items_xlsx, export_materials_xlsx
from bidprep.merge import merge_all
from bidprep.pdf_text import PageText, extract_pages, pages_to_full_text, slice_pages_text
from bidprep.rules_extract import extract_rules
from bidprep.llm_runtime import LLMRuntimeConfig
from bidprep.schemas import TenderExtraction


def _collect_review_pages(extraction: TenderExtraction, all_pages: int) -> list[int]:
    pages: set[int] = set()
    for m in extraction.materials:
        if m.source_page and 1 <= m.source_page <= all_pages:
            pages.add(m.source_page)
    for f in extraction.fill_items:
        if f.page and 1 <= f.page <= all_pages:
            pages.add(f.page)
    # 始终包含前几页（通常含邀请书）
    for p in range(1, min(6, all_pages + 1)):
        pages.add(p)
    ordered = sorted(pages)
    return ordered[: settings.max_pages_for_review]


def run_pipeline(
    pdf_path: str | Path,
    out_dir: str | Path,
    *,
    progress: Callable[[str], None] | None = None,
    skip_llm: bool = False,
    llm: LLMRuntimeConfig | None = None,
) -> dict[str, Any]:
    def log(msg: str) -> None:
        if progress:
            progress(msg)

    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log("正在读取 PDF…")
    pages: list[PageText] = extract_pages(str(pdf_path))
    full_text = pages_to_full_text(pages)

    log("规则兜底抽取…")
    rules_basic, rules_hits, rules_pc, rules_ac = extract_rules(full_text)

    if skip_llm:
        agent_ex = TenderExtraction()
    else:
        log("Agent1：结构化抽取（可能较慢）…")
        agent_ex = extract_from_text(full_text, llm=llm)

    log("程序合并（规则优先）…")
    merged, meta = merge_all(agent_ex, rules_basic, rules_pc, rules_ac, rules_hits, review=None)

    if not skip_llm:
        log("Agent2：审查…")
        review_pages = _collect_review_pages(merged, len(pages))
        evidence = slice_pages_text(pages, review_pages)
        review = review_extraction(merged, evidence, llm=llm)
        baseline = review.patched_extraction if review.patched_extraction is not None else merged
        merged, meta = merge_all(
            baseline,
            rules_basic,
            rules_pc,
            rules_ac,
            rules_hits,
            review=review,
        )

    log("导出文件…")
    md_path = out_dir / "投标材料准备清单.md"
    mat_path = out_dir / "投标材料清单表.xlsx"
    fill_path = out_dir / "需要填空的内容项表.xlsx"
    md_pdf_path = out_dir / "投标材料准备清单.pdf"
    mat_pdf_path = out_dir / "投标材料清单表.pdf"
    fill_pdf_path = out_dir / "需要填空的内容项表.pdf"
    meta_path = out_dir / "extraction_meta.json"

    export_checklist_md(merged, md_path)
    export_materials_xlsx(merged, mat_path)
    export_fill_items_xlsx(merged, meta, fill_path)
    export_checklist_pdf(merged, md_pdf_path)
    export_materials_pdf(merged, mat_pdf_path)
    export_fill_items_pdf(merged, meta, fill_pdf_path)

    meta_path.write_text(
        json.dumps(meta.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log("完成。")
    return {
        "out_dir": str(out_dir),
        "files": {
            "markdown": str(md_path),
            "materials_xlsx": str(mat_path),
            "fill_xlsx": str(fill_path),
            "markdown_pdf": str(md_pdf_path),
            "materials_pdf": str(mat_pdf_path),
            "fill_pdf": str(fill_pdf_path),
            "meta": str(meta_path),
        },
    }
