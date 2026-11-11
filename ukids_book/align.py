# -*- coding: utf-8 -*-
"""时间轴自动对齐：无 OCR。

原理：烧录字幕的出现在画面底部字幕带，台词切换时刻 = 字幕带突变时刻。
取录像第一段逐秒抽字幕带小图 → 得到"字幕变化事件流"；
SRT 的 cue 起止时刻构成另一条事件流；滑动偏移量做事件匹配，
得分最高的 (SRT, offset) 即对齐结果。场景切分用全帧差分掩蔽掉。
"""

import bisect
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageChops

from . import config, segment, subtitles

FPS = 2                # 每秒采样 2 帧，事件时间分辨率 0.5s
BAND = "1920:180:0:900"  # 字幕带 crop=w:h:x:y


def _band_diffs(seg: Path, n_sec: float, tmp: Path) -> list[tuple[float, float]]:
    """返回 [(t, band_diff, full_diff)]，t 为本地秒。"""
    bdir, fdir = tmp / "band", tmp / "full"
    shutil.rmtree(tmp, ignore_errors=True)
    bdir.mkdir(parents=True), fdir.mkdir(parents=True)
    for d, vf in ((bdir, f"fps={FPS},crop={BAND}"),
                  (fdir, f"fps={FPS},scale=192:108")):
        subprocess.run(
            [config.FFMPEG, "-i", str(seg), "-vf", vf,
             "-frames:v", str(int(n_sec * FPS)), str(d / "%03d.png")],
            capture_output=True, text=True)

    def diffs(d: Path) -> list[float]:
        files = sorted(d.glob("*.png"))
        out = []
        for a, b in zip(files, files[1:]):
            ia = Image.open(a).convert("L")
            ib = Image.open(b).convert("L")
            diff = ImageChops.difference(ia, ib)
            out.append(sum(diff.getdata()) / (diff.width * diff.height))
        return out

    bd, fd = diffs(bdir), diffs(fdir)
    return [(i / FPS + 1 / FPS, bd[i], fd[i] if i < len(fd) else 0)
            for i in range(len(bd))]


def _clean_signal(rows: list[tuple[float, float, float]]) -> list[float]:
    """band 差分减去全帧运动分量，保留字幕变化信号（0.5s 步长）。"""
    return [max(0.0, b - f) for _, b, f in rows]


def _srt_indicator(cues, n_samples: int) -> list[float]:
    """cue 边界指示序列（0.5s 步长）：边界 ±0.25s 内为 1。"""
    total = n_samples / FPS
    ind = [0.0] * n_samples
    for c in cues:
        for e in (c.t0, c.t1):
            if e > 5:                        # 忽略片头空白边界
                i = round(e * FPS)
                for j in (i - 1, i, i + 1):
                    if 0 <= j < n_samples:
                        ind[j] = 1.0
    return ind


def _events_from_signal(sig: list[float], th: float = 6.0) -> list[float]:
    """字幕带变化事件（本地时刻，秒）。"""
    return [(i + 1) / FPS for i, v in enumerate(sig) if v > th]


def _srt_edges(cues) -> list[float]:
    ev = sorted({round(c.t0, 2) for c in cues} | {round(c.t1, 2) for c in cues})
    return [e for e in ev if e > 5]


def _jaccard(local_ev: list[float], srt_ev: list[float], offset: float,
             n_loc: float, win: float = 0.75) -> float:
    """双向事件匹配率：本地事件命中率 + SRT边界覆盖率 的综合。"""
    matched_l = 0
    for t in local_ev:
        v = t + offset
        i = bisect.bisect_left(srt_ev, v)
        near = ((i < len(srt_ev) and srt_ev[i] - v <= win) or
                (i > 0 and v - srt_ev[i - 1] <= win))
        matched_l += near
    t_end = offset + n_loc
    srt_in = [s for s in srt_ev if offset <= s <= t_end]
    matched_s = 0
    for s in srt_in:
        near = any(abs(t + offset - s) <= win for t in local_ev)
        matched_s += near
    denom = len(local_ev) + len(srt_in)
    return (matched_l + matched_s) / max(1, denom)


def _best_offset(sig: list[float], cues) -> tuple[float, float]:
    """滑动偏移求最大 Jaccard，返回 (offset, 得分)。"""
    n_loc = len(sig) / FPS
    local_ev = _events_from_signal(sig)
    srt_ev = _srt_edges(cues)
    if len(local_ev) < 5:
        return 0.0, 0.0
    total = max(c.t1 for c in cues)
    best_off, best = 0.0, -1.0
    for k in range(0, int(total * 2)):
        off = k * 0.5
        s = _jaccard(local_ev, srt_ev, off, n_loc)
        if s > best:
            best, best_off = s, off
    return best_off, best


def align(task_dir: Path, srt_paths: list[Path]) -> dict:
    """返回 {srt, offset, score}。score 为相关系数，<0.15 时应人工核对。"""
    meta = json.loads((task_dir / "record_meta.json").read_text(encoding="utf-8"))
    first_seg = task_dir / "raw" / meta["segments"][0]["file"]
    n_sec = min(120, segment.duration(first_seg))   # 120s 窗口区分度最好
    rows = _band_diffs(first_seg, n_sec, task_dir / "align_tmp")
    sig = [r[1] for r in rows]                # band 原始差分

    best = {"srt": None, "offset": None, "score": 0}
    for sp in srt_paths:
        cues = subtitles.load(sp)
        off, r = _best_offset(sig, cues)
        print(f"  {sp.name}: offset={off:7.1f}s 相关={r:.3f}")
        if r > best["score"]:
            best = {"srt": str(sp), "offset": off, "score": r}

    (task_dir / "align.json").write_text(
        json.dumps(best, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(task_dir / "align_tmp", ignore_errors=True)
    return best
