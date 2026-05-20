from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="招标 PDF → 投标材料清单")
    p.add_argument("pdf", type=Path, help="招标文件 PDF 路径")
    p.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=Path("out"),
        help="输出目录",
    )
    p.add_argument(
        "--skip-llm",
        action="store_true",
        help="跳过 LLM（仅规则兜底 + 空表骨架，用于离线测试）",
    )
    args = p.parse_args(argv)

    from bidprep.pipeline import run_pipeline

    def prog(msg: str) -> None:
        print(msg, file=sys.stderr)

    run_pipeline(args.pdf, args.out_dir, progress=prog, skip_llm=args.skip_llm)
    out_resolved = args.out_dir.resolve()
    print(f"已写入：{out_resolved}", file=sys.stderr)
    from bidprep.local_export import try_auto_export_phase1

    extra = try_auto_export_phase1(out_resolved, input_pdf=args.pdf.resolve())
    if extra is not None:
        print(f"已自动归档到：{extra}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
