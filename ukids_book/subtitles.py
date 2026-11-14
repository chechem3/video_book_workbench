# -*- coding: utf-8 -*-
"""字幕带处理：从帧图抠底部字幕条，白字字模去重。

v2 唯一字幕来源——不依赖 SRT 文件与播放器实现。
字模判据：亮像素(>235) 且 邻近暗像素(<100，字幕描边)——排除亮色背景
（沙滩/天空等大面积高亮动画背景会越过纯亮度阈值）。
"""

from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

BAND_Y0, BAND_Y1 = 930, 1060     # 字幕文本集中区（1080p，含双行）
BIN_TH = 235                     # 白字二值化阈值
DARK_TH = 100                    # 描边/阴影暗阈值
DUP_TH = 0.05                    # 字模差异像素占比低于 5% 视为同一句（含淡入淡出微差）
BLANK_RATIO = 0.003              # 字模覆盖率低于此视为无字幕


def _text_mask(band: Image.Image) -> tuple[Image.Image, float]:
    """提取「亮且有暗描边」的字模，返回 (mask, 覆盖率)。

    描边搜索半径随宽度缩放（1920px 原图 5，320px 缩略图 3）。
    """
    gray = band.convert("L")
    radius = 5 if gray.width >= 1000 else 3
    bright = gray.point(lambda p: 255 if p > BIN_TH else 0)
    dark = gray.point(lambda p: 255 if p < DARK_TH else 0) \
        .filter(ImageFilter.MaxFilter(radius))
    mask = ImageChops.multiply(bright, dark)
    n = mask.width * mask.height
    ratio = sum(mask.getdata()) / (255 * n)
    return mask, ratio


def crop_band(img: Image.Image) -> Image.Image:
    """整帧 → 底部字幕条；按 1080p 基准坐标随高度缩放（兼容缩略图等）。"""
    s = img.height / 1080
    return img.crop((0, round(BAND_Y0 * s), img.width, round(BAND_Y1 * s)))


def _band_of(p: Path) -> Image.Image:
    """打开帧文件；已是字幕带（高度小）直用，否则抠带。"""
    img = Image.open(p)
    return img if img.height <= 300 else crop_band(img)


def _filter_mask(bands: list, name_of) -> list:
    """按字模过滤（去空白 + 同句去重），返回 (band, name) 保留项。"""
    kept, prev_mask = [], None
    for i, band in enumerate(bands):
        mask, ratio = _text_mask(band)
        if ratio < BLANK_RATIO:            # 无字幕
            continue
        if prev_mask is not None:
            diff = ImageChops.difference(mask, prev_mask)
            d = sum(diff.getdata()) / (diff.width * diff.height * 255)
            if d < DUP_TH:                 # 同一句
                continue
        prev_mask = mask
        kept.append((band, name_of(i)))
    return kept


def select_strip_names(paths: list[Path]) -> list[str]:
    """返回有字幕的帧名（去空白 + 同句去重）——OCR 预填与计数的选帧依据。"""
    bands = [_band_of(p) for p in paths]
    return [name for _, name in _filter_mask(bands, lambda i: paths[i].name)]
