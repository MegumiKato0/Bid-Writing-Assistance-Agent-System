from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cli_validate_llm_for_remote() -> str | None:
    """远程非 Ollama 时要求有效 API Key；本地 Ollama 不校验。"""
    from bidprep.config import settings

    key = (settings.llm_api_key or "").strip()
    base = (settings.llm_base_url or "").strip().lower()
    if "127.0.0.1:11434" in base or "localhost:11434" in base:
        return None
    if not key or key in ("sk-placeholder", "ollama"):
        return (
            "阶段二固定为双 Agent，当前 LLM_BASE_URL 指向远程接口，"
            "请在 .env 中为 LLM_API_KEY 填写有效密钥。"
        )
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="客户材料核验与归档（Phase 2，固定双 Agent）")
    p.add_argument(
        "--baseline",
        type=Path,
        action="append",
        dest="baselines",
        metavar="PATH",
        required=True,
        help="投标材料清单表 .xlsx / .xlsm 或 baseline .json（可重复指定多个，合并为一条清单）",
    )
    p.add_argument(
        "--client",
        type=Path,
        help="客户材料：目录、ZIP 或单个 pdf/docx/图片",
    )
    p.add_argument(
        "--client-file",
        type=Path,
        action="append",
        dest="client_files",
        default=[],
        help="额外客户文件（可重复）",
    )
    p.add_argument("-o", "--out-dir", type=Path, required=True, help="输出目录")
    p.add_argument("--no-ocr", action="store_true", help="禁用 OCR 兜底")
    p.add_argument(
        "--llm-assist",
        action="store_true",
        help="启用大模型灰区辅助审查（build_verifications 之后；使用 .env / 环境变量中的 LLM 配置）",
    )
    p.add_argument(
        "--llm-file-read-batch-size",
        type=int,
        default=4,
        metavar="N",
        help="Agent A 每批最多文件数，1～32，默认 4",
    )
    p.add_argument(
        "--include-unused-files",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="是否输出未使用文件相关报表（默认开启；使用 --no-include-unused-files 关闭）",
    )
    p.add_argument(
        "--llm-apply",
        action="store_true",
        help="在满足置信度阈值时将模型 satisfied 建议应用为已匹配（见 pipeline 说明）",
    )
    p.add_argument(
        "--llm-min-confidence",
        type=float,
        default=0.85,
        help="应用建议时的最低置信度 0～1，默认 0.85",
    )
    args = p.parse_args(argv)

    if args.client is None and not args.client_files:
        print("请提供 --client 和/或至少一个 --client-file", file=sys.stderr)
        return 2
    if not args.baselines:
        print("请至少指定一个 --baseline", file=sys.stderr)
        return 2

    err = _cli_validate_llm_for_remote()
    if err:
        print(err, file=sys.stderr)
        return 2

    from bidprep.llm_runtime import runtime_from_settings
    from bidprep.phase2.pipeline import run_phase2_verify

    llm = runtime_from_settings()

    def prog(m: str) -> None:
        print(m, file=sys.stderr)

    mc = max(0.0, min(1.0, float(args.llm_min_confidence)))
    bs = max(1, min(32, int(args.llm_file_read_batch_size)))
    include_unused = bool(args.include_unused_files)

    run_phase2_verify(
        args.baselines,
        args.client,
        args.out_dir,
        client_paths=args.client_files or None,
        ocr_fallback=not args.no_ocr,
        progress=prog,
        llm=llm,
        llm_assist=bool(args.llm_assist),
        llm_dual_agent=True,
        llm_file_read=True,
        llm_file_read_batch_size=bs,
        include_unused_files=include_unused,
        llm_apply=bool(args.llm_apply),
        llm_min_confidence=mc,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
