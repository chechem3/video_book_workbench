# -*- coding: utf-8 -*-
"""合成：关键帧（裁掉底部字幕带）+ SRT 双语台词 → 纵向长图。"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config
from .subtitles import Cue


def _font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(config.FONT_CJK, size)
    except OSError:
        return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    """中文按字符折行，英文按词，混排按字符兜底。"""
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) <= max_w:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines or [""]


def render_entry(img: Image.Image, cues: list[Cue], width: int,
                 crop_ratio: float, pad: int = 24) -> Image.Image:
    """单段：帧（裁底部字幕带，缩放到 width）+ 台词文本，返回该段的竖条图。"""
    w, h = img.size
    img = img.crop((0, 0, w, int(h * (1 - crop_ratio))))
    scale = width / img.width
    img = img.resize((width, int(img.height * scale)), Image.LANCZOS)

    inner = width - pad * 2
    draw = ImageDraw.Draw(img)
    zh_f, en_f = _font(34), _font(26)
    lh_zh, lh_en = 48, 38
    rows: list[tuple[str, object, int]] = []
    for c in cues:
        for ln in _wrap(draw, c.zh, zh_f, inner):
            rows.append((ln, zh_f, lh_zh))
        for ln in _wrap(draw, c.en, en_f, inner):
            rows.append((ln, en_f, lh_en))
        rows.append(("", en_f, 18))          # 台词间空行
    text_h = sum(r[2] for r in rows) + pad * 2 if rows else 0

    entry = Image.new("RGB", (width, img.height + text_h), "white")
    entry.paste(img, (0, 0))
    d = ImageDraw.Draw(entry)
    y = img.height + pad
    for text, font, lh in rows:
        d.text((pad, y), text, font=font, fill=(30, 30, 30))
        y += lh
    return entry


def compose_book(entries: list[tuple[Path, list[Cue]]], out_png: Path,
                 width: int = config.PAGE_WIDTH, gap: int = config.SEG_GAP,
                 crop_ratio: float = config.SUBTITLE_BAND_CROP) -> Path:
    """entries: [(关键帧路径, 该段台词列表)] → 纵向长图。"""
    out_png.parent.mkdir(parents=True, exist_ok=True)
    blocks = []
    for frame_path, cues in entries:
        img = Image.open(frame_path).convert("RGB")
        blocks.append(render_entry(img, cues, width, crop_ratio))
    total_h = sum(b.height for b in blocks) + gap * (len(blocks) - 1)
    book = Image.new("RGB", (width, total_h), "white")
    y = 0
    for b in blocks:
        book.paste(b, (0, y))
        y += b.height + gap
    book.save(out_png)
    return out_png


# ---- A4 画册 PDF（300dpi，双面打印装订边交替） ----
A4_W, A4_H = 2480, 3508                    # 210×297mm @300dpi
MARGIN_OUT = 177                            # 15mm 外侧/上下
MARGIN_BIND = 296                           # 25mm 装订侧
ZH_SIZE, EN_SIZE = 54, 78                   # 中文字小一点，英文字大一点
ZH_LH, EN_LH = 78, 108


def _page_layout(page_idx: int) -> tuple[int, int]:
    """(左边距, 右边距)：奇数页(第1,3..页)装订在左，偶数页在右（双面打印）。"""
    bind_left = page_idx % 2 == 0           # 0-based：第1页左边装订
    left = MARGIN_BIND if bind_left else MARGIN_OUT
    right = MARGIN_OUT if bind_left else MARGIN_BIND
    return left, right


def render_page(img: Image.Image, cues: list[Cue], page_idx: int,
                crop_ratio: float = config.SUBTITLE_BAND_CROP) -> Image.Image:
    """A4 单页：帧在上（满可用宽），台词在下；内容垂直居中，超高自动缩字号。"""
    page = Image.new("RGB", (A4_W, A4_H), "white")
    left, right = _page_layout(page_idx)
    inner_w = A4_W - left - right

    w, h = img.size
    img = img.crop((0, 0, w, int(h * (1 - crop_ratio))))
    scale = inner_w / img.width
    img = img.resize((inner_w, int(img.height * scale)), Image.LANCZOS)

    gap = 90
    avail = A4_H - 2 * MARGIN_OUT - img.height - gap
    fscale = 1.0
    for _ in range(8):                      # 台词过多则逐级缩字号
        rows = _rows(cues, inner_w, fscale)
        text_h = sum(lh for _, _, lh in rows)
        if text_h <= avail or fscale <= 0.55:
            break
        fscale *= 0.9
    content_h = img.height + gap + min(text_h, avail)
    y_img = MARGIN_OUT + max(0, (A4_H - 2 * MARGIN_OUT - content_h) // 2)
    page.paste(img, (left, y_img))

    d = ImageDraw.Draw(page)
    y = y_img + img.height + gap
    for text, font_size, lh in rows:
        if y + lh > A4_H - MARGIN_OUT:
            break
        if text:
            d.text((left, y), text, font=_font(int(font_size)), fill=(30, 30, 30))
        y += lh
    return page


def _rows(cues: list[Cue], inner_w: int, scale: float) -> list[tuple]:
    """折行后的文本行: (text, font_size, line_height)。"""
    zh_s, en_s = int(ZH_SIZE * scale), int(EN_SIZE * scale)
    zh_lh, en_lh = int(ZH_LH * scale), int(EN_LH * scale)
    tmp = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    rows = []
    for c in cues:
        f_zh, f_en = _font(zh_s), _font(en_s)
        for ln in _wrap(tmp, c.zh, f_zh, inner_w):
            rows.append((ln, zh_s, zh_lh))
        for ln in _wrap(tmp, c.en, f_en, inner_w):
            rows.append((ln, en_s, en_lh))
        rows.append(("", en_s, int(44 * scale)))    # 台词间空行
    return rows


def compose_pdf(entries: list[tuple[Path, list[Cue]]], out_pdf: Path,
                pages_dir: Path | None = None,
                crop_ratio: float = config.SUBTITLE_BAND_CROP) -> Path:
    """每段一页 A4，输出多页 PDF；pages_dir 同时落 PNG 便于检查。"""
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    pages = []
    for i, (frame_path, cues) in enumerate(entries):
        img = Image.open(frame_path).convert("RGB")
        page = render_page(img, cues, i, crop_ratio)
        pages.append(page)
        if pages_dir:
            pages_dir.mkdir(parents=True, exist_ok=True)
            page.save(pages_dir / f"page_{i+1:03d}.png")
    pages[0].save(out_pdf, save_all=True, append_images=pages[1:],
                  resolution=300)
    return out_pdf


# ---- 字幕带切片模式：完整帧 + 底部字幕条拼接（不依赖 SRT） ----

def render_entry_band(img: Image.Image, strips: list[Image.Image],
                      width: int, gap: int = 8, pad: int = 12) -> Image.Image:
    """完整关键帧（不裁剪）+ 去重后的字幕条。"""
    scale = width / img.width
    img = img.resize((width, int(img.height * scale)), Image.LANCZOS)
    sh = int(strips[0].height * width / strips[0].width) if strips else 0
    total = img.height + pad * 2 + (sh + gap) * len(strips)
    entry = Image.new("RGB", (width, total), "white")
    entry.paste(img, (0, 0))
    y = img.height + pad
    for s in strips:
        entry.paste(s.resize((width, sh), Image.LANCZOS), (0, y))
        y += sh + gap
    return entry


def compose_book_band(entries: list[tuple[Path, list[Image.Image]]],
                      out_png: Path, width: int = config.PAGE_WIDTH,
                      gap: int = config.SEG_GAP) -> Path:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    blocks = []
    for frame_path, strips in entries:
        img = Image.open(frame_path).convert("RGB")
        blocks.append(render_entry_band(img, strips, width))
    total_h = sum(b.height for b in blocks) + gap * (len(blocks) - 1)
    book = Image.new("RGB", (width, total_h), "white")
    y = 0
    for b in blocks:
        book.paste(b, (0, y))
        y += b.height + gap
    book.save(out_png)
    return out_png


def render_page_band(img: Image.Image, strips: list[Image.Image],
                     page_idx: int) -> Image.Image:
    """A4 单页（band 模式）：完整帧 + 字幕条，内容垂直居中。"""
    page = Image.new("RGB", (A4_W, A4_H), "white")
    left, right = _page_layout(page_idx)
    inner_w = A4_W - left - right
    scale = inner_w / img.width
    img = img.resize((inner_w, int(img.height * scale)), Image.LANCZOS)
    gap, sgap = 80, 20
    heights, ys = [], []
    y = 0
    for s in strips:
        sh = int(s.height * inner_w / s.width)
        heights.append(sh)
        ys.append(y)
        y += sh + sgap
    content_h = img.height + gap + y - (sgap if strips else 0)
    y_img = MARGIN_OUT + max(0, (A4_H - 2 * MARGIN_OUT - content_h) // 2)
    page.paste(img, (left, y_img))
    for s, sh, dy in zip(strips, heights, ys):
        page.paste(s.resize((inner_w, sh), Image.LANCZOS),
                   (left, y_img + img.height + gap + dy))
    return page


def compose_pdf_band(entries: list[tuple[Path, list[Image.Image]]],
                     out_pdf: Path, pages_dir: Path | None = None) -> Path:
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    pages = []
    for i, (frame_path, strips) in enumerate(entries):
        img = Image.open(frame_path).convert("RGB")
        page = render_page_band(img, strips, i)
        pages.append(page)
        if pages_dir:
            pages_dir.mkdir(parents=True, exist_ok=True)
            page.save(pages_dir / f"page_{i+1:03d}.png")
    pages[0].save(out_pdf, save_all=True, append_images=pages[1:],
                  resolution=300)
    return out_pdf
