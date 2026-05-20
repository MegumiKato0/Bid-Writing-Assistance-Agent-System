from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Callable

from bidprep.llm_runtime import LLMRuntimeConfig
from bidprep.phase2.file_profile_schemas import ClientDocumentProfile, FileProfileBatch
from bidprep.phase2.llm_file_reader import run_agent_a_file_profiles
from bidprep.phase2.schemas import ClientFile

_MIN_PARSED_CHARS = 48
_KEYWORD_TEXT_LIMIT = 12000
_EXCERPT_HEAD = 2400
_EXCERPT_TAIL = 1200

_DOC_TYPE_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("身份证", ("身份证", "居民身份证")),
    ("营业执照", ("营业执照",)),
    ("执业许可证", ("执业许可证", "许可证")),
    ("授权书", ("授权书", "委托书", "授权委托")),
    ("声明函", ("声明函",)),
    ("承诺函", ("承诺函", "资格承诺", "信用承诺")),
    ("盖章页", ("盖章", "公章")),
    ("签字页", ("签字", "签署", "签章")),
    ("证书类", ("证书", "资格证", "等级证")),
]


def merge_text_sources(parsed_text: str, ocr_text: str) -> tuple[str, str, str]:
    parsed = (parsed_text or "").strip()
    ocr = (ocr_text or "").strip()
    if len(parsed) >= _MIN_PARSED_CHARS:
        effective = parsed
    elif ocr:
        effective = ocr
    else:
        effective = parsed
    return parsed, ocr, effective


def build_text_excerpt(effective_text: str, *, head: int = _EXCERPT_HEAD, tail: int = _EXCERPT_TAIL) -> str:
    text = (effective_text or "").strip()
    if not text:
        return ""
    if len(text) <= head + tail + 24:
        return text
    return text[: head // 2] + "\n...(omitted)...\n" + text[-tail // 2 :]


def _normalize_filename(name: str) -> str | None:
    stem = Path(name or "").stem.strip().lower()
    return stem or None


def _file_size_bytes(abs_path: str) -> int | None:
    try:
        path = Path(abs_path)
        return int(path.stat().st_size) if path.is_file() else None
    except OSError:
        return None


def _guess_company_name(text: str) -> str | None:
    if not text:
        return None
    match = re.search(
        r"([\u4e00-\u9fffA-Za-z0-9()（）·]{4,80}?(?:有限公司|股份有限公司|有限责任公司|集团|事务所))",
        text[:8000],
    )
    return match.group(1).strip() if match else None


def _guess_person_names(text: str) -> list[str]:
    found = re.findall(r"([\u4e00-\u9fff·]{2,8})(?:先生|女士)", text[:6000])
    out: list[str] = []
    seen: set[str] = set()
    for item in found:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out[:12]


def _guess_document_date(text: str) -> str | None:
    for pattern in (
        r"(20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)",
        r"(20\d{2}\.\d{1,2}\.\d{1,2})",
    ):
        match = re.search(pattern, text[:4000])
        if match:
            return match.group(1).replace(" ", "")
    return None


def extract_profile_keywords(effective_text: str, original_name: str, *, top_n: int = 24) -> list[str]:
    name_tokens = [token.strip() for token in re.split(r"[\s_.\-]+", original_name or "") if len(token.strip()) >= 2]
    text = (effective_text or "")[:_KEYWORD_TEXT_LIMIT]
    zh_chunks = re.findall(r"[\u4e00-\u9fff]{3,8}", text)
    en_chunks = re.findall(r"[A-Za-z]{4,}", text.lower())

    ordered: list[str] = list(name_tokens)
    for word, _ in Counter(zh_chunks).most_common(top_n):
        ordered.append(word)
    for word, _ in Counter(en_chunks).most_common(8):
        ordered.append(word)

    out: list[str] = []
    seen: set[str] = set()
    for item in ordered:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= top_n:
            break
    return out


def _detect_material_types(original_name: str, effective_text: str) -> list[str]:
    haystack = f"{original_name}\n{effective_text[:8000]}"
    out: list[str] = []
    for label, patterns in _DOC_TYPE_PATTERNS:
        if any(pattern in haystack for pattern in patterns):
            out.append(label)
    return out


def _build_key_information(
    company_name: str | None,
    person_names: list[str],
    document_date: str | None,
    keywords: list[str],
) -> list[str]:
    info: list[str] = []
    if company_name:
        info.append(f"公司名称: {company_name}")
    if person_names:
        info.append("人员姓名: " + "、".join(person_names[:6]))
    if document_date:
        info.append(f"文档日期: {document_date}")
    if keywords:
        info.append("关键词: " + "、".join(keywords[:10]))
    return info


def _source_abbr(original_filename: str) -> str:
    normalized = _normalize_filename(original_filename) or "file"
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", normalized).strip("_")
    return cleaned[:24] or "file"


def build_file_profile(
    client: ClientFile,
    *,
    parsed_text: str | None = None,
    ocr_text: str | None = None,
) -> ClientDocumentProfile:
    if parsed_text is None and ocr_text is None:
        extracted = (client.extracted_text or "").strip()
        parsed_raw = "" if client.ocr_used else extracted
        ocr_raw = extracted if client.ocr_used else ""
    else:
        parsed_raw = (parsed_text or "").strip()
        ocr_raw = (ocr_text or "").strip()

    parsed, ocr, effective = merge_text_sources(parsed_raw, ocr_raw)
    excerpt = build_text_excerpt(effective)
    keywords = extract_profile_keywords(effective, client.original_filename)
    company_name = _guess_company_name(effective)
    person_names = _guess_person_names(effective)
    document_date = _guess_document_date(effective)
    material_types = _detect_material_types(client.original_filename, effective)
    source_abbr = _source_abbr(client.original_filename)

    return ClientDocumentProfile(
        file_id=client.id,
        original_name=client.original_filename,
        rel_path=client.rel_path,
        file_path=client.abs_path,
        ext=(client.extension or Path(client.original_filename).suffix).lstrip(".").lower(),
        mime_type=(client.mime_hint or "").strip() or None,
        pages=client.page_count if client.page_count > 0 else None,
        file_size=_file_size_bytes(client.abs_path),
        hash=(client.sha256 or "").strip() or None,
        parsed_text=parsed,
        ocr_text=ocr,
        effective_text=effective,
        text_excerpt=excerpt,
        coarse_category=(client.coarse_category or "").strip() or None,
        doc_category=(client.coarse_category or "").strip() or None,
        detected_material_types=material_types,
        keywords=keywords,
        company_name=company_name,
        person_names=person_names,
        document_date=document_date,
        key_information=_build_key_information(company_name, person_names, document_date, keywords),
        llm_reviewed=False,
        source_abbr=source_abbr,
        should_render_png=bool(material_types),
    )


def build_file_profiles(
    clients: list[ClientFile],
    *,
    parsed_by_id: dict[str, str] | None = None,
    ocr_by_id: dict[str, str] | None = None,
) -> list[ClientDocumentProfile]:
    parsed_map = parsed_by_id or {}
    ocr_map = ocr_by_id or {}
    return [
        build_file_profile(
            client,
            parsed_text=parsed_map.get(client.id),
            ocr_text=ocr_map.get(client.id),
        )
        for client in clients
    ]


def as_profile_batch(
    clients: list[ClientFile],
    *,
    parsed_by_id: dict[str, str] | None = None,
    ocr_by_id: dict[str, str] | None = None,
) -> FileProfileBatch:
    return FileProfileBatch(files=build_file_profiles(clients, parsed_by_id=parsed_by_id, ocr_by_id=ocr_by_id))


def hydrate_client_file_profiles(
    clients: list[ClientFile],
    llm: LLMRuntimeConfig,
    *,
    progress: Callable[[str], None] | None = None,
) -> list[ClientFile]:
    if not clients:
        return clients
    profiles = run_agent_a_file_profiles(clients, llm, progress=progress)
    return [client.model_copy(update={"file_profile": profiles.get(client.id)}) for client in clients]
