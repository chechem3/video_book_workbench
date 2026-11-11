# -*- coding: utf-8 -*-
"""ukids_book 命令行入口。

当前命令：
  record  链式录屏到任务目录
  shot    即时截图（单张 / 定时连拍）
后续按方案 v2 增补：
  extract（录像按 dt 分帧）/ analyze（切镜头→shots.yaml）
  compose（出长图+PDF）/ serve（Web 编辑界面）
"""

import argparse
import sys
from pathlib import Path

from . import config
from .capture import new_task_dir, record


def cmd_record(args) -> None:
    task = new_task_dir(args.name)
    record(task, duration=args.duration)


def cmd_shot(args) -> None:
    from .screenshot import shoot
    shoot(Path(args.out).resolve(), interval=args.interval,
          count=args.count, serial=args.serial)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="ukids_book")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_rec = sub.add_parser("record", help="链式录屏到任务目录")
    p_rec.add_argument("--name", default="task", help="任务名（目录前缀）")
    p_rec.add_argument("--duration", type=float, required=True,
                       help="录制总秒数（一集约 1275）")
    p_rec.set_defaults(func=cmd_record)

    p_shot = sub.add_parser("shot", help="即时截图（单张/定时连拍）")
    p_shot.add_argument("-o", "--out", default=str(config.WORK_DIR / "screenshots"),
                        help="输出目录")
    p_shot.add_argument("-i", "--interval", type=float, help="连拍间隔秒数（缺省截一张）")
    p_shot.add_argument("-c", "--count", type=int, help="连拍张数（缺省到 Ctrl+C）")
    p_shot.add_argument("--serial", default=config.DEVICE_SERIAL, help="设备序列号")
    p_shot.set_defaults(func=cmd_shot)

    args = ap.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
