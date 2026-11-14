# -*- coding: utf-8 -*-
"""合成：主图（裁掉底部字幕带）+ 台词文本（OCR 预填 + 人工校对）排版 → 长图 / PDF。"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config

# ---- 纸张（300dpi）与页边距 ----
PAPERS = {"a4": (2480, 3508), "a3": (3508, 4961), "a5": (1748, 2480)}
MARGIN_OUT = 177                            # 15mm 外侧/上下
MARGIN_BIND = 296                           # 25mm 装订侧


def page_geom(paper: str = "a4", landscape: bool = False) -> tuple[int, int]:
    """(页宽, 页高)。"""
    if paper not in PAPERS:
        raise ValueError(f"未知纸张 {paper}，可选 {list(PAPERS)}")
    w, h = PAPERS[paper]
    return (h, w) if landscape else (w, h)


def _font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(config.FONT_CJK, size)
    except OSError:
        return ImageFont.load_default()


def _page_layout(page_idx: int, pw: int) -> tuple[int, int]:
    """(左边距, 右边距)：奇数页(第1,3..页)装订在左，偶数页在右（双面打印）。"""
    bind_left = page_idx % 2 == 0           # 0-based：第1页左边装订
    return (MARGIN_BIND if bind_left else MARGIN_OUT,
            MARGIN_OUT if bind_left else MARGIN_BIND)


def _page_number(page: Image.Image, idx: int, total: int,
                 pw: int, ph: int) -> None:
    """页码在外下角，避开装订边。"""
    d = ImageDraw.Draw(page)
    text = f"{idx + 1} / {total}"
    font = _font(44)
    w = d.textlength(text, font=font)
    left, _ = _page_layout(idx, pw)
    x = (pw - MARGIN_OUT - w) if left == MARGIN_BIND else MARGIN_OUT
    d.text((x, ph - MARGIN_OUT + 40), text, font=font, fill=(120, 120, 120))


# ---- 台词文本排版 ----

TEXT_FILL = (52, 68, 82)


def _wrap(d: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    """逐字符累加折行（中英混排通用）。"""
    lines, cur = [], ""
    for ch in text:
        if d.textlength(cur + ch, font=font) <= max_w or not cur:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines or [""]


def _layout_texts(lines: list[tuple[str, str]], width: int, fs: int, pad: int,
                  compact: bool = False):
    """台词 [(en, zh), …] → [(行文本, y)] 与文本块总高；en/zh 各占段、分别折行。

    compact=True 为页底统一台词块的紧凑排版（行距 1.35、条间距 0.35）。
    """
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    font = _font(fs)
    lh = int(fs * (1.35 if compact else 1.5))
    gap_line = int(fs * (0.35 if compact else 0.55))
    rows, y = [], int(fs * 0.35 if compact else int(fs * 0.6))
    for en, zh in lines:
        for p in (en, zh):
            if not p or not p.strip():
                continue
            for ln in _wrap(d, p.strip(), font, width - pad * 2):
                rows.append((ln, y))
                y += lh
        y += gap_line
    return rows, y + int(fs * 0.2 if compact else int(fs * 0.4))


def _draw_texts(canvas: Image.Image, rows, y0: int, pad: int, fs: int) -> None:
    d = ImageDraw.Draw(canvas)
    font = _font(fs)
    for ln, y in rows:
        d.text((pad, y0 + y), ln, font=font, fill=TEXT_FILL)


def _fs_of(width: int) -> int:
    return max(16, round(width * 0.021))


def render_entry_text(img: Image.Image, lines: list[tuple[str, str]],
                      width: int) -> Image.Image:
    """主图（裁底部字幕带）+ 台词文本块。"""
    scale = width / img.width
    img = img.resize((width, int(img.height * scale)), Image.LANCZOS)
    fs, pad = _fs_of(width), round(width * 0.04)
    rows, th = _layout_texts(lines, width, fs, pad)
    entry = Image.new("RGB", (width, img.height + th), "white")
    entry.paste(img, (0, 0))
    _draw_texts(entry, rows, img.height, pad, fs)
    return entry


def compose_book_text(entries: list[tuple[Path, list[tuple[str, str]]]],
                      out_png: Path, width: int = config.PAGE_WIDTH,
                      gap: int = config.SEG_GAP,
                      crop_ratio: float = config.SUBTITLE_BAND_CROP) -> Path:
    """entries: [(主图路径, [(en, zh), …])] → 纵向长图。"""
    out_png.parent.mkdir(parents=True, exist_ok=True)
    blocks = []
    for frame_path, texts in entries:
        img = Image.open(frame_path).convert("RGB")
        w, h = img.size
        img = img.crop((0, 0, w, int(h * (1 - crop_ratio))))
        blocks.append(render_entry_text(img, texts, width))
    total_h = sum(b.height for b in blocks) + gap * (len(blocks) - 1)
    book = Image.new("RGB", (width, max(1, total_h)), "white")
    y = 0
    for b in blocks:
        book.paste(b, (0, y))
        y += b.height + gap
    book.save(out_png)
    return out_png


def render_page_text(img: Image.Image, lines: list[tuple[str, str]],
                     page_idx: int, total: int, pw: int, ph: int) -> Image.Image:
    """单页：主图 + 台词文本块，内容垂直居中。"""
    page = Image.new("RGB", (pw, ph), "white")
    left, right = _page_layout(page_idx, pw)
    inner_w = pw - left - right
    scale = inner_w / img.width
    img = img.resize((inner_w, int(img.height * scale)), Image.LANCZOS)
    fs, pad = _fs_of(inner_w), round(inner_w * 0.045)
    rows, th = _layout_texts(lines, inner_w, fs, pad)
    content_h = img.height + th
    y_img = MARGIN_OUT + max(0, (ph - 2 * MARGIN_OUT - content_h) // 2)
    page.paste(img, (left, y_img))
    _draw_texts(page, rows, y_img + img.height, left + pad, fs)
    _page_number(page, page_idx, total, pw, ph)
    return page


def compose_pdf_text(entries: list[tuple[Path, list[tuple[str, str]]]],
                     out_pdf: Path, pages_dir: Path | None = None,
                     crop_ratio: float = config.SUBTITLE_BAND_CROP,
                     paper: str = "a4", landscape: bool = False) -> Path:
    """文本台词版 PDF：每段一页带页码。"""
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    pw, ph = page_geom(paper, landscape)
    pages = []
    n = len(entries)
    for i, (frame_path, texts) in enumerate(entries):
        img = Image.open(frame_path).convert("RGB")
        w, h = img.size
        img = img.crop((0, 0, w, int(h * (1 - crop_ratio))))
        page = render_page_text(img, texts, i, n, pw, ph)
        pages.append(page)
        if pages_dir:
            pages_dir.mkdir(parents=True, exist_ok=True)
            page.save(pages_dir / f"page_{i+1:03d}.png")
    pages[0].save(out_pdf, save_all=True, append_images=pages[1:],
                  resolution=300)
    return out_pdf


# ---- 多镜头同页（页面打包） ----

def pack_pages(shots: list, mode: str = "1") -> list[list]:
    """按镜头顺序切页：mode = 每页镜头数 "1" | "2" | "4"。

    一个镜头（含其全部主图与台词）永远在同一页——多主图在格子内纵向堆叠。
    """
    n = {"2": 2, "4": 4}.get(str(mode), 1)
    return [shots[i:i + n] for i in range(0, len(shots), n)]


def _grid_plan(n_imgs: int, cw: int, ch: int, gap: int,
               ar: float = 16 / 9) -> int:
    """为 n 张 ar 比例图在 cw×ch 区域选列数（行数=ceil），使单图面积最大。

    自动适配区域形状：横版 2 图选左右、4 图选田字；纵版高区域
    可能选单列——哪种排法图更大用哪种。
    """
    best = None
    for cols in range(1, n_imgs + 1):
        rows = (n_imgs + cols - 1) // cols
        scw = (cw - gap * (cols - 1)) / cols
        sch = (ch - gap * (rows - 1)) / rows
        if scw <= 0 or sch <= 0:
            continue
        w = min(scw, sch * ar)                 # 保持比例能放进子格的宽
        area = w * (w / ar)
        if best is None or area > best[0]:
            best = (area, cols)
    return (best or (0, 1))[1]


def render_page_multi(cells: list, page_idx: int, total: int,
                      pw: int, ph: int) -> Image.Image:
    """整页统一排版：全部主图网格铺在上部（择优列数），台词紧凑排在页底。

    cells = 每格一个镜头的 [(img, lines), …]；台词收集整页所有镜头的，
    顺序拼接，统一画在页面底部（页码上方）。空间利用紧凑：网格按图片
    实际纵横比规划（满格不留白），格间距/图词间隔收紧。
    """
    images: list[Image.Image] = []
    lines: list[tuple[str, str]] = []
    for cell in cells:
        for img, l in cell:
            images.append(img)
            lines.extend(l)

    page = Image.new("RGB", (pw, ph), "white")
    left, right = _page_layout(page_idx, pw)
    inner_w = pw - left - right
    pad = round(inner_w * 0.04)
    fs = max(round(inner_w * 0.021), 24)

    # ① 台词块：紧凑排版，锚定页底（页码上方少量留白）
    rows, th = _layout_texts(lines, inner_w, fs, pad, compact=True)
    y_text = max(MARGIN_OUT, ph - MARGIN_OUT - 55 - th)

    # ② 图片网格：占据台词以上的区域；按图片实际纵横比规划，满格铺
    gap = max(40, int(fs * 1.0))
    ar = images[0].width / images[0].height if images else 16 / 9
    area_h = y_text - MARGIN_OUT - int(fs * 1.5)
    cols = _grid_plan(len(images), inner_w, area_h, gap, ar)
    g_rows = (len(images) + cols - 1) // cols
    scw = (inner_w - gap * (cols - 1)) // cols
    sch = (area_h - gap * (g_rows - 1)) // g_rows
    # 整组网格在区域内垂直居中（比例吻合时基本满格，残差均分）
    ih = min(sch, round(scw / ar))
    used_h = g_rows * ih + gap * (g_rows - 1)
    y_off = MARGIN_OUT + max(0, (area_h - used_h) // 2)
    for i, img in enumerate(images):
        r, c = divmod(i, cols)
        cx = left + c * (scw + gap)
        cy = y_off + r * (ih + gap)
        scale = scw / img.width
        iw, ih2 = scw, round(img.height * scale)
        if ih2 > sch:                           # 个别图比例略偏时兜底
            ih2 = sch
            iw = round(img.width * (sch / img.height))
        page.paste(img.resize((iw, ih2), Image.LANCZOS),
                   (cx + (scw - iw) // 2, cy + (ih - ih2) // 2))

    # ③ 页底台词 + 页码
    _draw_texts(page, rows, y_text, left + pad, fs)
    _page_number(page, page_idx, total, pw, ph)
    return page


def compose_pdf_multi(pages: list, out_pdf: Path,
                      pages_dir: Path | None = None,
                      crop_ratio: float = config.SUBTITLE_BAND_CROP,
                      paper: str = "a4", landscape: bool = False) -> Path:
    """按镜头页渲染 PDF。pages: [[镜头格…], …]，格 = [(img_path, lines), …]。"""
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    pw, ph = page_geom(paper, landscape)
    imgs = []
    n = len(pages)
    for i, cells in enumerate(pages):
        blocks = []
        for cell in cells:
            b = []
            for path, lines in cell:
                img = Image.open(path).convert("RGB")
                w, h = img.size
                b.append((img.crop((0, 0, w, int(h * (1 - crop_ratio)))),
                          lines))
            blocks.append(b)
        page_img = render_page_multi(blocks, i, n, pw, ph)
        imgs.append(page_img)
        if pages_dir:
            pages_dir.mkdir(parents=True, exist_ok=True)
            page_img.save(pages_dir / f"page_{i+1:03d}.png")
    imgs[0].save(out_pdf, save_all=True, append_images=imgs[1:],
                 resolution=300)
    return out_pdf
