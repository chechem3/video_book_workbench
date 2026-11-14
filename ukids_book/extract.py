# -*- coding: utf-8 -*-
"""分帧：raw/ 分段录像 → shots/ 全尺寸 JPEG + thumbs/ 320px + strips/ 字幕带 + frames.json。

strips/ 在抽帧时顺带从同一张解码图抠出（近零成本），出稿时无需重复解码全帧。
frames.json: {"dt": 1.0, "frames": [{"file", "t", "seg"}, ...]}（t 为全局秒）
"""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from . import config, subtitles


def probe_duration(video: Path) -> float:
    """ffprobe 读时长（秒）；失败/损坏返回 0。"""
    r = subprocess.run(
        [config.FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, timeout=60)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _reset_dir(d: Path) -> None:
    if d.exists():
        for f in d.iterdir():
            f.unlink() if f.is_file() else None
    d.mkdir(parents=True, exist_ok=True)


def extract_frames(task_dir: Path, dt: float, progress=None) -> list[dict]:
    """对 raw/ 全部分段按 dt 抽帧，返回 frames 列表并写 frames.json。

    progress(done:int, total:int, label:str)——total=0 表示分段阶段无总量。
    """
    dt = max(0.5, float(dt))
    raw = sorted((task_dir / "raw").glob("seg_*.mp4"))
    if not raw:
        raise RuntimeError("raw/ 中没有录像段")
    shots_dir = task_dir / "shots"
    thumbs_dir = task_dir / "thumbs"
    strips_dir = task_dir / "strips"
    for d in (shots_dir, thumbs_dir, strips_dir):
        _reset_dir(d)

    frames: list[dict] = []
    next_no = 1
    seg_t0 = 0.0
    for i, seg in enumerate(raw):
        dur = probe_duration(seg)
        if dur < 0.5:
            continue                                # 损坏段跳过
        r = subprocess.run(
            [config.FFMPEG, "-nostdin", "-loglevel", "error", "-y",
             "-i", str(seg), "-vf", f"fps={1 / dt:.4f}", "-q:v", "2",
             "-start_number", str(next_no),
             str(shots_dir / "f%06d.jpg")],
            capture_output=True, text=True, timeout=max(180, dur * 4))
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg 分帧失败 {seg.name}: {r.stderr[-400:]}")
        n = len(list(shots_dir.glob("f*.jpg"))) - (next_no - 1)
        for k in range(n):
            frames.append({"file": f"f{next_no + k:06d}.jpg",
                           "t": round(seg_t0 + k * dt, 2), "seg": seg.name})
        next_no += n
        seg_t0 += dur
        if progress:
            progress(len(frames), 0, f"{i + 1}/{len(raw)} 段 · {len(frames)} 帧")

    # 缩略图 + 字幕带（并行；同一张解码图两个产物）
    total = len(frames)

    def _one(item):
        i, f = item
        img = Image.open(shots_dir / f["file"])
        subtitles.crop_band(img).save(strips_dir / f["file"], quality=90)
        w = config.THUMB_WIDTH
        img.resize((w, round(img.height * w / img.width)),
                   Image.LANCZOS).save(thumbs_dir / f["file"],
                                       quality=config.JPEG_Q_THUMB)
        if progress and (i % 50 == 0 or i == total - 1):
            progress(i + 1, total, f"缩略图 {i + 1}/{total}")

    if frames:
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(_one, enumerate(frames)))

    (task_dir / "frames.json").write_text(
        json.dumps({"dt": dt, "frames": frames}, ensure_ascii=False),
        encoding="utf-8")
    return frames
