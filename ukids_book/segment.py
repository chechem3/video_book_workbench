# -*- coding: utf-8 -*-
"""ffmpeg 分片与抽帧：场景检测切分（min/max 段长钳制），每段取中点帧。"""

import re
import subprocess
from pathlib import Path

from . import config

_PTS = re.compile(r"pts_time:([\d.]+)")


def _run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"命令失败: {cmd[:3]}...\n{r.stderr[-500:]}")
    return r.stderr + r.stdout


def duration(file: Path) -> float:
    out = subprocess.run(
        [config.FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(file)],
        capture_output=True, text=True).stdout.strip()
    return float(out)


def scene_cuts(file: Path, threshold: float) -> list[float]:
    """场景突变时刻（本地时间戳，升序）。"""
    err = _run([
        config.FFMPEG, "-i", str(file), "-vf",
        f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-",
    ])
    return sorted(float(m) for m in _PTS.findall(err))


def build_segments(cuts: list[float], total: float,
                   min_sec: float, max_sec: float) -> list[tuple[float, float]]:
    """由切点构建 (start, end) 片段列表，钳制到 [min_sec, max_sec]。"""
    points = [0.0] + [c for c in cuts if 0.5 < c < total - 0.5] + [total]
    segs: list[list[float]] = []
    for a, b in zip(points, points[1:]):
        if segs and a - segs[-1][0] < min_sec:
            segs[-1][1] = b            # 与上一段合并（过短）
        else:
            segs.append([a, b])
    out = []
    for a, b in segs:                   # 过长均匀拆分
        n = max(1, round((b - a) / max_sec))
        step = (b - a) / n
        for i in range(n):
            out.append((a + i * step, a + (i + 1) * step))
    return out


def extract_frame(file: Path, t: float, out_png: Path) -> Path:
    subprocess.run(
        [config.FFMPEG, "-ss", f"{t:.3f}", "-i", str(file),
         "-frames:v", "1", "-y", str(out_png)],
        capture_output=True, text=True)
    return out_png
