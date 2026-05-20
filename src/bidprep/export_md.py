from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from bidprep.schemas import TenderExtraction


def render_checklist_markdown(extraction: TenderExtraction) -> str:
    tpl_dir = Path(__file__).resolve().parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(tpl_dir)),
        autoescape=select_autoescape(enabled_extensions=()),
    )
    tpl = env.get_template("checklist.md.j2")
    return tpl.render(
        project_basic=extraction.project_basic,
        purchaser_contact=extraction.purchaser_contact,
        agency_contact=extraction.agency_contact,
        materials=extraction.materials,
        response_file_parts=extraction.response_file_parts,
        service_summary=extraction.service_summary,
        cautions=extraction.cautions,
        collection_checklist=extraction.collection_checklist,
        generated_at=datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
    )


def export_checklist_md(extraction: TenderExtraction, out_path: str | Path) -> None:
    text = render_checklist_markdown(extraction)
    Path(out_path).write_text(text, encoding="utf-8")
