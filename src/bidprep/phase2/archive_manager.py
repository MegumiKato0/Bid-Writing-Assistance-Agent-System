from __future__ import annotations

import json
import shutil
from pathlib import Path

from bidprep.phase2.png_renderer import (
    _safe_segment,
    default_selected_pages,
    preview_basename_for_requirement,
    render_client_file_pngs,
    should_render_png_for_requirement,
)
from bidprep.phase2.schemas import ArchiveIndexEntry, ClientFile, Requirement, RequirementVerification

ARCHIVE_BUCKETS = {
    "qualification": "01_资格证明文件",
    "personnel": "02_人员证件",
    "quotation": "03_报价响应文件",
    "technical": "04_技术方案",
    "business": "05_商务资料",
    "unknown": "99_未识别材料",
}


def normalized_bucket_for_requirement(requirement: Requirement) -> str:
    text = f"{requirement.category} {requirement.name}"
    if any(token in text for token in ("资格", "资质", "营业执照", "许可证", "证明")):
        return ARCHIVE_BUCKETS["qualification"]
    if any(token in text for token in ("人员", "身份证", "法定代表人", "授权", "委托", "签字")):
        return ARCHIVE_BUCKETS["personnel"]
    if any(token in text for token in ("报价", "响应", "开标", "投标报价")):
        return ARCHIVE_BUCKETS["quotation"]
    if any(token in text for token in ("技术", "方案")):
        return ARCHIVE_BUCKETS["technical"]
    if any(token in text for token in ("商务", "承诺", "声明", "业绩")):
        return ARCHIVE_BUCKETS["business"]
    return ARCHIVE_BUCKETS["unknown"]


def _dest_filename(requirement: Requirement, client: ClientFile) -> str:
    source_abbr = _safe_segment(Path(client.original_filename).stem, 24)
    ext = Path(client.original_filename).suffix.lower() or f".{client.extension.lstrip('.')}"
    return f"{requirement.ordinal:02d}_{_safe_segment(requirement.name, 48)}_{source_abbr}{ext}"


def _ensure_entry(job_root: Path, client: ClientFile, index: dict[str, ArchiveIndexEntry]) -> ArchiveIndexEntry:
    if client.id not in index:
        original_rel = Path(client.abs_path).relative_to(job_root).as_posix()
        index[client.id] = ArchiveIndexEntry(
            original_filename=client.original_filename,
            client_file_id=client.id,
            sha256=client.sha256,
            original_copy_path=original_rel,
        )
    return index[client.id]


def archive_verification_results(
    job_root: Path,
    requirements: list[Requirement],
    clients_by_id: dict[str, ClientFile],
    verifications: list[RequirementVerification],
    *,
    normalized_root: Path | None = None,
    previews_root: Path | None = None,
) -> dict[str, ArchiveIndexEntry]:
    normalized_root = normalized_root or (job_root / "normalized")
    previews_root = previews_root or (job_root / "previews_png")
    normalized_root.mkdir(parents=True, exist_ok=True)
    previews_root.mkdir(parents=True, exist_ok=True)

    requirement_by_id = {requirement.id: requirement for requirement in requirements}
    index: dict[str, ArchiveIndexEntry] = {}

    for verification in verifications:
        requirement = requirement_by_id[verification.requirement_id]
        matched_ids = list(dict.fromkeys(
            verification.matched_file_ids + verification.substitution_source_file_ids + verification.probable_file_ids
        ))
        if not matched_ids and verification.status.value == "matched":
            continue

        archive_paths: list[str] = []
        archive_names: list[str] = []
        png_paths: list[str] = []
        for file_id in matched_ids:
            client = clients_by_id.get(file_id)
            if client is None:
                continue
            entry = _ensure_entry(job_root, client, index)
            if requirement.id not in entry.requirement_ids:
                entry.requirement_ids.append(requirement.id)
            if requirement.name not in entry.requirement_names:
                entry.requirement_names.append(requirement.name)

            bucket = normalized_bucket_for_requirement(requirement)
            bucket_dir = normalized_root / bucket
            bucket_dir.mkdir(parents=True, exist_ok=True)

            dest_name = _dest_filename(requirement, client)
            dest_path = bucket_dir / dest_name
            if not dest_path.exists():
                shutil.copy2(Path(client.abs_path), dest_path)

            dest_rel = dest_path.relative_to(job_root).as_posix()
            if dest_rel not in entry.normalized_paths:
                entry.normalized_paths.append(dest_rel)
            if dest_name not in entry.standardized_filenames:
                entry.standardized_filenames.append(dest_name)

            archive_paths.append(dest_rel)
            archive_names.append(dest_name)

            profile = client.document_profile
            if should_render_png_for_requirement(requirement, profile):
                basename = preview_basename_for_requirement(requirement.ordinal, requirement.name, Path(client.original_filename).stem)
                selected_pages = default_selected_pages(client, profile, render_all=Path(client.abs_path).suffix.lower() == ".pdf")
                for png_path in render_client_file_pngs(
                    client,
                    previews_root,
                    basename,
                    selected_pages=selected_pages,
                    render_all=Path(client.abs_path).suffix.lower() == ".pdf",
                ):
                    png_rel = png_path.relative_to(job_root).as_posix()
                    if png_rel not in entry.preview_png_paths:
                        entry.preview_png_paths.append(png_rel)
                    png_paths.append(png_rel)

        verification.archive_file_paths = archive_paths
        verification.archive_file_names = archive_names
        verification.png_paths = list(dict.fromkeys(png_paths))

    return index


def archive_unused_client_files(
    job_root: Path,
    unused_file_ids: list[str],
    clients_by_id: dict[str, ClientFile],
    *,
    normalized_root: Path | None = None,
    previews_root: Path | None = None,
    index: dict[str, ArchiveIndexEntry] | None = None,
) -> dict[str, ArchiveIndexEntry]:
    normalized_root = normalized_root or (job_root / "normalized")
    previews_root = previews_root or (job_root / "previews_png")
    normalized_root.mkdir(parents=True, exist_ok=True)
    previews_root.mkdir(parents=True, exist_ok=True)
    index = index or {}

    bucket_dir = normalized_root / ARCHIVE_BUCKETS["unknown"]
    bucket_dir.mkdir(parents=True, exist_ok=True)

    for file_id in unused_file_ids:
        client = clients_by_id.get(file_id)
        if client is None:
            continue
        entry = _ensure_entry(job_root, client, index)
        source_abbr = _safe_segment(Path(client.original_filename).stem, 24)
        dest_name = f"99_未识别材料_{source_abbr}{Path(client.original_filename).suffix.lower()}"
        dest_path = bucket_dir / dest_name
        if not dest_path.exists():
            shutil.copy2(Path(client.abs_path), dest_path)
        dest_rel = dest_path.relative_to(job_root).as_posix()
        if dest_rel not in entry.normalized_paths:
            entry.normalized_paths.append(dest_rel)
        if dest_name not in entry.standardized_filenames:
            entry.standardized_filenames.append(dest_name)
    return index


def write_ingested_files_manifest(job_root: Path, clients: list[ClientFile], path: Path | None = None) -> Path:
    out_path = path or (job_root / "reports" / "ingested_files.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "client_file_id": client.id,
            "original_filename": client.original_filename,
            "rel_path": client.rel_path,
            "sha256": client.sha256,
            "extension": client.extension,
        }
        for client in clients
    ]
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def write_extract_sidecars(job_root: Path, clients: list[ClientFile], extracts_dir: Path | None = None) -> None:
    root = extracts_dir or (job_root / "reports" / "extracts")
    root.mkdir(parents=True, exist_ok=True)
    for client in clients:
        name = f"{client.id}_{_safe_segment(client.original_filename, 24)}.txt"
        (root / name).write_text(client.extracted_text or "", encoding="utf-8")
