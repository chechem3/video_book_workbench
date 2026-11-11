# -*- coding: utf-8 -*-
"""录制：链式 screenrecord，任务目录隔离。

任务目录: work/<name>_<YYYYmmdd_HHMMSS>/
  raw/seg_000.mp4 ...     分段录像（175s/段，段间 ~0.4s 盲区）
  record_meta.json        {name, created, segments:[{file, dur}]}
"""

import datetime as dt
import json
from pathlib import Path

from . import config
from .adb import AdbDevice


def new_task_dir(name: str = "task") -> Path:
    """创建不冲突的任务目录：名称_时间戳，同秒冲突则递增后缀。"""
    base = config.WORK_DIR
    base.mkdir(parents=True, exist_ok=True)
    stem = f"{name}_{dt.datetime.now():%Y%m%d_%H%M%S}"
    task = base / stem
    n = 1
    while task.exists():
        n += 1
        task = base / f"{stem}_{n}"
    (task / "raw").mkdir(parents=True)
    return task


def record(task: Path, duration: float) -> Path:
    """链式录制 duration 秒到任务目录。Ctrl+C 可安全中断，已完成段照常拉回。"""
    dev = AdbDevice()
    dev.ensure_ready()
    dev_dir = f"{config.DEVICE_TMP_DIR}/{task.name}"
    dev.shell("mkdir", "-p", dev_dir)

    segs: list[dict] = []
    remaining = duration
    idx = 0
    print(f"开始录制 {duration:.0f}s → {task}")
    try:
        while remaining > 0.5:
            seg_dur = min(config.SEGMENT_SECONDS, remaining)
            fname = f"seg_{idx:03d}.mp4"
            print(f"  段 {idx}: {seg_dur:.0f}s ...", flush=True)
            p = dev.shell_bg(
                "screenrecord",
                "--bit-rate", config.RECORD_BITRATE,
                "--size", config.RECORD_SIZE,
                "--time-limit", str(int(seg_dur)),
                f"{dev_dir}/{fname}",
            )
            rc = p.wait(timeout=seg_dur + 60)
            if rc != 0:
                err = p.stderr.read().decode(errors="replace") if p.stderr else ""
                raise RuntimeError(f"screenrecord 段 {idx} 失败 rc={rc}: {err}")
            segs.append({"file": fname, "dur": seg_dur})
            remaining -= seg_dur
            idx += 1
    except KeyboardInterrupt:
        print("\n用户中断，回收已完成的段 ...")
    finally:
        meta = {"name": task.name, "created": dt.datetime.now().isoformat(),
                "segments": segs}
        (task / "record_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        _pull_all(dev, dev_dir, task / "raw")
        dev.shell("rm", "-rf", dev_dir)
    print(f"录制完成: {len(segs)} 段 → {task/'raw'}")
    return task


def _pull_all(dev: AdbDevice, dev_dir: str, local: Path) -> None:
    out = dev.shell("ls", dev_dir)
    for fname in out.split():
        if fname.endswith(".mp4"):
            dev.pull(f"{dev_dir}/{fname}", local / fname)
            print(f"  拉回 {fname}")
