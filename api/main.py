from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

# 未执行 pip install -e . 时，保证能从源码目录加载 bidprep（uvicorn --reload 子进程同样生效）
_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from bidprep.llm_runtime import fetch_ollama_model_names, llm_config_from_frontend
from bidprep.local_export import try_auto_export_phase1, try_auto_export_phase2
from bidprep.pipeline import run_pipeline
from bidprep.phase2.pipeline import run_phase2_verify

app = FastAPI(title="BidPrep", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 输出文件名（与 pipeline 一致）→ URL 键，避免路径遍历
JOB_FILE_KEYS: dict[str, tuple[str, str]] = {
    "checklist_md": ("投标材料准备清单.md", "text/markdown; charset=utf-8"),
    "checklist_pdf": ("投标材料准备清单.pdf", "application/pdf"),
    "materials_xlsx": ("投标材料清单表.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "materials_pdf": ("投标材料清单表.pdf", "application/pdf"),
    "fill_xlsx": ("需要填空的内容项表.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "fill_pdf": ("需要填空的内容项表.pdf", "application/pdf"),
    "meta_json": ("extraction_meta.json", "application/json; charset=utf-8"),
}

JOBS_ROOT = Path(tempfile.gettempdir()) / "bidprep_jobs"
PHASE2_JOBS_ROOT = Path(tempfile.gettempdir()) / "bidprep_phase2_jobs"
JOB_MAX_AGE_SEC = 24 * 3600

PHASE2_FILE_KEYS: dict[str, tuple[str, str]] = {
    "verification_xlsx": ("reports/核验结果总表.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "checklist_filled_xlsx": (
        "reports/投标材料清单表_阶段二回填.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    "ingested_files_json": ("reports/ingested_files.json", "application/json; charset=utf-8"),
    "missing_md": ("reports/缺失材料清单.md", "text/markdown; charset=utf-8"),
    "archive_json": ("reports/归档索引.json", "application/json; charset=utf-8"),
    "verification_json": ("reports/核验明细.json", "application/json; charset=utf-8"),
    "coverage_json": ("reports/材料覆盖与未使用文件.json", "application/json; charset=utf-8"),
    "unused_md": ("reports/未使用与未匹配文件.md", "text/markdown; charset=utf-8"),
    "file_usage_json": ("reports/文件使用情况.json", "application/json; charset=utf-8"),
}

PHASE2_BUNDLE_KEYS: dict[str, tuple[str, str]] = {
    "originals_zip": ("originals", "客户原件工作副本.zip"),
    "normalized_zip": ("normalized", "归档目录.zip"),
    "previews_zip": ("previews_png", "PNG预览.zip"),
    "reports_zip": ("reports", "核验报告全集.zip"),
}


@dataclass
class JobRecord:
    out_dir: Path
    created: float


_JOBS: dict[str, JobRecord] = {}
_PHASE2_JOBS: dict[str, JobRecord] = {}


def _cleanup_old_jobs() -> None:
    now = time.time()
    dead: list[str] = []
    for jid, rec in _JOBS.items():
        if now - rec.created > JOB_MAX_AGE_SEC:
            dead.append(jid)
    for jid in dead:
        rec = _JOBS.pop(jid, None)
        if rec and rec.out_dir.parent.is_dir():
            shutil.rmtree(rec.out_dir.parent, ignore_errors=True)
    dead2: list[str] = []
    for jid, rec in _PHASE2_JOBS.items():
        if now - rec.created > JOB_MAX_AGE_SEC:
            dead2.append(jid)
    for jid in dead2:
        rec = _PHASE2_JOBS.pop(jid, None)
        if rec and rec.out_dir.parent.is_dir():
            shutil.rmtree(rec.out_dir.parent, ignore_errors=True)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/llm/ollama/models")
def ollama_models(host: str = "http://127.0.0.1:11434") -> dict:
    """列出本机 Ollama 已安装模型（需 Ollama 服务已启动）。"""
    models, err = fetch_ollama_model_names(host)
    return {"models": models, "error": err}


def _build_download_response(job_id: str, local_saved_path: str | None = None) -> dict:
    """供 POST 返回：按类别分组的下载路径（相对当前站点）。"""
    base = f"/api/jobs/{job_id}"
    out: dict = {
        "job_id": job_id,
        "expires_hint_hours": int(JOB_MAX_AGE_SEC / 3600),
        "categories": [
            {
                "id": "checklist",
                "title": "投标材料准备清单",
                "items": [
                    {"key": "checklist_md", "label": "Markdown", "suffix": ".md", "href": f"{base}/checklist_md"},
                    {"key": "checklist_pdf", "label": "PDF", "suffix": ".pdf", "href": f"{base}/checklist_pdf"},
                ],
            },
            {
                "id": "materials",
                "title": "投标材料清单表",
                "items": [
                    {"key": "materials_xlsx", "label": "Excel", "suffix": ".xlsx", "href": f"{base}/materials_xlsx"},
                    {"key": "materials_pdf", "label": "PDF", "suffix": ".pdf", "href": f"{base}/materials_pdf"},
                ],
            },
            {
                "id": "fill",
                "title": "需要填空的内容项表",
                "items": [
                    {"key": "fill_xlsx", "label": "Excel", "suffix": ".xlsx", "href": f"{base}/fill_xlsx"},
                    {"key": "fill_pdf", "label": "PDF", "suffix": ".pdf", "href": f"{base}/fill_pdf"},
                ],
            },
            {
                "id": "meta",
                "title": "抽取元数据",
                "items": [
                    {"key": "meta_json", "label": "JSON（溯源与审查）", "suffix": ".json", "href": f"{base}/meta_json"},
                ],
            },
        ],
    }
    if local_saved_path:
        out["local_saved_path"] = local_saved_path
    return out


@app.post("/api/process")
async def process_pdf(
    file: UploadFile = File(...),
    llm_mode: str = Form("ollama"),
    ollama_host: str = Form("http://127.0.0.1:11434"),
    ollama_model: str = Form(""),
    openai_base_url: str = Form(""),
    openai_api_key: str = Form(""),
    openai_model: str = Form(""),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "请上传 PDF 文件")
    data = await file.read()
    if len(data) > 80 * 1024 * 1024:
        raise HTTPException(400, "文件过大（限制 80MB）")

    try:
        llm = llm_config_from_frontend(
            llm_mode,
            ollama_host,
            ollama_model,
            openai_base_url,
            openai_api_key,
            openai_model,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    _cleanup_old_jobs()
    job_id = uuid.uuid4().hex
    job_parent = JOBS_ROOT / job_id
    job_parent.mkdir(parents=True, exist_ok=True)
    pdf_path = job_parent / "input.pdf"
    out_dir = job_parent / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(data)

    def _run() -> None:
        run_pipeline(pdf_path, out_dir, skip_llm=False, llm=llm)

    try:
        await asyncio.to_thread(_run)
    except Exception as e:
        shutil.rmtree(job_parent, ignore_errors=True)
        raise HTTPException(502, f"处理失败：{e}") from e

    saved: str | None = None
    try:
        dest = try_auto_export_phase1(out_dir, input_pdf=pdf_path, job_id=job_id)
        if dest is not None:
            saved = str(dest)
    except OSError:
        # 不因落盘失败影响下载；仅不返回路径
        saved = None

    _JOBS[job_id] = JobRecord(out_dir=out_dir.resolve(), created=time.time())
    return JSONResponse(_build_download_response(job_id, local_saved_path=saved))


def _build_phase2_download_response(job_id: str, local_saved_path: str | None = None) -> dict:
    base = f"/api/phase2/jobs/{job_id}"
    out = {
        "job_id": job_id,
        "expires_hint_hours": int(JOB_MAX_AGE_SEC / 3600),
        "categories": [
            {
                "id": "reports",
                "title": "核验报表",
                "items": [
                    {"key": "verification_xlsx", "label": "核验结果总表 (Excel)", "suffix": ".xlsx", "href": f"{base}/verification_xlsx"},
                    {
                        "key": "checklist_filled_xlsx",
                        "label": "投标材料清单表·阶段二回填 (Excel)",
                        "suffix": ".xlsx",
                        "href": f"{base}/checklist_filled_xlsx",
                    },
                    {
                        "key": "ingested_files_json",
                        "label": "已入库客户文件清单 (JSON)",
                        "suffix": ".json",
                        "href": f"{base}/ingested_files_json",
                    },
                    {"key": "missing_md", "label": "缺失材料清单 (Markdown)", "suffix": ".md", "href": f"{base}/missing_md"},
                    {"key": "archive_json", "label": "归档索引 (JSON)", "suffix": ".json", "href": f"{base}/archive_json"},
                    {"key": "verification_json", "label": "核验明细 (JSON)", "suffix": ".json", "href": f"{base}/verification_json"},
                    {"key": "coverage_json", "label": "材料覆盖与未使用 (JSON)", "suffix": ".json", "href": f"{base}/coverage_json"},
                    {"key": "unused_md", "label": "未使用与未匹配 (Markdown)", "suffix": ".md", "href": f"{base}/unused_md"},
                    {"key": "file_usage_json", "label": "文件使用情况 (JSON)", "suffix": ".json", "href": f"{base}/file_usage_json"},
                ],
            },
            {
                "id": "archives",
                "title": "归档与预览",
                "items": [
                    {"key": "originals_zip", "label": "originals 原件目录", "suffix": ".zip", "href": f"{base}/bundle/originals_zip"},
                    {"key": "normalized_zip", "label": "normalized 归档目录", "suffix": ".zip", "href": f"{base}/bundle/normalized_zip"},
                    {"key": "previews_zip", "label": "previews_png 预览图", "suffix": ".zip", "href": f"{base}/bundle/previews_zip"},
                    {"key": "reports_zip", "label": "reports 全部报告", "suffix": ".zip", "href": f"{base}/bundle/reports_zip"},
                ],
            },
        ],
    }
    if local_saved_path:
        out["local_saved_path"] = local_saved_path
    return out


def _zip_directory(src_dir: Path, zip_path: Path) -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in src_dir.rglob("*"):
            if path.is_file():
                zf.write(path, arcname=path.relative_to(src_dir.parent))
    return zip_path


def _form_bool(v: object) -> bool:
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "on")


@app.post("/api/phase2/verify")
async def phase2_verify(
    baseline: Annotated[list[UploadFile], File(description="投标材料清单表或 baseline JSON，可多文件合并")],
    ocr_fallback: Annotated[str, Form()] = "true",
    clients_zip: Annotated[UploadFile | None, File()] = None,
    extra_files: Annotated[list[UploadFile] | None, File()] = None,
    llm_mode: Annotated[str, Form()] = "ollama",
    ollama_host: Annotated[str, Form()] = "http://127.0.0.1:11434",
    ollama_model: Annotated[str, Form()] = "",
    openai_base_url: Annotated[str, Form()] = "",
    openai_api_key: Annotated[str, Form()] = "",
    openai_model: Annotated[str, Form()] = "",
    llm_assist: Annotated[str, Form()] = "false",
    llm_apply: Annotated[str, Form()] = "false",
    llm_min_confidence: Annotated[str, Form()] = "0.85",
    llm_file_read: Annotated[str, Form()] = "true",
    llm_file_read_batch_size: Annotated[str, Form()] = "4",
    include_unused_files: Annotated[str, Form()] = "true",
    llm_dual_agent: Annotated[str, Form()] = "true",
):
    """
    multipart：baseline（必填）、客户文件、ocr_fallback；须配置与阶段一相同的 LLM 字段（阶段二固定双 Agent）。

    参数说明（均为表单字段，字符串 true/false 或数值）：
    - llm_assist：是否对灰区核验条目做 LLM 辅助（第三层）。
    - llm_apply：是否将灰区 satisfied 建议应用为已匹配（需 llm_assist）。
    - llm_min_confidence：应用建议的最低置信度 0～1。
    - llm_file_read / llm_dual_agent：兼容旧字段，服务端固定启用 Agent A + B，忽略为 false 的调用。
    - llm_file_read_batch_size：Agent A 每批最多文件数，默认 4，范围 1～32。
    - include_unused_files：是否输出「未使用文件」类报表（默认 true）；为 false 时跳过未使用专项 Markdown 与 Excel 子表。
    """
    baselines_in = [b for b in baseline if b.filename and str(b.filename).strip()]
    if not baselines_in:
        raise HTTPException(400, "请上传至少一个基准清单文件")
    _baseline_ok_suffix = (".xlsx", ".xlsm", ".json", ".pdf", ".md", ".markdown")
    for bf in baselines_in:
        bl_name = (bf.filename or "").lower()
        if not bl_name.endswith(_baseline_ok_suffix):
            raise HTTPException(
                400,
                f"基准清单仅支持 .json / .xlsx / .xlsm / .pdf / .md：{bf.filename}",
            )

    _cleanup_old_jobs()
    job_id = uuid.uuid4().hex
    job_parent = PHASE2_JOBS_ROOT / job_id
    job_parent.mkdir(parents=True, exist_ok=True)
    out_root = job_parent / "out"
    out_root.mkdir(parents=True, exist_ok=True)

    baseline_paths: list[Path] = []
    for i, bf in enumerate(baselines_in):
        assert bf.filename
        baseline_suffix = Path(bf.filename).suffix or ".xlsx"
        baseline_path = job_parent / f"baseline_{i}{baseline_suffix}"
        baseline_path.write_bytes(await bf.read())
        baseline_paths.append(baseline_path)

    client_src: Path | None = None
    extra_paths: list[Path] = []
    if clients_zip is not None and clients_zip.filename and str(clients_zip.filename).strip():
        zp = job_parent / "clients.zip"
        data = await clients_zip.read()
        if len(data) > 120 * 1024 * 1024:
            shutil.rmtree(job_parent, ignore_errors=True)
            raise HTTPException(400, "ZIP 过大（限制 120MB）")
        zp.write_bytes(data)
        client_src = zp

    upload_dir = job_parent / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    for uf in extra_files or []:
        if not uf.filename or not str(uf.filename).strip():
            continue
        raw = await uf.read()
        if len(raw) > 80 * 1024 * 1024:
            shutil.rmtree(job_parent, ignore_errors=True)
            raise HTTPException(400, f"文件过大：{uf.filename}")
        safe = Path(uf.filename).name
        p = upload_dir / safe
        p.write_bytes(raw)
        extra_paths.append(p)

    if client_src is None and not extra_paths:
        shutil.rmtree(job_parent, ignore_errors=True)
        raise HTTPException(400, "请上传 clients_zip 或至少一个客户文件（extra_files）")

    use_ocr = str(ocr_fallback).lower() in ("1", "true", "yes", "on")

    want_assist = _form_bool(llm_assist)
    # 阶段二固定双 Agent（Agent A + B），须始终构造 LLM；表单中的 llm_file_read / llm_dual_agent 仅作兼容
    want_llm = True
    llm = None
    if want_llm:
        try:
            llm = llm_config_from_frontend(
                llm_mode,
                ollama_host,
                ollama_model,
                openai_base_url,
                openai_api_key,
                openai_model,
            )
        except ValueError as e:
            shutil.rmtree(job_parent, ignore_errors=True)
            raise HTTPException(400, str(e)) from e

    try:
        min_conf = float((llm_min_confidence or "0.85").strip())
    except ValueError:
        min_conf = 0.85
    min_conf = max(0.0, min(1.0, min_conf))

    try:
        br = int((llm_file_read_batch_size or "4").strip())
    except ValueError:
        br = 4
    br = max(1, min(32, br))

    include_unused = _form_bool(include_unused_files)

    def _run() -> None:
        run_phase2_verify(
            baseline_paths,
            client_src,
            out_root,
            client_paths=extra_paths or None,
            ocr_fallback=use_ocr,
            progress=None,
            llm=llm,
            llm_assist=want_assist,
            llm_dual_agent=True,
            llm_file_read=True,
            llm_file_read_batch_size=br,
            include_unused_files=include_unused,
            llm_apply=_form_bool(llm_apply),
            llm_min_confidence=min_conf,
        )

    baseline_display_name = baselines_in[0].filename if baselines_in else None

    try:
        await asyncio.to_thread(_run)
    except Exception as e:
        shutil.rmtree(job_parent, ignore_errors=True)
        raise HTTPException(502, f"核验失败：{e}") from e

    saved: str | None = None
    try:
        dest = try_auto_export_phase2(out_root, baseline_name=baseline_display_name, job_id=job_id)
        if dest is not None:
            saved = str(dest)
    except OSError:
        saved = None

    _PHASE2_JOBS[job_id] = JobRecord(out_dir=out_root.resolve(), created=time.time())
    return JSONResponse(_build_phase2_download_response(job_id, local_saved_path=saved))


@app.get("/api/phase2/jobs/{job_id}/{file_key}")
def download_phase2_job_file(job_id: str, file_key: str) -> FileResponse:
    _cleanup_old_jobs()
    if file_key not in PHASE2_FILE_KEYS:
        raise HTTPException(404, "未知的文件类型")
    rec = _PHASE2_JOBS.get(job_id)
    if not rec:
        raise HTTPException(404, "任务不存在或已过期")
    rel, media = PHASE2_FILE_KEYS[file_key]
    path = rec.out_dir / rel
    if not path.is_file():
        raise HTTPException(404, "文件未生成")
    name = Path(rel).name
    return FileResponse(
        path,
        media_type=media,
        filename=name,
        content_disposition_type="attachment",
    )


@app.get("/api/phase2/jobs/{job_id}/bundle/{bundle_key}")
def download_phase2_job_bundle(job_id: str, bundle_key: str) -> FileResponse:
    _cleanup_old_jobs()
    if bundle_key not in PHASE2_BUNDLE_KEYS:
        raise HTTPException(404, "未知的目录打包类型")
    rec = _PHASE2_JOBS.get(job_id)
    if not rec:
        raise HTTPException(404, "任务不存在或已过期")
    rel_dir, filename = PHASE2_BUNDLE_KEYS[bundle_key]
    src_dir = rec.out_dir / rel_dir
    if not src_dir.is_dir():
        raise HTTPException(404, "目录未生成")
    bundle_dir = rec.out_dir.parent / "_bundles"
    zip_path = bundle_dir / f"{bundle_key}.zip"
    _zip_directory(src_dir, zip_path)
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=filename,
        content_disposition_type="attachment",
    )


@app.get("/api/jobs/{job_id}/{file_key}")
def download_job_file(job_id: str, file_key: str) -> FileResponse:
    _cleanup_old_jobs()
    if file_key not in JOB_FILE_KEYS:
        raise HTTPException(404, "未知的文件类型")
    rec = _JOBS.get(job_id)
    if not rec:
        raise HTTPException(404, "任务不存在或已过期")
    disk_name, media = JOB_FILE_KEYS[file_key]
    path = rec.out_dir / disk_name
    if not path.is_file():
        raise HTTPException(404, "文件未生成")
    return FileResponse(
        path,
        media_type=media,
        filename=disk_name,
        content_disposition_type="attachment",
    )


_fe_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@app.get("/favicon.ico", include_in_schema=False, response_model=None)
def favicon_ico():
    p = _fe_dist / "favicon.ico"
    if p.is_file():
        return FileResponse(p, media_type="image/x-icon")
    return Response(status_code=204)


@app.get("/.well-known/appspecific/com.chrome.devtools.json", include_in_schema=False)
def chrome_devtools_appspecific() -> dict:
    return {}


_assets = _fe_dist / "assets"
if (_fe_dist / "index.html").is_file() and _assets.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_assets)), name="fe_assets")

    @app.get("/", include_in_schema=False)
    def spa_index() -> FileResponse:
        return FileResponse(_fe_dist / "index.html")
