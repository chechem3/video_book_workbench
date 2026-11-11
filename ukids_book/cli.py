# -*- coding: utf-8 -*-
"""命令行入口。

python -m ukids_book.cli record --name E315 --duration 300 [--offset 0]
python -m ukids_book.cli build  --task work/E315_xxx --srt assets/subtitles/x.srt [--offset 0]
"""

import argparse
import sys
from pathlib import Path

from . import config
from .capture import new_task_dir, record


def cmd_record(args) -> None:
    task = new_task_dir(args.name)
    record(task, duration=args.duration, offset=args.offset)
    print(f"\n任务目录: {task}")
    print("处理: python -m ukids_book.cli build "
          f"--task {task} --srt <srt路径>")


def cmd_build(args) -> None:
    from .pipeline import build_task
    srts = []
    for s in args.srt:
        p = Path(s)
        if not p.is_absolute():
            p = config.PROJECT_ROOT / p
        srts.extend(sorted(p.parent.glob(p.name)) if any(
            c in s for c in "*?") else [p])
    build_task(Path(args.task), srts, offset=args.offset, sub_mode=args.sub,
               scene_th=args.scene_th, min_sec=args.min_sec,
               max_sec=args.max_sec, make_png=not args.no_png,
               make_pdf=not args.no_pdf)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="ukids_book")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_rec = sub.add_parser("record", help="链式录屏到任务目录")
    p_rec.add_argument("--name", default="task", help="任务名（目录前缀）")
    p_rec.add_argument("--duration", type=float, required=True,
                       help="录制总秒数")
    p_rec.add_argument("--offset", type=float, default=0.0,
                       help="录制起点对应的视频时间戳（秒），用于SRT对齐")
    p_rec.set_defaults(func=cmd_record)

    p_bld = sub.add_parser("build", help="处理任务目录生成长图+PDF")
    p_bld.add_argument("--task", required=True, help="任务目录 (work/xxx)")
    p_bld.add_argument("--srt", nargs="+", default=[],
                       help="SRT 路径，可多个或通配符；band 模式可省略")
    p_bld.add_argument("--sub", choices=["auto", "srt", "band"], default="auto",
                       help="字幕来源：auto=SRT对齐可靠则用文本，否则抠字幕条")
    p_bld.add_argument("--offset", type=float, default=None,
                       help="手动指定视频时间偏移（跳过自动对齐）")
    p_bld.add_argument("--scene-th", type=float, default=config.SCENE_THRESHOLD)
    p_bld.add_argument("--min-sec", type=float, default=config.MIN_SEG_SECONDS)
    p_bld.add_argument("--max-sec", type=float, default=config.MAX_SEG_SECONDS)
    p_bld.add_argument("--no-png", action="store_true", help="不输出长图")
    p_bld.add_argument("--no-pdf", action="store_true", help="不输出PDF")
    p_bld.set_defaults(func=cmd_build)

    args = ap.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
