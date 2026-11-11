# -*- coding: utf-8 -*-
"""合成：主图（裁掉底部字幕带）+ 字幕条 → 长图 / A4 PDF（带页码）。"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config

# ---- A4 画册（300dpi，双面打印装订边交替） ----
A4_W, A4_H = 2480, 3508                    # 210×297mm @300dpi
MARGIN_OUT = 177                            # 15mm 外侧/上下
MARGIN_BIND = 296                           # 25mm 装订侧


def _font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(config.FONT_CJK, size)
    except OSError:
        return ImageFont.load_default()


def _page_layout(page_idx: int) -> tuple[int, int]:
    """(左边距, 右边距)：奇数页(第1,3..页)装订在左，偶数页在右（双面打印）。"""
    bind_left = page_idx % 2 == 0           # 0-based：第1页左边装订
    return (MARGIN_BIND if bind_left else MARGIN_OUT,
            MARGIN_OUT if bind_left else MARGIN_BIND)


def _page_number(page: Image.Image, idx: int, total: int) -> None:
    """页码在外下角，避开装订边。"""
    d = ImageDraw.Draw(page)
    text = f"{idx + 1} / {total}"
    font = _font(44)
    w = d.textlength(text, font=font)
    left, _ = _page_layout(idx)
    x = (A4_W - MARGIN_OUT - w) if left == MARGIN_BIND else MARGIN_OUT
    d.text((x, A4_H - MARGIN_OUT + 40), text, font=font, fill=(120, 120, 120))


# ---- 长图 ----

def render_entry_band(img: Image.Image, strips: list[Image.Image],
                      width: int, gap: int = 8, pad: int = 12) -> Image.Image:
    """完整关键帧（裁底部字幕带）+ 字幕条。"""
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
                      gap: int = config.SEG_GAP,
                      crop_ratio: float = config.SUBTITLE_BAND_CROP) -> Path:
    """entries: [(主图路径, 字幕条列表)] → 纵向长图。"""
    out_png.parent.mkdir(parents=True, exist_ok=True)
    blocks = []
    for frame_path, strips in entries:
        img = Image.open(frame_path).convert("RGB")
        w, h = img.size
        img = img.crop((0, 0, w, int(h * (1 - crop_ratio))))
        blocks.append(render_entry_band(img, strips, width))
    total_h = sum(b.height for b in blocks) + gap * (len(blocks) - 1)
    book = Image.new("RGB", (width, total_h), "white")
    y = 0
    for b in blocks:
        book.paste(b, (0, y))
        y += b.height + gap
    book.save(out_png)
    return out_png


# ---- A4 PDF ----

def render_page_band(img: Image.Image, strips: list[Image.Image],
                     page_idx: int, total: int) -> Image.Image:
    """A4 单页：主图（裁字幕带）+ 字幕条，内容垂直居中。"""
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
    _page_number(page, page_idx, total)
    return page


def compose_pdf_band(entries: list[tuple[Path, list[Image.Image]]],
                     out_pdf: Path, pages_dir: Path | None = None,
                     crop_ratio: float = config.SUBTITLE_BAND_CROP) -> Path:
    """每段一页 A4 带页码；pages_dir 同时落 PNG 便于检查。"""
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    pages = []
    n = len(entries)
    for i, (frame_path, strips) in enumerate(entries):
        img = Image.open(frame_path).convert("RGB")
        w, h = img.size
        img = img.crop((0, 0, w, int(h * (1 - crop_ratio))))
        page = render_page_band(img, strips, i, n)
        pages.append(page)
        if pages_dir:
            pages_dir.mkdir(parents=True, exist_ok=True)
            page.save(pages_dir / f"page_{i+1:03d}.png")
    pages[0].save(out_pdf, save_all=True, append_images=pages[1:],
                  resolution=300)
    return out_pdf
