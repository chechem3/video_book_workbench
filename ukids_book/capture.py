# -*- coding: utf-8 -*-
"""可控录屏：RecordSession 链式分段录制，start/stop 由 Web 服务调度。

任务目录: work/<name>_<YYYYmmdd_HHMMSS>/
  raw/seg_000.mp4 ...     分段录像（175s/段，段间 ~0.4s 盲区）
  record_meta.json        {name, created, segments:[{file, dur}]}
设备目录: /sdcard/ukcap/<task>/（停止后由管线拉回并清理）
"""

import datetime as dt
import json
import subprocess
import threading
import time
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


class RecordSession:
    """一次录制会话：start() 后台链式录段，stop() SIGINT 当前段并收尾。"""

    def __init__(self, dev: AdbDevice, task_dir: Path):
        self.dev = dev
        self.task_dir = task_dir
        self.dev_dir = f"{config.DEVICE_TMP_DIR}/{task_dir.name}"
        self.segments: list[dict] = []
        self.duration_hint: float | None = None
        self.error: str | None = None
        self.started_at: float | None = None
        self._end_at: float | None = None
        self._stop = threading.Event()
        self._watcher: threading.Thread | None = None
        self._size_at: tuple[float, float | None] | None = None

    # ---- 生命周期 ------------------------------------------------------
    def start(self, duration_hint: float | None = None) -> None:
        self.dev.ensure_ready()
        self.dev.shell("rm", "-rf", self.dev_dir, check=False)   # 清理残留
        self.dev.shell("mkdir", "-p", self.dev_dir)
        self.duration_hint = duration_hint
        self.started_at = time.monotonic()
        self._watcher = threading.Thread(target=self._loop, daemon=True)
        self._watcher.start()

    def alive(self) -> bool:
        return self._watcher is not None and self._watcher.is_alive()

    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self._end_at if self._end_at is not None else time.monotonic()
        return end - self.started_at

    def stop(self) -> list[dict]:
        """SIGINT 当前段（触发 mp4 正常收尾）并等待；写 record_meta.json。"""
        self._stop.set()
        for _ in range(3):
            self.dev.stop_screenrecord()
            if not self.alive():
                break
            self._watcher.join(timeout=15)
        if self._end_at is None:
            self._end_at = time.monotonic()
        meta = {"name": self.task_dir.name,
                "created": dt.datetime.now().isoformat(timespec="seconds"),
                "segments": self.segments}
        (self.task_dir / "record_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        return self.segments

    def size_mb(self) -> float | None:
        """设备目录占用（MB），5s 节流；失败返回 None。"""
        now = time.monotonic()
        if self._size_at and now - self._size_at[0] < 5.0:
            return self._size_at[1]
        try:
            out = self.dev.shell("du", "-k", self.dev_dir, check=False)
            kb = float(out.strip().splitlines()[-1].split()[0])
            mb = round(kb / 1024, 1)
        except Exception:
            mb = None
        self._size_at = (now, mb)
        return mb

    # ---- 内部 ----------------------------------------------------------
    def _loop(self) -> None:
        idx = 0
        while not self._stop.is_set():
            elapsed = self.elapsed()
            if self.duration_hint is not None and elapsed >= self.duration_hint - 1.0:
                break                                    # 到达预计时长，自然收尾
            seg_dur = float(config.SEGMENT_SECONDS)
            if self.duration_hint is not None:
                seg_dur = min(seg_dur, max(5.0, self.duration_hint - elapsed))
            fname = f"seg_{idx:03d}.mp4"
            p = self.dev.shell_bg(
                "screenrecord",
                "--bit-rate", config.RECORD_BITRATE,
                "--size", config.RECORD_SIZE,
                "--time-limit", str(int(seg_dur)),
                f"{self.dev_dir}/{fname}",
            )
            try:
                rc = p.wait(timeout=seg_dur + 90)
            except subprocess.TimeoutExpired:
                p.kill()
                self.dev.stop_screenrecord()
                self.error = f"screenrecord 段 {idx} 超时无响应"
                break
            if self._stop.is_set():
                # 被 SIGINT 收尾的段：文件已正常落盘，dur 由管线 ffprobe 校验
                self.segments.append({"file": fname, "dur": None})
                break
            if rc != 0:
                err = ""
                if p.stderr:
                    err = p.stderr.read().decode(errors="replace")[-300:]
                self.error = f"screenrecord 段 {idx} 异常退出 rc={rc}: {err}"
                break
            self.segments.append({"file": fname, "dur": seg_dur})
            idx += 1
