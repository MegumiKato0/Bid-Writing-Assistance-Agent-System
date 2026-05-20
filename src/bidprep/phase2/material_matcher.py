from __future__ import annotations

import re
from collections import defaultdict

from bidprep.phase2.file_profile_schemas import ClientDocumentProfile, LlmFileReadingResult
from bidprep.phase2.schemas import (
    ClientFile,
    FileUsageResult,
    MatchResult,
    ProfileMatchOutcome,
    Requirement,
    RequirementPriority,
    RequirementVerification,
)

_DIRECT_TH = 0.56
_PROBABLE_TH = 0.30


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def _score_profile_against_requirement(requirement: Requirement, profile: ClientDocumentProfile) -> tuple[float, list[str], str]:
    req_name = _norm(requirement.name)
    req_category = _norm(requirement.category)
    haystack = _norm(
        "\n".join(
            [
                profile.original_name,
                profile.doc_category or "",
                " ".join(profile.detected_material_types),
                " ".join(profile.keywords[:36]),
                " ".join(profile.candidate_requirement_names[:16]),
                " ".join(profile.candidate_requirement_ids[:16]),
                profile.effective_text[:12000],
            ]
        )
    )

    score = 0.0
    hits: list[str] = []
    reasons: list[str] = []

    if req_name and req_name in haystack:
        score += 0.62
        hits.append("requirement_name")
        reasons.append("命中 requirement 全称")

    for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,12}", requirement.name):
        compact = _norm(token)
        if compact and compact in haystack:
            score += min(0.18, 0.04 * len(token))
            hits.append(token)
            reasons.append(f"命中关键词 {token}")

    if req_category and req_category in haystack:
        score += 0.10
        hits.append("category")
        reasons.append("命中材料类别")

    if requirement.id in profile.candidate_requirement_ids:
        score += 0.24
        hits.append("agent_a_requirement_id")
        reasons.append("Agent A 建议命中 requirement_id")

    for item in profile.candidate_requirement_names:
        if _norm(item) == req_name:
            score += 0.20
            hits.append("agent_a_requirement_name")
            reasons.append("Agent A 建议命中 requirement 名称")
            break

    confidence = min(0.98, score)
    return confidence, hits, "；".join(dict.fromkeys(reasons)) or "弱匹配"


def match_files_to_requirements(requirements: list[Requirement], clients: list[ClientFile]) -> list[MatchResult]:
    matches: list[MatchResult] = []
    for client in clients:
        if client.document_profile is None:
            continue
        for requirement in requirements:
            score, hits, rationale = _score_profile_against_requirement(requirement, client.document_profile)
            if score >= _DIRECT_TH:
                matches.append(
                    MatchResult(
                        requirement_id=requirement.id,
                        client_file_id=client.id,
                        confidence=round(score, 4),
                        match_kind="direct",
                        rationale=rationale,
                        keyword_hits=hits,
                    )
                )
            elif score >= _PROBABLE_TH:
                matches.append(
                    MatchResult(
                        requirement_id=requirement.id,
                        client_file_id=client.id,
                        confidence=round(score, 4),
                        match_kind="probable",
                        rationale=rationale,
                        keyword_hits=hits,
                    )
                )
    return matches


def match_profiles_to_requirements(
    requirements: list[Requirement],
    file_profiles: list[ClientDocumentProfile],
    rule_matches: list[MatchResult],
    llm_readings: dict[str, LlmFileReadingResult] | None = None,
    *,
    all_file_ids: set[str],
    requirement_name_by_id: dict[str, str] | None = None,
) -> ProfileMatchOutcome:
    llm_readings = llm_readings or {}
    requirement_name_by_id = requirement_name_by_id or {req.id: req.name for req in requirements}

    merged: dict[tuple[str, str], MatchResult] = {}
    rule_pairs: set[tuple[str, str]] = set()
    llm_pairs: set[tuple[str, str]] = set()

    for match in rule_matches:
        key = (match.requirement_id, match.client_file_id)
        merged[key] = match if key not in merged or match.confidence > merged[key].confidence else merged[key]
        rule_pairs.add(key)

    valid_requirements = {req.id for req in requirements}
    for file_id, reading in llm_readings.items():
        for req_id in reading.suggested_requirement_ids:
            if req_id not in valid_requirements:
                continue
            key = (req_id, file_id)
            candidate = MatchResult(
                requirement_id=req_id,
                client_file_id=file_id,
                confidence=round(max(0.32, min(0.86, (reading.confidence or 0.45))), 4),
                match_kind="probable",
                rationale="Agent A 文件理解建议",
                keyword_hits=["llm_file_reader"],
            )
            merged[key] = candidate if key not in merged or candidate.confidence > merged[key].confidence else merged[key]
            llm_pairs.add(key)

    matches = list(merged.values())
    file_to_reqs: dict[str, set[str]] = defaultdict(set)
    file_to_kinds: dict[str, set[str]] = defaultdict(set)
    for match in matches:
        file_to_reqs[match.client_file_id].add(match.requirement_id)
        file_to_kinds[match.client_file_id].add(match.match_kind)

    file_usage: list[FileUsageResult] = []
    profiles_by_id = {profile.file_id: profile for profile in file_profiles}
    for file_id in sorted(all_file_ids):
        profile = profiles_by_id.get(file_id)
        req_ids = sorted(file_to_reqs.get(file_id, set()))
        used = bool(req_ids)
        status: str
        if not used:
            status = "unused"
        elif file_to_kinds.get(file_id) == {"probable"}:
            status = "manual_review"
        else:
            status = "used"
        file_usage.append(
            FileUsageResult(
                file_id=file_id,
                original_name=profile.original_name if profile else "",
                doc_category=profile.doc_category if profile else None,
                matched_requirement_ids=req_ids,
                matched_requirement_names=[requirement_name_by_id.get(req_id, req_id) for req_id in req_ids],
                used=used,
                status=status,
            )
        )

    match_source_by_requirement: dict[str, str] = {}
    for requirement in requirements:
        has_rule = any(req_id == requirement.id for req_id, _ in rule_pairs)
        has_llm = any(req_id == requirement.id for req_id, _ in llm_pairs)
        if has_rule and has_llm:
            match_source_by_requirement[requirement.id] = "rule_plus_llm"
        elif has_rule:
            match_source_by_requirement[requirement.id] = "rule_only"
        elif has_llm:
            match_source_by_requirement[requirement.id] = "llm_only"

    unused = sorted(all_file_ids - set(file_to_reqs.keys()))
    return ProfileMatchOutcome(
        matches=matches,
        match_source_by_requirement=match_source_by_requirement,
        file_usage=file_usage,
        unused_file_ids=unused,
    )


def rebuild_file_usage_from_verifications(
    all_file_ids: set[str],
    file_profiles: list[ClientDocumentProfile],
    verifications: list[RequirementVerification],
    requirement_name_by_id: dict[str, str],
) -> tuple[list[FileUsageResult], list[str]]:
    file_to_reqs: dict[str, set[str]] = defaultdict(set)
    file_to_kinds: dict[str, set[str]] = defaultdict(set)
    for verification in verifications:
        if verification.priority == RequirementPriority.exempt:
            continue
        for file_id in verification.matched_file_ids:
            file_to_reqs[file_id].add(verification.requirement_id)
            file_to_kinds[file_id].add("direct")
        for file_id in verification.probable_file_ids:
            file_to_reqs[file_id].add(verification.requirement_id)
            file_to_kinds[file_id].add("probable")
        for file_id in verification.substitution_source_file_ids:
            file_to_reqs[file_id].add(verification.requirement_id)
            file_to_kinds[file_id].add("direct")

    profiles_by_id = {profile.file_id: profile for profile in file_profiles}
    file_usage: list[FileUsageResult] = []
    for file_id in sorted(all_file_ids):
        profile = profiles_by_id.get(file_id)
        req_ids = sorted(file_to_reqs.get(file_id, set()))
        used = bool(req_ids)
        if not used:
            status = "unused"
        elif file_to_kinds.get(file_id) == {"probable"}:
            status = "manual_review"
        else:
            status = "used"
        file_usage.append(
            FileUsageResult(
                file_id=file_id,
                original_name=profile.original_name if profile else "",
                doc_category=profile.doc_category if profile else None,
                matched_requirement_ids=req_ids,
                matched_requirement_names=[requirement_name_by_id.get(req_id, req_id) for req_id in req_ids],
                used=used,
                status=status,
            )
        )
    unused = sorted(all_file_ids - set(file_to_reqs.keys()))
    return file_usage, unused
