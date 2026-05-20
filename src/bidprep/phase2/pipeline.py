from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Callable, Sequence

from bidprep.llm_runtime import LLMRuntimeConfig
from bidprep.phase2.archive_manager import (
    archive_unused_client_files,
    archive_verification_results,
    write_extract_sidecars,
    write_ingested_files_manifest,
)
from bidprep.phase2.baseline_loader import resolve_and_load_baselines
from bidprep.phase2.checklist_filler import write_filled_materials_checklist
from bidprep.phase2.document_classifier import apply_classification
from bidprep.phase2.file_ingest import ingest_many_files, ingest_path
from bidprep.phase2.file_profile_schemas import ClientDocumentProfile, Phase2CoverageSnapshot, RequirementReviewBatch
from bidprep.phase2.file_profiles import build_file_profiles
from bidprep.phase2.llm_assist import run_llm_assist_review
from bidprep.phase2.llm_file_reader import merge_llm_file_reading_into_profiles, run_llm_file_reader
from bidprep.phase2.material_matcher import (
    match_files_to_requirements,
    match_profiles_to_requirements,
    rebuild_file_usage_from_verifications,
)
from bidprep.phase2.requirement_verifier import run_requirement_verifier
from bidprep.phase2.schemas import (
    ClientFile,
    MatchResult,
    Phase2Artifacts,
    Requirement,
    RequirementPriority,
    RequirementVerification,
    VerificationStatus,
)
from bidprep.phase2.substitution_engine import apply_substitution_engine, apply_substitution_matches, requirement_is_conditional
from bidprep.phase2.text_extractor import hydrate_clients_text
from bidprep.phase2.verification_reporter import (
    write_archive_index_json,
    write_coverage_snapshot_json,
    write_file_usage_json,
    write_missing_md,
    write_unused_and_unmatched_md,
    write_verification_json,
    write_verification_xlsx,
)


def _log(progress: Callable[[str], None] | None, message: str) -> None:
    if progress:
        progress(message)


def _dedupe_clients(clients: list[ClientFile]) -> list[ClientFile]:
    seen: set[str] = set()
    out: list[ClientFile] = []
    for client in clients:
        if client.sha256 in seen:
            continue
        seen.add(client.sha256)
        out.append(client)
    return out


def _profile_key_information(profile: ClientDocumentProfile) -> list[str]:
    info = list(profile.key_information)
    if profile.llm_summary and profile.llm_summary not in info:
        info.append(profile.llm_summary)
    return info[:10]


def _merge_requirement_results(
    requirements: list[Requirement],
    match_outcome,
    agent_b_out: RequirementReviewBatch,
    profiles_by_id: dict[str, ClientDocumentProfile],
    *,
    llm_global_notes: str = "",
) -> list[RequirementVerification]:
    rule_match_by_requirement: dict[str, list[MatchResult]] = defaultdict(list)
    for match in match_outcome.matches:
        rule_match_by_requirement[match.requirement_id].append(match)

    agent_b_by_requirement = {item.requirement_id: item for item in agent_b_out.items}
    verifications: list[RequirementVerification] = []

    for requirement in requirements:
        rule_matches = rule_match_by_requirement.get(requirement.id, [])
        rule_direct_ids = sorted({match.client_file_id for match in rule_matches if match.match_kind == "direct"})
        rule_probable_ids = sorted({match.client_file_id for match in rule_matches if match.match_kind == "probable"})

        agent_b_item = agent_b_by_requirement.get(requirement.id)
        agent_b_direct_ids = sorted(set(agent_b_item.matched_file_ids if agent_b_item else []))
        agent_b_probable_ids = sorted(set(agent_b_item.probable_file_ids if agent_b_item else []))

        matched_ids = sorted(set(rule_direct_ids) | set(agent_b_direct_ids))
        probable_ids = sorted((set(rule_probable_ids) | set(agent_b_probable_ids)) - set(matched_ids))
        candidate_ids = sorted(set(matched_ids) | set(probable_ids))

        if requirement.priority == RequirementPriority.exempt:
            status = VerificationStatus.matched
            notes = "招标清单标记为免提供"
        elif matched_ids:
            status = VerificationStatus.matched
            notes = "已命中客户材料"
        elif probable_ids and requirement_is_conditional(requirement):
            status = VerificationStatus.conditional_match
            notes = "存在候选文件，且该 requirement 具有条件性"
        elif probable_ids:
            status = VerificationStatus.probable_match
            notes = "存在候选文件，需人工确认"
        elif requirement_is_conditional(requirement):
            status = VerificationStatus.conditional_match
            notes = "条件材料未直接命中，需结合招标上下文人工确认"
        elif requirement.priority == RequirementPriority.optional:
            status = VerificationStatus.manual_review
            notes = "可选材料未直接命中"
        else:
            status = VerificationStatus.missing
            notes = "未找到满足 requirement 的客户文件"

        info: list[str] = []
        for file_id in matched_ids + probable_ids:
            profile = profiles_by_id.get(file_id)
            if profile is None:
                continue
            for item in _profile_key_information(profile):
                if item not in info:
                    info.append(item)

        match_source = match_outcome.match_source_by_requirement.get(requirement.id)
        if agent_b_item and (agent_b_item.matched_file_ids or agent_b_item.probable_file_ids):
            match_source = "program_merge" if match_source else "agent_b_only"

        llm_note = agent_b_item.notes if agent_b_item else ""
        if llm_global_notes and not llm_note:
            llm_note = llm_global_notes[:500]

        verifications.append(
            RequirementVerification(
                requirement_id=requirement.id,
                ordinal=requirement.ordinal,
                category=requirement.category,
                name=requirement.name,
                tender_status=requirement.tender_status,
                remark=requirement.remark,
                priority=requirement.priority,
                status=status,
                matched_file_ids=matched_ids,
                probable_file_ids=probable_ids,
                candidate_file_ids=candidate_ids,
                notes=notes,
                match_source=match_source,
                key_information=info,
                llm_comment=llm_note,
                llm_confidence=agent_b_item.confidence if agent_b_item else None,
                llm_reviewed=agent_b_item is not None,
                conditional=status == VerificationStatus.conditional_match,
                condition_note="条件满足/条件核验" if status == VerificationStatus.conditional_match else "",
            )
        )

    verifications = apply_substitution_engine(requirements, verifications, profiles_by_id)

    fixed: list[RequirementVerification] = []
    for verification in verifications:
        if verification.status == VerificationStatus.matched and not (
            verification.candidate_file_ids or verification.substitution_source_file_ids
        ):
            verification = verification.model_copy(
                update={
                    "status": VerificationStatus.manual_review,
                    "notes": (verification.notes + "；缺少 candidate_file_ids，已降级为人工复核").strip("；"),
                }
            )
        fixed.append(verification)
    return fixed


def _build_coverage_snapshot(
    clients: list[ClientFile],
    verifications: list[RequirementVerification],
    agent_b_unused_file_ids: list[str],
) -> Phase2CoverageSnapshot:
    all_file_ids = [client.id for client in clients]
    used_file_ids: set[str] = set()
    requirement_file_map: dict[str, list[str]] = {}
    unmatched_requirement_ids: list[str] = []

    for verification in verifications:
        file_ids = list(
            dict.fromkeys(
                verification.matched_file_ids
                + verification.probable_file_ids
                + verification.substitution_source_file_ids
            )
        )
        requirement_file_map[verification.requirement_id] = file_ids
        used_file_ids.update(file_ids)
        if verification.status in {VerificationStatus.missing, VerificationStatus.manual_review, VerificationStatus.probable_match}:
            unmatched_requirement_ids.append(verification.requirement_id)

    program_unused = sorted(set(all_file_ids) - used_file_ids)
    return Phase2CoverageSnapshot(
        all_client_file_ids=all_file_ids,
        used_file_ids=sorted(used_file_ids),
        unused_file_ids=program_unused,
        requirement_file_map=requirement_file_map,
        agent_b_unused_file_ids=sorted(agent_b_unused_file_ids),
        program_unused_file_ids=program_unused,
        unmatched_requirement_ids=unmatched_requirement_ids,
    )


def run_phase2_verify(
    baseline_path: str | Path | Sequence[str | Path],
    client_source: str | Path | None,
    out_dir: str | Path,
    *,
    client_paths: list[Path] | None = None,
    ocr_fallback: bool = True,
    progress: Callable[[str], None] | None = None,
    llm: LLMRuntimeConfig | None = None,
    llm_assist: bool = False,
    llm_apply: bool = False,
    llm_min_confidence: float = 0.85,
    llm_dual_agent: bool = True,
    llm_file_read: bool = True,
    llm_file_read_batch_size: int = 4,
    include_unused_files: bool = True,
) -> dict:
    root = Path(out_dir).resolve()
    artifacts = Phase2Artifacts()
    originals = root / artifacts.originals_dir
    normalized = root / artifacts.normalized_dir
    previews = root / artifacts.previews_png_dir
    reports = root / artifacts.reports_dir
    for directory in (originals, normalized, previews, reports):
        directory.mkdir(parents=True, exist_ok=True)

    _log(progress, "加载招标基准清单")
    if isinstance(baseline_path, (str, Path)):
        baseline_paths = [Path(baseline_path)]
    else:
        baseline_paths = [Path(item) for item in baseline_path]
    requirements, baseline_note, chosen_baseline = resolve_and_load_baselines(baseline_paths)
    _log(progress, baseline_note)
    if not requirements:
        raise ValueError("基准清单为空，请检查输入文件")

    _log(progress, "接收并去重客户文件")
    clients: list[ClientFile] = []
    if client_source is not None:
        source_path = Path(client_source).resolve()
        if source_path.exists():
            clients.extend(ingest_path(source_path, originals))
    if client_paths:
        clients.extend(ingest_many_files([path.resolve() for path in client_paths], originals))
    clients = _dedupe_clients(clients)

    _log(progress, f"客户文件已进入系统视野: {len(clients)} 个")
    write_ingested_files_manifest(root, clients, reports / "ingested_files.json")

    _log(progress, "执行文本抽取与 OCR")
    hydrate_clients_text(clients, ocr_fallback=ocr_fallback)
    apply_classification(clients)

    _log(progress, "为每个客户文件构建 ClientDocumentProfile")
    file_profiles = build_file_profiles(clients)
    profiles_by_id = {profile.file_id: profile for profile in file_profiles}
    clients = [client.model_copy(update={"document_profile": profiles_by_id.get(client.id)}) for client in clients]

    llm_readings = {}
    if llm_file_read or llm_dual_agent:
        _log(progress, "Agent A 逐文件理解")
        requirement_brief = "\n".join(f"{req.id} {req.name}" for req in requirements)
        llm_readings = run_llm_file_reader(
            file_profiles,
            llm,
            requirements_brief=requirement_brief,
            batch_max_files=llm_file_read_batch_size,
            progress=progress,
        )
        file_profiles = merge_llm_file_reading_into_profiles(file_profiles, llm_readings)
        profiles_by_id = {profile.file_id: profile for profile in file_profiles}
        clients = [client.model_copy(update={"document_profile": profiles_by_id.get(client.id)}) for client in clients]

    _log(progress, "规则匹配与候选 requirement 生成")
    rule_matches = match_files_to_requirements(requirements, clients)
    rule_matches = apply_substitution_matches(requirements, clients, rule_matches)
    profile_match_outcome = match_profiles_to_requirements(
        requirements,
        file_profiles,
        rule_matches,
        llm_readings,
        all_file_ids={client.id for client in clients},
        requirement_name_by_id={req.id: req.name for req in requirements},
    )

    _log(progress, "Agent B 按 requirement 核验")
    agent_b_out = run_requirement_verifier(
        requirements,
        profiles_by_id,
        {client.id for client in clients},
        llm,
        progress=progress,
    )

    _log(progress, "程序层合并最终状态、替代关系与条件逻辑")
    verifications = _merge_requirement_results(
        requirements,
        profile_match_outcome,
        agent_b_out,
        profiles_by_id,
        llm_global_notes=agent_b_out.global_notes,
    )

    clients_by_id = {client.id: client for client in clients}
    if llm_assist and llm is not None:
        _log(progress, "执行灰区辅助审查")
        verifications = run_llm_assist_review(
            verifications,
            clients_by_id,
            llm,
            apply_suggestions=llm_apply,
            min_confidence=llm_min_confidence,
            progress=progress,
        )

    file_usage, unused_file_ids = rebuild_file_usage_from_verifications(
        {client.id for client in clients},
        file_profiles,
        verifications,
        {req.id: req.name for req in requirements},
    )

    _log(progress, "生成归档目录与 PNG 预览")
    archive_index = archive_verification_results(
        root,
        requirements,
        clients_by_id,
        verifications,
        normalized_root=normalized,
        previews_root=previews,
    )
    archive_index = archive_unused_client_files(
        root,
        unused_file_ids,
        clients_by_id,
        normalized_root=normalized,
        previews_root=previews,
        index=archive_index,
    )

    _log(progress, "写出文本侧车、回填清单表和报告")
    extracts_dir = reports / "extracts"
    write_extract_sidecars(root, clients, extracts_dir=extracts_dir)
    for client in clients:
        entry = archive_index.get(client.id)
        if entry is not None:
            entry.text_extract_path = f"{artifacts.reports_dir}/extracts/{client.id}_{client.original_filename}.txt"

    coverage = _build_coverage_snapshot(clients, verifications, agent_b_out.unused_file_ids)
    write_coverage_snapshot_json(coverage, reports / "materials_coverage.json")
    if include_unused_files:
        write_unused_and_unmatched_md(coverage, clients_by_id, reports / "unused_and_unmatched.md")
    write_verification_xlsx(
        verifications,
        reports / "核验结果总表.xlsx",
        coverage=coverage,
        clients_by_id=clients_by_id,
        file_usage=file_usage,
        include_unused_files=include_unused_files,
    )
    write_missing_md(
        verifications,
        reports / "缺失材料清单.md",
        file_usage=file_usage,
        clients_by_id=clients_by_id,
        include_unused_files=include_unused_files,
    )
    write_archive_index_json(archive_index, reports / "archive_index.json")
    write_verification_json(verifications, reports / "verification.json")
    write_file_usage_json(file_usage, reports / "file_usage.json")

    baseline_xlsx = chosen_baseline if chosen_baseline.suffix.lower() in {".xlsx", ".xlsm"} else None
    write_filled_materials_checklist(
        baseline_xlsx=baseline_xlsx,
        requirements=requirements,
        verifications=verifications,
        clients_by_id=clients_by_id,
        archive_index=archive_index,
        out_path=reports / "投标材料清单表_阶段二回填.xlsx",
    )

    _log(progress, f"阶段二完成: {root}")
    return {
        "out_dir": str(root),
        "requirements": len(requirements),
        "client_files": len(clients),
        "matched_requirements": len([item for item in verifications if item.status == VerificationStatus.matched]),
        "unused_files": unused_file_ids,
        "requirement_file_map": coverage.requirement_file_map,
        "artifacts": artifacts.model_dump(),
    }
