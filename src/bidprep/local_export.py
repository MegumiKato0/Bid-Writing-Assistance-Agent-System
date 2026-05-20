from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from bidprep.config import settings


def _find_project_root() -> Path:
    path = Path(__file__).resolve().parent
    for _ in range(10):
        if (path / "pyproject.toml").is_file():
            return path
        if path.parent == path:
            break
        path = path.parent
    return Path.cwd()


def _default_export_root_phase1() -> Path:
    return _find_project_root() / "exports" / "phase1"


def _default_export_root_phase2() -> Path:
    return _find_project_root() / "exports2"


def _safe_dir_name(text: str) -> str:
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", (text or "").strip()).strip("_")
    return value[:80] or "job"


def _choose_subdir_name(
    *,
    source_name: str | None = None,
    job_id: str | None = None,
    suffix_timestamp: bool = False,
) -> str:
    if source_name:
        stem = _safe_dir_name(Path(source_name).stem)
        if job_id:
            return f"{stem}_{job_id[:8]}"
        if suffix_timestamp:
            return f"{stem}_{int(time.time())}"
        return stem
    if job_id:
        return job_id
    return f"job_{int(time.time())}"


def _copy_out_dir(out_dir: Path, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(out_dir, dest)
    return dest.resolve()


def try_auto_export_phase1(
    out_dir: Path,
    *,
    input_pdf: Path | None = None,
    job_id: str | None = None,
) -> Path | None:
    if not settings.phase1_auto_export:
        return None
    out_dir = out_dir.resolve()
    if not out_dir.is_dir():
        return None

    custom = (settings.phase1_auto_export_dir or "").strip()
    base = Path(custom).expanduser().resolve() if custom else _default_export_root_phase1()
    base.mkdir(parents=True, exist_ok=True)

    dest = base / _choose_subdir_name(source_name=input_pdf.name if input_pdf else None, job_id=job_id, suffix_timestamp=not bool(job_id))
    _copy_out_dir(out_dir, dest)
    if input_pdf and input_pdf.is_file():
        shutil.copy2(input_pdf.resolve(), dest / "input.pdf")
    return dest.resolve()


def try_auto_export_phase2(
    out_dir: Path,
    *,
    baseline_name: str | None = None,
    job_id: str | None = None,
) -> Path | None:
    if not settings.phase2_auto_export:
        return None
    out_dir = out_dir.resolve()
    if not out_dir.is_dir():
        return None

    custom = (settings.phase2_auto_export_dir or "").strip()
    base = Path(custom).expanduser().resolve() if custom else _default_export_root_phase2()
    base.mkdir(parents=True, exist_ok=True)

    dest = base / _choose_subdir_name(source_name=baseline_name, job_id=job_id, suffix_timestamp=not bool(job_id))
    return _copy_out_dir(out_dir, dest)
