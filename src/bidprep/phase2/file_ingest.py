from __future__ import annotations

import hashlib
import shutil
import zipfile
from pathlib import Path

from bidprep.phase2.schemas import ClientFile

_ALLOWED_EXT = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}
_TEXT_EXT_HINT = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def _sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _unique_dest_name(originals_dir: Path, base: str, ext: str, digest: str) -> Path:
    short = digest[:12]
    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in base)[:120]
    name = f"{short}__{safe}{ext}"
    p = originals_dir / name
    n = 0
    while p.exists():
        n += 1
        p = originals_dir / f"{short}__{safe}_{n}{ext}"
    return p


def ingest_path(
    source: Path,
    originals_dir: Path,
    *,
    extract_zip: bool = True,
) -> list[ClientFile]:
    """
    将 source（文件、目录或 zip）展开并复制到 originals_dir，返回 ClientFile 列表。
    原件以「hash__原名」形式落盘，避免重名覆盖。
    """
    originals_dir.mkdir(parents=True, exist_ok=True)
    roots: list[Path] = []
    if source.is_file():
        low = source.suffix.lower()
        if low == ".zip" and extract_zip:
            staging = originals_dir.parent / "_staging_extract"
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=True)
            try:
                with zipfile.ZipFile(source, "r") as zf:
                    zf.extractall(staging)
                roots.append(staging)
            finally:
                pass  # 在收集完文件后再删 staging，见下方
        else:
            return _copy_and_build([(source.resolve(), source.name)], originals_dir)
    elif source.is_dir():
        roots.append(source.resolve())
    else:
        raise FileNotFoundError(str(source))

    collected: list[tuple[Path, str]] = []
    staging_to_clean: Path | None = None
    if roots and roots[0].name == "_staging_extract":
        staging_to_clean = roots[0]

    try:
        for root in roots:
            if root.is_file():
                continue
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                ext = p.suffix.lower()
                if ext not in _ALLOWED_EXT:
                    continue
                try:
                    rel = p.relative_to(root).as_posix()
                except ValueError:
                    rel = p.name
                collected.append((p, rel))

        if not collected and source.is_dir():
            return []

        return _copy_and_build(collected, originals_dir)
    finally:
        if staging_to_clean and staging_to_clean.exists():
            shutil.rmtree(staging_to_clean, ignore_errors=True)


def _copy_and_build(pairs: list[tuple[Path, str]], originals_dir: Path) -> list[ClientFile]:
    originals_dir.mkdir(parents=True, exist_ok=True)
    out: list[ClientFile] = []
    for src, rel in pairs:
        ext = src.suffix.lower()
        if ext not in _ALLOWED_EXT:
            continue
        try:
            if not src.is_file() or src.stat().st_size == 0:
                continue
        except OSError:
            continue
        digest = _sha256_file(src)
        dest = _unique_dest_name(originals_dir, src.stem, ext, digest)
        shutil.copy2(src, dest)
        cid = digest[:16]
        out.append(
            ClientFile(
                id=cid,
                original_filename=src.name,
                rel_path=rel,
                abs_path=str(dest.resolve()),
                sha256=digest,
                extension=ext,
                mime_hint=_TEXT_EXT_HINT.get(ext, ""),
            )
        )
    # 稳定顺序：按原名
    out.sort(key=lambda c: (c.rel_path or c.original_filename).lower())
    return out


def ingest_many_files(paths: list[Path], originals_dir: Path) -> list[ClientFile]:
    """将多个已上传文件复制到 originals_dir。"""
    pairs = [(p.resolve(), p.name) for p in paths if p.is_file()]
    return _copy_and_build(pairs, originals_dir)
