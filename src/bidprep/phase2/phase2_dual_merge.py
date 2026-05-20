from __future__ import annotations

from bidprep.phase2.file_profile_schemas import AgentBBatchOutput, AgentBRequirementOutput, Phase2CoverageSnapshot
from bidprep.phase2.schemas import (
    ClientFile,
    MatchResult,
    Requirement,
    RequirementPriority,
    RequirementVerification,
    VerificationStatus,
)


def _sanitize_agent_row(ag: AgentBRequirementOutput, valid: set[str]) -> AgentBRequirementOutput:
    m = [x for x in ag.matched_file_ids if x in valid]
    p = [x for x in ag.probable_file_ids if x in valid and x not in m]
    return ag.model_copy(update={"matched_file_ids": m, "probable_file_ids": p})


def merge_agent_b_into_verifications(
    rule_verifications: list[RequirementVerification],
    requirements: list[Requirement],
    agent_b: AgentBBatchOutput | None,
    *,
    valid_file_ids: set[str],
) -> list[RequirementVerification]:
    """将 Agent B 输出合并进规则核验行（豁免与替代行不改）。"""
    req_by_id = {r.id: r for r in requirements}
    agent_by_id: dict[str, AgentBRequirementOutput] = {}
    if agent_b:
        for it in agent_b.items:
            rid = (it.requirement_id or "").strip()
            if rid:
                agent_by_id[rid] = _sanitize_agent_row(it, valid_file_ids)

    out: list[RequirementVerification] = []
    for v in rule_verifications:
        req = req_by_id.get(v.requirement_id)
        if req is None:
            out.append(v)
            continue
        if req.priority == RequirementPriority.exempt:
            out.append(v)
            continue
        if v.substitution_applied:
            out.append(v)
            continue
        ag = agent_by_id.get(v.requirement_id)
        if ag is None:
            out.append(v)
            continue

        nv = v.model_copy(deep=True)
        nv.matched_file_ids = sorted(set(nv.matched_file_ids) | set(ag.matched_file_ids))
        nv.probable_file_ids = sorted(
            (set(nv.probable_file_ids) | set(ag.probable_file_ids)) - set(nv.matched_file_ids)
        )
        if ag.review_notes.strip() and not ag.review_notes.strip().startswith("[Agent B"):
            tag = f"【Agent B】{ag.review_notes.strip()}"
            nv.notes = (nv.notes + "\n" if nv.notes else "") + tag

        if nv.matched_file_ids:
            nv.status = VerificationStatus.matched
        elif nv.probable_file_ids:
            cond = any(x in (req.remark or "") for x in ("若", "如有", "若有", "如果"))
            opt = "可选" in (req.tender_status or "") or req.priority == RequirementPriority.optional
            nv.status = (
                VerificationStatus.manual_review if (cond or opt) else VerificationStatus.probable_match
            )
        else:
            nv.status = (
                VerificationStatus.missing
                if req.priority == RequirementPriority.required
                else VerificationStatus.manual_review
            )
        nv.candidate_file_ids = sorted(
            set(nv.matched_file_ids) | set(nv.probable_file_ids) | set(nv.substitution_source_file_ids)
        )
        out.append(nv)
    return out


def verifications_to_match_results(verifications: list[RequirementVerification]) -> list[MatchResult]:
    """由最终核验行生成归档用 MatchResult 列表。"""
    ms: list[MatchResult] = []
    for v in verifications:
        if v.priority == RequirementPriority.exempt:
            continue
        if v.substitution_applied:
            for fid in v.substitution_source_file_ids:
                ms.append(
                    MatchResult(
                        requirement_id=v.requirement_id,
                        client_file_id=fid,
                        confidence=0.78,
                        match_kind="direct",
                        rationale="替代满足",
                        keyword_hits=["substitution_rule"],
                    )
                )
            continue
        for fid in v.matched_file_ids:
            ms.append(
                MatchResult(
                    requirement_id=v.requirement_id,
                    client_file_id=fid,
                    confidence=0.9,
                    match_kind="direct",
                    rationale="核验匹配",
                    keyword_hits=[],
                )
            )
        for fid in v.probable_file_ids:
            ms.append(
                MatchResult(
                    requirement_id=v.requirement_id,
                    client_file_id=fid,
                    confidence=0.45,
                    match_kind="probable",
                    rationale="疑似匹配",
                    keyword_hits=[],
                )
            )
    return ms


def build_coverage_snapshot(
    clients: list[ClientFile],
    final_verifications: list[RequirementVerification],
    agent_b_unused: list[str],
) -> Phase2CoverageSnapshot:
    all_ids = sorted({c.id for c in clients})
    valid = set(all_ids)
    used: set[str] = set()
    rmap: dict[str, list[str]] = {}
    for v in final_verifications:
        u = set(v.matched_file_ids) | set(v.probable_file_ids) | set(v.substitution_source_file_ids)
        used |= u
        rmap[v.requirement_id] = sorted(u)
    prog_unused = sorted(valid - used)
    ag_unused = sorted(set(agent_b_unused) & valid)
    unmatched_req = sorted(
        {
            v.requirement_id
            for v in final_verifications
            if v.priority != RequirementPriority.exempt and v.status == VerificationStatus.missing
        }
    )
    return Phase2CoverageSnapshot(
        all_client_file_ids=all_ids,
        used_file_ids=sorted(used & valid),
        unused_file_ids=prog_unused,
        requirement_file_map=rmap,
        agent_b_unused_file_ids=ag_unused,
        program_unused_file_ids=prog_unused,
        unmatched_requirement_ids=unmatched_req,
    )
