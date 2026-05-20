from __future__ import annotations

from bidprep.phase2.file_profile_schemas import ClientDocumentProfile
from bidprep.phase2.schemas import MatchResult, Requirement, RequirementPriority, RequirementVerification, VerificationStatus

_SUBSTITUTION_SOURCE_KEYWORDS = ("资格承诺", "信用承诺", "承诺函", "声明函")
_SUBSTITUTION_TARGET_KEYWORDS = ("财务", "纳税", "税收", "社保", "社会保险", "缴费")
_CONDITIONAL_KEYWORDS = ("如有", "如适用", "如需", "若有", "可选", "如无", "如能提供")


def requirement_is_conditional(requirement: Requirement) -> bool:
    text = f"{requirement.tender_status} {requirement.remark} {requirement.name}"
    return any(keyword in text for keyword in _CONDITIONAL_KEYWORDS) or requirement.priority == RequirementPriority.optional


def _is_substitution_source(profile: ClientDocumentProfile) -> bool:
    haystack = f"{profile.original_name}\n{profile.doc_category or ''}\n{profile.effective_text[:8000]}"
    return any(keyword in haystack for keyword in _SUBSTITUTION_SOURCE_KEYWORDS)


def requirement_accepts_substitution(requirement: Requirement) -> bool:
    text = f"{requirement.name} {requirement.remark}"
    return any(keyword in text for keyword in _SUBSTITUTION_TARGET_KEYWORDS)


def find_substitution_source_ids(profiles_by_id: dict[str, ClientDocumentProfile]) -> list[str]:
    return [file_id for file_id, profile in profiles_by_id.items() if _is_substitution_source(profile)]


def apply_substitution_matches(
    requirements: list[Requirement],
    clients: list,
    existing: list[MatchResult],
) -> list[MatchResult]:
    profiles_by_id = {
        client.id: client.document_profile
        for client in clients
        if getattr(client, "document_profile", None) is not None
    }
    source_ids = find_substitution_source_ids(profiles_by_id)
    if not source_ids:
        return existing

    covered = {(match.requirement_id, match.client_file_id) for match in existing}
    out = list(existing)
    for requirement in requirements:
        if requirement.priority == RequirementPriority.exempt or not requirement_accepts_substitution(requirement):
            continue
        for source_id in source_ids:
            key = (requirement.id, source_id)
            if key in covered:
                continue
            out.append(
                MatchResult(
                    requirement_id=requirement.id,
                    client_file_id=source_id,
                    confidence=0.46,
                    match_kind="probable",
                    rationale="替代关系候选",
                    keyword_hits=["substitution_rule"],
                )
            )
            covered.add(key)
    return out


def apply_substitution_engine(
    requirements: list[Requirement],
    verifications: list[RequirementVerification],
    profiles_by_id: dict[str, ClientDocumentProfile],
) -> list[RequirementVerification]:
    requirement_map = {requirement.id: requirement for requirement in requirements}
    substitution_sources = find_substitution_source_ids(profiles_by_id)
    out: list[RequirementVerification] = []

    for verification in verifications:
        requirement = requirement_map[verification.requirement_id]
        updated = verification

        if (
            verification.status not in {VerificationStatus.matched, VerificationStatus.substituted}
            and requirement_accepts_substitution(requirement)
            and substitution_sources
        ):
            updated = updated.model_copy(
                update={
                    "status": VerificationStatus.substituted,
                    "substitution_applied": True,
                    "substitution_source_file_ids": substitution_sources,
                    "candidate_file_ids": sorted(set(updated.candidate_file_ids) | set(substitution_sources)),
                    "notes": (updated.notes + "；由承诺函/声明函替代满足").strip("；"),
                    "match_source": updated.match_source or "substitution_engine",
                }
            )

        if updated.status in {VerificationStatus.missing, VerificationStatus.probable_match, VerificationStatus.manual_review} and requirement_is_conditional(requirement):
            updated = updated.model_copy(
                update={
                    "status": VerificationStatus.conditional_match,
                    "conditional": True,
                    "condition_note": "该 requirement 带有条件性或可选性，需结合招标上下文人工确认",
                    "notes": (updated.notes + "；条件满足/条件核验").strip("；"),
                }
            )

        out.append(updated)
    return out
