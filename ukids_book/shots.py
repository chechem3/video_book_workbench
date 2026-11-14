# -*- coding: utf-8 -*-
"""切镜头：thumbs 差分 + 自适应阈值 + min_shots 钳制 → shots.yaml（单一事实源）。

shots.yaml:
  meta:  {task, dt, threshold, min_shots}
  shots: [{id, main, candidates, others, subs}]   # subs 为近似字幕条数（UI 参考）
"""

import json
import statistics
from pathlib import Path

import yaml
from PIL import Image, ImageChops

from . import config
from .subtitles import select_strip_names

SMALL = (80, 45)          # 差分用缩略尺寸
YAML_NAME = "shots.yaml"


def _fno(f: str) -> int:
    """帧文件名 f012345.jpg → 帧序号（排序用）。"""
    return int(f[1:7])


# ---- 读写 --------------------------------------------------------------

def read_shots(task_dir: Path) -> dict | None:
    p = task_dir / YAML_NAME
    if not p.is_file():
        return None
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def write_shots(task_dir: Path, doc: dict) -> None:
    (task_dir / YAML_NAME).write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=200),
        encoding="utf-8")


def count_subs(task_dir: Path, names: list[str]) -> int:
    """按 strips/ 全尺寸字幕带统计去重后的近似条数（UI 参考用）。"""
    strips = task_dir / "strips"
    try:
        return len(select_strip_names([strips / nm for nm in names]))
    except Exception:
        return 0


def refresh_candidates(sh: dict) -> None:
    """按 others 重建 candidates（≤5 均布，含 main）；main 丢失则取中间帧。"""
    others = sh["others"]
    if not others:
        return
    if sh.get("main") not in others:
        sh["main"] = others[len(others) // 2]
    n = len(others)
    if n <= config.CANDIDATES_MAX:
        cand = others
    else:
        cand = [others[round(j * (n - 1) / (config.CANDIDATES_MAX - 1))]
                for j in range(config.CANDIDATES_MAX)]
    if sh["main"] not in cand:
        cand.append(sh["main"])
    sh["candidates"] = cand


def maintain_shot(sh: dict, extra_mains: list[str] | None = None) -> None:
    """维护多主图一致性：mains ⊆ others（按帧序），main = mains[0]，≥1 张。

    extra_mains: 编辑操作（合并/移入）带来的额外主图，并入后统一维护。
    """
    others = sh["others"]
    if not others:
        return
    mains = [f for f in (sh.get("mains") or []) if f in others]
    for f in extra_mains or []:
        if f in others and f not in mains:
            mains.append(f)
    if sh.get("main") in others and sh["main"] not in mains:
        mains.insert(0, sh["main"])
    if not mains:
        mains = [others[len(others) // 2]]
    mains = sorted(set(mains), key=_fno)
    sh["mains"] = mains
    sh["main"] = mains[0]
    refresh_candidates(sh)


# ---- 检测 --------------------------------------------------------------

def _mean_abs_diff(a: Image.Image, b: Image.Image) -> float:
    """两灰度图的平均绝对差（0-255 量纲）。"""
    hist = ImageChops.difference(a, b).histogram()  # 256 bins，C 速度
    n = a.width * a.height
    return sum(i * c for i, c in enumerate(hist)) / n


def detect_shots(task_dir: Path, threshold: float | None = None,
                 min_shots: int | None = None,
                 keep_main_by_id: dict | None = None,
                 progress=None) -> dict:
    """切镜头并写 shots.yaml；keep_main_by_id 保留人工挑选的主图。"""
    fj = task_dir / "frames.json"
    if not fj.is_file():
        raise RuntimeError("缺少 frames.json，请先抽帧")
    doc_f = json.loads(fj.read_text(encoding="utf-8"))
    dt = float(doc_f["dt"])
    files = [f["file"] for f in doc_f["frames"]]
    if len(files) < 2:
        raise RuntimeError("帧数不足，无法切镜头")
    thumbs = task_dir / "thumbs"

    grays, n = [], len(files)
    for i, f in enumerate(files):
        grays.append(Image.open(thumbs / f).convert("L").resize(SMALL))
        if progress and (i % 100 == 0 or i == n - 1):
            progress(i + 1, n, f"读帧 {i + 1}/{n}")

    diffs = [_mean_abs_diff(grays[i - 1], grays[i]) for i in range(1, n)]
    if threshold is None:
        mu, sd = statistics.mean(diffs), statistics.pstdev(diffs)
        threshold = min(config.DIFF_MAX_TH,
                        max(config.DIFF_MIN_TH, mu + config.DIFF_K * sd))
    threshold = round(float(threshold), 1)
    ms = int(min_shots or config.MIN_SHOTS)

    # diffs[k] = 帧 k 与 k+1 的差 → 切点帧号 k+1
    starts = [0] + [k + 1 for k, d in enumerate(diffs) if d > threshold]
    ranges = [(starts[j], starts[j + 1]) for j in range(len(starts) - 1)]
    ranges.append((starts[-1], n))
    merged: list[list[int]] = []
    for s, e in ranges:
        if merged and e - s < ms:                   # 短镜头并入前一个
            merged[-1].extend(range(s, e))
        else:
            merged.append(list(range(s, e)))
    if len(merged) >= 2 and len(merged[0]) < ms:     # 首镜头过短并入下一个
        merged[1][:0] = merged[0]
        merged.pop(0)

    keep = keep_main_by_id or {}
    shot_list = []
    for sid, idxs in enumerate(merged, 1):
        nf = len(idxs)
        if nf <= config.CANDIDATES_MAX:
            cand_idx = idxs
        else:
            cand_idx = [idxs[round(j * (nf - 1) / (config.CANDIDATES_MAX - 1))]
                        for j in range(config.CANDIDATES_MAX)]
        names = [files[i] for i in idxs]
        cand = [files[i] for i in cand_idx]
        main = cand[len(cand) // 2]                 # 默认主图 = 候选中间帧
        if keep.get(sid) in names:                  # 重分析保留人工主图
            main = keep[sid]
        shot_list.append({"id": sid, "main": main, "mains": [main],
                          "candidates": cand,
                          "others": names, "subs": count_subs(task_dir, names)})
        if progress:
            progress(sid, len(merged), f"镜头 {sid}/{len(merged)}")

    doc = {"meta": {"task": task_dir.name, "dt": dt,
                    "threshold": threshold, "min_shots": ms},
           "shots": shot_list}
    write_shots(task_dir, doc)
    return doc
