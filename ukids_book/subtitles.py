# -*- coding: utf-8 -*-
"""SRT 字幕解析：UTF-8 BOM / CRLF / 中英双语（中文行在前，英文行在后）。"""

import re
from dataclasses import dataclass
from pathlib import Path

from . import config

_TIME = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


@dataclass
class Cue:
    t0: float
    t1: float
    zh: str
    en: str


def _sec(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def load(path: str | Path) -> list[Cue]:
    text = Path(path).read_text(encoding="utf-8-sig")
    cues = []
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if len(lines) < 2:
            continue
        m = _TIME.search(lines[1])
        if not m:
            continue
        g = m.groups()
        body = lines[2:]
        zh = body[0] if body else ""
        en = " ".join(body[1:])
        cues.append(Cue(_sec(*g[:4]), _sec(*g[4:]), zh, en))
    return cues


def cues_in(cues: list[Cue], t0: float, t1: float,
            tol: float = config.SRT_TOLERANCE) -> list[Cue]:
    """返回与 [t0, t1]（含容差）有交集的台词，按时间排序。"""
    return [c for c in cues if c.t0 <= t1 + tol and c.t1 >= t0 - tol]


# ---- 字幕带切片模式：不依赖 SRT，直接从录像抠底部字幕条 ----

BAND_Y0, BAND_Y1 = 930, 1060     # 字幕文本集中区（1080p，含双行）
BIN_TH = 190                     # 白字二值化阈值
DUP_TH = 0.05                    # 字模差异像素占比低于 5% 视为同一句（含淡入淡出微差）
BLANK_RATIO = 0.003              # 字模覆盖率低于此视为无字幕


def _text_mask(img: "Image.Image") -> tuple["Image.Image", float]:
    """亮度二值化提取白色字模，返回 (mask, 覆盖率)。"""
    mask = img.point(lambda p: 255 if p > BIN_TH else 0)
    n = mask.width * mask.height
    ratio = sum(mask.getdata()) / (255 * n)
    return mask, ratio


def band_strips(file: Path, t0: float, t1: float, fps: int = 2) -> list:
    """[t0,t1] 内按 fps 抽帧裁字幕带，按字模去重去空白后返回条图列表。"""
    import subprocess
    import tempfile

    from PIL import Image, ImageChops

    with tempfile.TemporaryDirectory() as td:
        subprocess.run(
            [config.FFMPEG, "-ss", f"{t0:.3f}", "-i", str(file),
             "-t", f"{t1 - t0:.3f}",
             "-vf", f"fps={fps},crop=1920:{BAND_Y1 - BAND_Y0}:0:{BAND_Y0}",
             str(Path(td) / "s%03d.png")],
            capture_output=True, text=True)
        strips, prev_mask = [], None
        for p in sorted(Path(td).glob("s*.png")):
            gray = Image.open(p).convert("L")
            mask, ratio = _text_mask(gray)
            if ratio < BLANK_RATIO:        # 无字幕
                continue
            if prev_mask is not None:
                diff = ImageChops.difference(mask, prev_mask)
                d = sum(diff.getdata()) / (diff.width * diff.height * 255)
                if d < DUP_TH:             # 同一句（背景变化不影响字模）
                    continue
            prev_mask = mask
            strips.append(Image.open(p).convert("RGB"))
    return strips
