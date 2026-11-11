# -*- coding: utf-8 -*-
"""字幕带处理：从帧图抠底部字幕条，白字字模去重。

v2 唯一字幕来源——不依赖 SRT 文件与播放器实现。
"""

from pathlib import Path

from PIL import Image, ImageChops

BAND_Y0, BAND_Y1 = 930, 1060     # 字幕文本集中区（1080p，含双行）
BIN_TH = 190                     # 白字二值化阈值
DUP_TH = 0.05                    # 字模差异像素占比低于 5% 视为同一句（含淡入淡出微差）
BLANK_RATIO = 0.003              # 字模覆盖率低于此视为无字幕


def _text_mask(band: Image.Image) -> tuple[Image.Image, float]:
    """字幕条亮度二值化提取白色字模，返回 (mask, 覆盖率)。"""
    gray = band.convert("L")
    mask = gray.point(lambda p: 255 if p > BIN_TH else 0)
    n = mask.width * mask.height
    ratio = sum(mask.getdata()) / (255 * n)
    return mask, ratio


def crop_band(img: Image.Image) -> Image.Image:
    """整帧 → 底部字幕条。"""
    return img.crop((0, BAND_Y0, img.width, BAND_Y1))


def strips_from_images(paths: list[Path]) -> list[Image.Image]:
    """帧图序列 → 去重去空白后的字幕条列表（RGB）。

    背景动画不影响白字字模，比对用字模而非原始像素。
    """
    strips: list[Image.Image] = []
    prev_mask: Image.Image | None = None
    for p in paths:
        band = crop_band(Image.open(p))
        mask, ratio = _text_mask(band)
        if ratio < BLANK_RATIO:            # 无字幕
            continue
        if prev_mask is not None:
            diff = ImageChops.difference(mask, prev_mask)
            d = sum(diff.getdata()) / (diff.width * diff.height * 255)
            if d < DUP_TH:                 # 同一句
                continue
        prev_mask = mask
        strips.append(band.convert("RGB"))
    return strips
