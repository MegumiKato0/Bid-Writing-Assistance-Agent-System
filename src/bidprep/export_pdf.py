from __future__ import annotations

import re
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from bidprep.export_md import render_checklist_markdown
from bidprep.schemas import MergeMeta, TenderExtraction

_FONT_REGISTERED: str | None = None


def _find_cjk_font_path() -> str | None:
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    for p in candidates:
        if Path(p).is_file():
            return p
    return None


def _register_cjk_font() -> str:
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return _FONT_REGISTERED
    path = _find_cjk_font_path()
    if path and path.lower().endswith(".ttc"):
        try:
            pdfmetrics.registerFont(TTFont("BidPrepCJK", path, subfontIndex=0))
            _FONT_REGISTERED = "BidPrepCJK"
            return _FONT_REGISTERED
        except Exception:
            pass
    if path and path.lower().endswith(".ttf"):
        try:
            pdfmetrics.registerFont(TTFont("BidPrepCJK", path))
            _FONT_REGISTERED = "BidPrepCJK"
            return _FONT_REGISTERED
        except Exception:
            pass
    _FONT_REGISTERED = "Helvetica"
    return _FONT_REGISTERED


def _strip_inline_md(text: str) -> str:
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", text or "")
    return re.sub(r"`([^`]+)`", r"\1", t)


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    t = escape(_strip_inline_md(text or "")).replace("\n", "<br/>")
    return Paragraph(t, style)


def _base_styles() -> tuple[str, dict[str, ParagraphStyle]]:
    font = _register_cjk_font()
    base = getSampleStyleSheet()
    styles: dict[str, ParagraphStyle] = {}

    def make(name: str, parent: ParagraphStyle, **kw) -> ParagraphStyle:
        s = ParagraphStyle(name, parent=parent, **kw)
        s.fontName = font
        return s

    styles["normal"] = make(
        "bp_normal",
        base["Normal"],
        fontSize=10,
        leading=14,
        alignment=TA_LEFT,
        spaceAfter=6,
    )
    styles["h1"] = make(
        "bp_h1",
        base["Heading1"],
        fontSize=18,
        leading=22,
        spaceAfter=12,
        spaceBefore=6,
        textColor=colors.HexColor("#0f172a"),
    )
    styles["h2"] = make(
        "bp_h2",
        base["Heading2"],
        fontSize=14,
        leading=18,
        spaceAfter=8,
        spaceBefore=10,
        textColor=colors.HexColor("#1e3a5f"),
    )
    styles["small"] = make(
        "bp_small",
        base["Normal"],
        fontSize=8,
        leading=10,
        textColor=colors.grey,
        spaceAfter=4,
    )
    return font, styles


def _markdown_table_to_table(rows: list[list[str]], col_widths: list[float] | None, font_name: str) -> Table:
    t = Table(rows, colWidths=col_widths, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#66CCFF")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return t


def export_checklist_pdf(extraction: TenderExtraction, out_path: str | Path) -> None:
    """将「投标材料准备清单」Markdown 同源内容导出为 PDF。"""
    md = render_checklist_markdown(extraction)
    font_name, styles = _base_styles()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title="投标材料准备清单",
    )
    story: list = []
    lines = md.splitlines()
    i = 0
    in_code = False
    code_buf: list[str] = []

    def flush_code() -> None:
        nonlocal code_buf
        if not code_buf:
            return
        body = "\n".join(code_buf)
        story.append(_p(body, styles["small"]))
        story.append(Spacer(1, 0.2 * cm))
        code_buf = []

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if in_code:
                flush_code()
                in_code = False
            else:
                in_code = True
                code_buf = []
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        stripped = line.strip()
        if not stripped:
            story.append(Spacer(1, 0.15 * cm))
            i += 1
            continue
        if stripped == "---":
            story.append(Spacer(1, 0.25 * cm))
            i += 1
            continue
        if stripped.startswith("# "):
            story.append(_p(stripped[2:].strip(), styles["h1"]))
        elif stripped.startswith("## "):
            story.append(_p(stripped[3:].strip(), styles["h2"]))
        elif stripped.startswith("### "):
            story.append(_p(stripped[4:].strip(), styles["h2"]))
        elif stripped.startswith("|") and "|" in stripped[1:]:
            table_lines: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            rows_parsed: list[list[str]] = []
            for tl in table_lines:
                if re.match(r"^\|\s*[-:]+\s*\|", tl):
                    continue
                cells = [c.strip() for c in tl.strip("|").split("|")]
                rows_parsed.append(cells)
            if rows_parsed:
                w = doc.width / max(len(rows_parsed[0]), 1)
                cw = [w] * len(rows_parsed[0])
                story.append(_markdown_table_to_table(rows_parsed, cw, font_name))
                story.append(Spacer(1, 0.3 * cm))
            continue
        elif stripped.startswith("> "):
            story.append(_p(stripped[2:].strip(), styles["normal"]))
        elif stripped.startswith(("- ", "* ", "1. ")):
            story.append(_p(stripped, styles["normal"]))
        else:
            story.append(_p(stripped, styles["normal"]))
        i += 1

    flush_code()
    doc.build(story)


def export_materials_pdf(extraction: TenderExtraction, out_path: str | Path) -> None:
    """投标材料清单表 → PDF（与 xlsx 同源数据）。"""
    font_name, _ = _base_styles()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    headers = ["序号", "材料类别", "材料名称", "是否具备", "备注"]
    rows: list[list[str]] = [headers]
    for idx, m in enumerate(extraction.materials, start=1):
        remark = m.remark
        if m.source_page:
            remark = (remark + f"（约第{m.source_page}页）") if remark else f"约第{m.source_page}页"
        rows.append([str(idx), m.category, m.name, m.status, remark])
    if len(rows) == 1:
        rows.append(["1", "—", "请根据招标文件人工补充", "☐ 需准备", ""])

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title="投标材料清单表",
    )
    w = doc.width
    col_w = [0.08 * w, 0.18 * w, 0.30 * w, 0.14 * w, 0.30 * w]
    t = _markdown_table_to_table(rows, col_w, font_name)
    story = [
        Paragraph(
            escape("投标材料清单表"),
            ParagraphStyle(
                "title",
                fontName=font_name,
                fontSize=16,
                leading=20,
                alignment=TA_CENTER,
                spaceAfter=14,
            ),
        ),
        Spacer(1, 0.2 * cm),
        t,
    ]
    doc.build(story)


def export_fill_items_pdf(extraction: TenderExtraction, meta: MergeMeta | None, out_path: str | Path) -> None:
    """需要填空的内容项表 → PDF。"""
    font_name, _ = _base_styles()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    headers = ["序号", "章节/段落", "字段名称", "说明", "必填", "摘录", "页码", "客户填写"]
    rows: list[list[str]] = [headers]
    for idx, f in enumerate(extraction.fill_items, start=1):
        rows.append(
            [
                str(idx),
                f.section,
                f.field_name,
                f.description,
                "是" if f.required else "否",
                f.quote,
                str(f.page or ""),
                "",
            ]
        )
    if len(rows) == 1:
        rows.append(["1", "", "报价金额（小写/大写）", "按招标文件", "是", "", "", ""])

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=1.2 * cm,
        rightMargin=1.2 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title="需要填空的内容项表",
    )
    w = doc.width
    col_w = [0.05 * w, 0.10 * w, 0.12 * w, 0.15 * w, 0.06 * w, 0.28 * w, 0.06 * w, 0.10 * w]
    t = _markdown_table_to_table(rows, col_w, font_name)
    story = [
        Paragraph(
            escape("需要填空的内容项表"),
            ParagraphStyle(
                "title",
                fontName=font_name,
                fontSize=16,
                leading=20,
                alignment=TA_CENTER,
                spaceAfter=14,
            ),
        ),
        Spacer(1, 0.2 * cm),
        t,
    ]
    if meta and meta.review and meta.review.issues:
        story.append(Spacer(1, 0.5 * cm))
        story.append(
            Paragraph(
                escape(f"审查提示：共 {len(meta.review.issues)} 条，详见 extraction_meta.json / xlsx 审查记录表。"),
                ParagraphStyle("note", fontName=font_name, fontSize=9, leading=12, textColor=colors.grey),
            )
        )
    doc.build(story)
