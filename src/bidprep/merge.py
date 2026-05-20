from __future__ import annotations

from typing import Any

from bidprep.schemas import (
    ContactBlock,
    FieldProvenance,
    MergeMeta,
    ProjectBasicInfo,
    ReviewResult,
    TenderExtraction,
)


def _pb_overlay(rule: ProjectBasicInfo, agent: ProjectBasicInfo) -> tuple[ProjectBasicInfo, list[FieldProvenance]]:
    prov: list[FieldProvenance] = []
    out = agent.model_copy()
    for field in rule.model_fields:
        rv = getattr(rule, field)
        av = getattr(agent, field)
        if rv not in (None, "", []):
            setattr(out, field, rv)
            prov.append(FieldProvenance(field=f"project_basic.{field}", source="rule", detail=str(rv)[:120]))
        elif av not in (None, "", []):
            prov.append(FieldProvenance(field=f"project_basic.{field}", source="agent", detail=str(av)[:120]))
    return out, prov


def _contact_overlay(rule: ContactBlock, agent: ContactBlock, prefix: str) -> tuple[ContactBlock, list[FieldProvenance]]:
    prov: list[FieldProvenance] = []
    out = agent.model_copy()
    for field in rule.model_fields:
        rv = getattr(rule, field)
        av = getattr(agent, field)
        if rv not in (None, "", []):
            setattr(out, field, rv)
            prov.append(FieldProvenance(field=f"{prefix}.{field}", source="rule", detail=str(rv)[:120]))
        elif av not in (None, "", []):
            prov.append(FieldProvenance(field=f"{prefix}.{field}", source="agent", detail=str(av)[:120]))
    return out, prov


def merge_all(
    agent_extraction: TenderExtraction,
    rules_basic: ProjectBasicInfo,
    rules_purchaser: ContactBlock,
    rules_agency: ContactBlock,
    rules_hits: dict[str, Any],
    review: ReviewResult | None = None,
) -> tuple[TenderExtraction, MergeMeta]:
    te = agent_extraction.model_copy()
    prov: list[FieldProvenance] = []

    pb, p1 = _pb_overlay(rules_basic, te.project_basic)
    te.project_basic = pb
    prov.extend(p1)

    pc, p2 = _contact_overlay(rules_purchaser, te.purchaser_contact, "purchaser_contact")
    te.purchaser_contact = pc
    prov.extend(p2)

    ac, p3 = _contact_overlay(rules_agency, te.agency_contact, "agency_contact")
    te.agency_contact = ac
    prov.extend(p3)

    if review and review.patched_extraction:
        te = review.patched_extraction.model_copy()
        # 再次叠规则，避免审查抹掉高置信规则字段
        te.project_basic, _ = _pb_overlay(rules_basic, te.project_basic)
        te.purchaser_contact, _ = _contact_overlay(rules_purchaser, te.purchaser_contact, "purchaser_contact")
        te.agency_contact, _ = _contact_overlay(rules_agency, te.agency_contact, "agency_contact")
        prov.append(FieldProvenance(field="*", source="review", detail="patched_extraction applied"))

    meta = MergeMeta(provenance=prov, review=review, rules_snapshot=rules_hits)
    return te, meta
