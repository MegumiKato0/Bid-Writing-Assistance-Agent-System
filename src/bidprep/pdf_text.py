from __future__ import annotations

from dataclasses import dataclass

import fitz  # PyMuPDF


@dataclass
class PageText:
    page_no: int  # 1-based
    text: str


def extract_pages(pdf_path: str) -> list[PageText]:
    doc = fitz.open(pdf_path)
    try:
        out: list[PageText] = []
        for i in range(doc.page_count):
            page = doc.load_page(i)
            text = page.get_text("text") or ""
            out.append(PageText(page_no=i + 1, text=text.strip()))
        return out
    finally:
        doc.close()


def pages_to_full_text(pages: list[PageText]) -> str:
    parts: list[str] = []
    for p in pages:
        parts.append(f"【第{p.page_no}页】\n{p.text}")
    return "\n\n".join(parts)


def slice_pages_text(pages: list[PageText], page_numbers: list[int]) -> str:
    wanted = {n for n in page_numbers if n > 0}
    parts: list[str] = []
    for p in pages:
        if p.page_no in wanted:
            parts.append(f"【第{p.page_no}页】\n{p.text}")
    return "\n\n".join(parts)
