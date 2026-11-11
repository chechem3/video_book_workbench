# -*- coding: utf-8 -*-
"""总装：任务目录 → 分段抽帧 → 长图 + A4 PDF。

字幕两种来源，sub_mode 控制：
  srt  —— SRT 文本渲染（需对齐；auto 时得分<0.75 自动弃用）
  band —— 从录像底部抠字幕条拼图（不依赖 SRT）
  auto —— 先试 SRT 对齐，可靠则用 srt，否则降级 band
"""

import json
from pathlib import Path

from . import config, segment, subtitles
from .align import align
from .compose import compose_book, compose_book_band, compose_pdf, compose_pdf_band

ALIGN_OK = 0.75        # SRT 对齐得分阈值，低于则降级 band 模式


def _pieces(task_dir: Path, scene_th, min_sec, max_sec) -> list:
    """场景切分 → [(视频t0, 视频t1, 文件, 本地t0, 本地t1)]。"""
    meta = json.loads((task_dir / "record_meta.json").read_text(encoding="utf-8"))
    pieces = []
    for seg in meta["segments"]:
        f = task_dir / "raw" / seg["file"]
        if not f.exists():
            continue
        dur = segment.duration(f)
        cuts = segment.scene_cuts(f, scene_th)
        for a, b in segment.build_segments(cuts, dur, min_sec, max_sec):
            pieces.append((seg["video_start"] + a, seg["video_start"] + b,
                           f, a, b))
    pieces.sort(key=lambda p: p[0])
    return pieces


def build_task(task_dir: Path, srt_paths: list[Path],
               offset: float | None = None, sub_mode: str = "auto",
               scene_th: float = config.SCENE_THRESHOLD,
               min_sec: float = config.MIN_SEG_SECONDS,
               max_sec: float = config.MAX_SEG_SECONDS,
               make_png: bool = True, make_pdf: bool = True) -> dict:
    task_dir = Path(task_dir)
    pieces = _pieces(task_dir, scene_th, min_sec, max_sec)
    frames_dir = task_dir / "frames"
    frames_dir.mkdir(exist_ok=True)

    # 抽中点帧（两种模式共用）
    frame_pngs = []
    for i, (vt0, vt1, f, lt0, lt1) in enumerate(pieces):
        png = frames_dir / f"key_{i:03d}.png"
        segment.extract_frame(f, (lt0 + lt1) / 2, png)
        frame_pngs.append(png)
    print(f"片段 {len(pieces)} 个，抽帧完成")

    # 决定字幕模式
    cues, use_srt = None, False
    if sub_mode in ("srt", "auto") and srt_paths:
        if offset is not None and len(srt_paths) == 1:
            best = {"srt": str(srt_paths[0]), "offset": offset, "score": 1.0}
        else:
            print("自动对齐字幕 ...")
            best = align(task_dir, srt_paths)
        if best["srt"] and (sub_mode == "srt" or best["score"] >= ALIGN_OK):
            cues = subtitles.load(best["srt"])
            use_srt = True
            offset = best["offset"]
            print(f"字幕模式: srt ({Path(best['srt']).name}, "
                  f"offset={offset:.1f}s, 得分={best['score']:.2f})")
        else:
            print(f"SRT 对齐不可靠(得分={best.get('score', 0):.2f})，降级 band 模式")
    else:
        print("字幕模式: band（按参数指定）")

    out_dir = config.OUTPUT_DIR / task_dir.name
    result = {"segments": len(pieces)}
    if use_srt:
        entries = [(png, subtitles.cues_in(cues, vt0, vt1))
                   for png, (vt0, vt1, *_ ) in zip(frame_pngs, pieces)]
        hit = sum(1 for _, c in entries if c)
        print(f"台词命中: {hit}/{len(entries)} 段")
        result.update(cue_hit=hit)
        if make_png:
            result["png"] = compose_book(entries, out_dir / "book.png")
        if make_pdf:
            result["pdf"] = compose_pdf(entries, out_dir / "book.pdf",
                                        pages_dir=task_dir / "pages_srt")
    else:
        entries = []
        for i, (vt0, vt1, f, lt0, lt1) in enumerate(pieces):
            strips = subtitles.band_strips(f, lt0, lt1)
            entries.append((frame_pngs[i], strips))
        n_strips = sum(len(s) for _, s in entries)
        print(f"字幕条共 {n_strips} 条")
        result.update(strips=n_strips)
        if make_png:
            result["png"] = compose_book_band(entries, out_dir / "book_band.png")
        if make_pdf:
            result["pdf"] = compose_pdf_band(entries, out_dir / "book_band.pdf",
                                             pages_dir=task_dir / "pages_band")
    for k in ("png", "pdf"):
        if k in result:
            print(f"{'长图' if k == 'png' else 'PDF '} → {result[k]}")
    return result
