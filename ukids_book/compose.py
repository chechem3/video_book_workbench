# -*- coding: utf-8 -*-
"""合成：HTML/CSS 排版 → Chromium 打印 → PDF（文字矢量、版式=改模板零 Python）。

分层：数据层（纸张 mm / 奇偶装订边距 / 每页镜头打包 / 网格列数择优）沿用 v3
口径；渲染层由 Jinja2 出单文件 HTML，常驻 Chromium 渲染线程串行出稿——
PDF 走 page.pdf() 矢量打印，pages/*.png 供 Web 逐页预览（同一 HTML 截图）。
PIL 仅存一职：主图裁掉底部字幕带并缓存（maincache/），版式与拼图不再用 PIL。
"""

import concurrent.futures
import math
import queue
import threading
from pathlib import Path

from jinja2 import Environment
from PIL import Image

from . import config

# ---- 纸张（mm，CSS 打印原生单位）与页边距 ----
PAPERS = {"a4": (210, 297), "a3": (297, 420), "a5": (148, 210)}
MARGIN_OUT_MM = 15                           # 外侧/上下
MARGIN_BIND_MM = 25                          # 装订侧（奇数页左、偶数页右）
GRID_GAP_MM = 3.5                            # 网格格间距
CAPTION_GAP_MM = 5                           # 图区-页底台词间隔
BG_LEFTOVER = 0.20                           # 单图页留白超此比例 → 模糊满版背景


def page_mm(paper: str, landscape: bool = False) -> tuple[int, int]:
    """(页宽, 页高)，单位 mm。"""
    if paper not in PAPERS:
        raise ValueError(f"未知纸张 {paper}，可选 {list(PAPERS)}")
    w, h = PAPERS[paper]
    return (h, w) if landscape else (w, h)


def pack_pages(shots: list, mode: str = "1") -> list[list]:
    """按镜头顺序切页：mode = 每页镜头数 "1" | "2" | "4"。

    一个镜头（含其全部主图与台词）永远在同一页——多主图在格内自动网格。
    """
    n = {"2": 2, "4": 4}.get(str(mode), 1)
    return [shots[i:i + n] for i in range(0, len(shots), n)]


# ---- 主图预处理：裁字幕带 + 磁盘缓存（HTML 以相对路径引用） ----

def _crop_main(path: Path, cache_dir: Path) -> Path:
    """全帧 → 裁掉底部字幕带的主图 JPEG（缓存键含裁剪比例，改配置自动失效）。"""
    ratio = config.SUBTITLE_BAND_CROP
    out = cache_dir / f"{path.stem}_c{round(ratio * 1000):03d}.jpg"
    if not out.is_file():
        cache_dir.mkdir(parents=True, exist_ok=True)
        with Image.open(path) as im:
            im.convert("RGB").crop(
                (0, 0, im.width, round(im.height * (1 - ratio)))).save(
                out, "JPEG", quality=93)
    return out


# ---- 排版规划（纯数值，供模板生成） ----

def _grid_plan(n_imgs: int, cw: float, ch: float, gap: float,
               ar: float) -> int:
    """为 n 张 ar 比例图在 cw×ch 区域选列数（行数=ceil），使单图面积最大。"""
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
    return (best or (0.0, 1))[1]


def _text_units(s: str) -> float:
    """文本宽度估算（em）：CJK≈1，其余≈0.55。"""
    return sum(1.0 if ord(c) > 0x2E80 else 0.55 for c in s)


def _est_caption_mm(lines: list[tuple[str, str]], text_w_mm: float,
                    fs_pt: float) -> float:
    """页底台词块高度估算（mm）——只用于选列数，实际高度由浏览器排版。"""
    fs_mm = fs_pt / 72 * 25.4
    lh, gap = fs_mm * 1.35, fs_mm * 0.35       # 与模板 .caption 行距/条距一致
    h = 0.0
    for en, zh in lines:
        for s in (en, zh):
            if not s.strip():
                continue
            w = _text_units(s.strip()) * fs_mm
            h += max(1, math.ceil(w / max(1.0, text_w_mm))) * lh
        h += gap
    return h + fs_mm * 0.2


def _page_data(cells: list, idx: int, total: int, pw: int, ph: int,
               cache_dir: Path | None) -> dict:
    """一页镜头格 → 模板数据。cells = [镜头格,…]，格 = [(主图路径, lines),…]。"""
    bind_left = idx % 2 == 0
    pl = MARGIN_BIND_MM if bind_left else MARGIN_OUT_MM
    pr = MARGIN_OUT_MM if bind_left else MARGIN_BIND_MM
    inner_w = pw - pl - pr
    fs_pt = max(6.5, round(inner_w * 0.021 * 72 / 25.4, 1))

    images: list[Path] = []
    lines: list[tuple[str, str]] = []
    for cell in cells:
        for path, ls in cell:
            images.append(_crop_main(path, cache_dir) if cache_dir else path)
            lines.extend(ls)

    n = len(images)
    has_cap = bool(lines)
    cap_h = _est_caption_mm(lines, inner_w - 6, fs_pt) if has_cap else 0.0
    avail_h = ph - MARGIN_OUT_MM * 2 - cap_h - (CAPTION_GAP_MM if has_cap else 0)
    ar = 1920 / 885                             # 裁带后 16:9 的实际比例
    if images:
        with Image.open(images[0]) as im:
            ar = im.width / im.height
    cols = _grid_plan(n, inner_w, avail_h, GRID_GAP_MM, ar) if n else 1
    rows = (n + cols - 1) // cols if n else 0

    # 满版背景：单图页纵向留白过多时，用模糊放大图铺底（画册常用手法）
    bg = None
    if n == 1:
        cell_w = (inner_w - GRID_GAP_MM * (cols - 1)) / cols
        ih = min((avail_h - GRID_GAP_MM * (rows - 1)) / rows, cell_w / ar)
        leftover = 1 - (rows * ih + GRID_GAP_MM * (rows - 1)) / max(avail_h, .1)
        if leftover > BG_LEFTOVER:
            bg = images[0]

    return {"idx": idx, "total": total, "bind_left": bind_left,
            "pl": pl, "pr": pr, "fs_pt": fs_pt,
            "images": images, "cols": cols, "rows": rows,
            "lines": [(en, zh) for en, zh in lines], "bg": bg}


# ---- HTML 模板 ----
# 坑位备忘（Chromium 打印）：@page margin 必须为 0、由 .page 自己 padding，否则
# 白边/串页；body 背景与页一致防亚像素缝隙；禁 backdrop-filter（打印丢内容）。

_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>
@page{size:{{ w }}mm {{ h }}mm;margin:0}
html,body{margin:0;padding:0;background:#fff;font-family:"Microsoft YaHei",sans-serif}
*{box-sizing:border-box}
.page{position:relative;width:{{ w }}mm;height:{{ h }}mm;overflow:hidden;background:#fff;
      display:flex;flex-direction:column;page-break-after:always;break-after:page}
.page.last{page-break-after:auto;break-after:auto}
.bg{position:absolute;inset:0;z-index:0;background-size:cover;background-position:center 55%;
    filter:blur(9mm) brightness(1.05);transform:scale(1.18)}
.veil{position:absolute;inset:0;z-index:1;background:rgba(255,255,255,.55)}
.grid{position:relative;z-index:2;flex:1;min-height:0;display:grid;
     grid-template-columns:repeat(var(--cols),1fr);
     grid-template-rows:repeat(var(--rows),1fr);gap:{{ gap }}mm}
.cell{min-width:0;min-height:0;overflow:hidden;display:flex;
     align-items:center;justify-content:center}
.cell img{max-width:100%;max-height:100%;object-fit:contain;display:block}
.caption{position:relative;z-index:2;margin-top:{{ cgap }}mm;line-height:1.35;
        color:#344452;min-width:0;padding:0 2mm}
.page.onbg .caption{background:rgba(255,255,255,.74);border-radius:2mm;
                   padding:2mm 3mm;margin-left:auto;width:fit-content;max-width:100%}
.caption .pair{margin:0 0 .35em}
.caption .pair:last-child{margin-bottom:0}
.caption p{margin:0}
.pageno{position:absolute;z-index:2;bottom:4mm;font-size:8.5pt;color:#787878}
</style></head><body>
{% for p in pages %}
<div class="page{% if loop.last %} last{% endif %}{% if p.bg %} onbg{% endif %}"
     style="padding:{{ mo }}mm {{ p.pr }}mm {{ mo }}mm {{ p.pl }}mm">
  {% if p.bg %}<div class="bg" style="background-image:url('{{ p.bg }}')"></div>
  <div class="veil"></div>{% endif %}
  {% if p.images %}
  <div class="grid" style="--cols:{{ p.cols }};--rows:{{ p.rows }}">
    {% for im in p.images %}<div class="cell"><img src="{{ im }}" alt=""></div>{% endfor %}
  </div>
  {% endif %}
  {% if p.lines %}
  <div class="caption" style="font-size:{{ p.fs_pt }}pt">
    {% for en, zh in p.lines %}<div class="pair">{% if en %}<p>{{ en }}</p>{% endif %}
    {%- if zh %}<p>{{ zh }}</p>{% endif %}</div>
    {%- endfor %}
  </div>
  {% endif %}
  <div class="pageno" style="{% if p.bind_left %}right{% else %}left{% endif %}:{{ mo }}mm">{{ p.idx + 1 }} / {{ p.total }}</div>
</div>
{% endfor %}
</body></html>
"""

_ENV = Environment(autoescape=True)


def build_html(pages_cells: list, paper: str = "a4", landscape: bool = False,
               tdir: Path | None = None, first_idx: int = 0,
               total: int | None = None) -> str:
    """页格列表 → 单文件 HTML。图片/背景写成相对 tdir 的 POSIX 路径。"""
    pw, ph = page_mm(paper, landscape)
    total = len(pages_cells) if total is None else total
    pages = [_page_data(c, first_idx + i, total, pw, ph,
                        tdir / "maincache" if tdir else None)
             for i, c in enumerate(pages_cells)]
    for p in pages:                              # 绝对路径 → 相对 HTML 的路径
        conv = (lambda ps: [im.relative_to(tdir).as_posix() for im in ps]) \
            if tdir else (lambda ps: [im.as_posix() for im in ps])
        p["images"] = conv(p["images"])
        p["bg"] = p["bg"].relative_to(tdir).as_posix() if p["bg"] else None
    return _ENV.from_string(_TEMPLATE).render(
        w=pw, h=ph, mo=MARGIN_OUT_MM, gap=GRID_GAP_MM, cgap=CAPTION_GAP_MM,
        pages=pages)


# ---- 常驻 Chromium 渲染线程：所有出稿/预览串行过同一浏览器 ----

class _RenderPool:
    """单工作线程持有 Playwright（sync API 绑线程），请求过队列串行。"""

    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self._th: threading.Thread | None = None
        self._lock = threading.Lock()

    def run(self, fn):
        with self._lock:
            if self._th is None:
                self._th = threading.Thread(
                    target=self._worker, daemon=True, name="chromium-render")
                self._th.start()
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._q.put((fn, fut))
        return fut

    def _worker(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError(
                "未安装 playwright：pip install playwright "
                "&& python -m playwright install chromium") from e
        pw = sync_playwright().start()
        browser = None
        while True:
            fn, fut = self._q.get()
            try:
                if browser is None:
                    browser = pw.chromium.launch()
                fut.set_result(fn(browser))
            except Exception as e:               # 崩溃即弃浏览器，下轮冷启动
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:
                        pass
                browser = None
                fut.set_exception(e)


_POOL = _RenderPool()


def _wrap_chromium_error(e: Exception) -> Exception:
    s = str(e)
    if "Executable doesn't exist" in s or " BrowserType" in s:
        raise RuntimeError(
            "Chromium 未安装：python -m playwright install chromium") from e
    return e


def render_book(html: str, tdir: Path, out_pdf: Path,
                pages_dir: Path | None = None) -> int:
    """HTML → 打印 PDF（矢量文字）+ 可选逐页 PNG（预览）。返回页数。"""
    html_path = tdir / "book.html"
    html_path.write_text(html, encoding="utf-8")

    def job(browser):
        ctx = browser.new_context(device_scale_factor=2)
        page = ctx.new_page()
        page.goto(html_path.as_uri(), wait_until="networkidle", timeout=60000)
        page.evaluate("document.fonts.ready")
        n = page.locator(".page").count()
        if n == 0:                           # 空文档防呆：宁可报错不出白卷
            raise RuntimeError("排版结果为空（模板无页面，检查 build_html 输入）")
        page.pdf(path=str(out_pdf), prefer_css_page_size=True,
                 print_background=True)
        if pages_dir:
            pages_dir.mkdir(parents=True, exist_ok=True)
            for old in pages_dir.glob("page_*.png"):
                old.unlink()
            for i in range(n):
                page.locator(".page").nth(i).screenshot(
                    path=str(pages_dir / f"page_{i + 1:03d}.png"))
        ctx.close()
        return n

    try:
        return _POOL.run(job).result()
    except Exception as e:
        raise _wrap_chromium_error(e) from None


def render_page_jpeg(html: str, tdir: Path, tag: str,
                     max_w: int = 1760) -> bytes:
    """单页 HTML → JPEG 字节（弹层实时预览，与出稿同一模板）。"""
    html_path = tdir / f"preview_{tag}.html"
    html_path.write_text(html, encoding="utf-8")

    def job(browser):
        ctx = browser.new_context(device_scale_factor=2)
        page = ctx.new_page()
        page.goto(html_path.as_uri(), wait_until="networkidle", timeout=60000)
        page.evaluate("document.fonts.ready")
        if page.locator(".page").count() == 0:
            raise RuntimeError("排版结果为空（模板无页面，检查 build_html 输入）")
        png = page.locator(".page").first.screenshot(timeout=20000)
        ctx.close()
        html_path.unlink(missing_ok=True)
        return png

    try:
        png = _POOL.run(job).result()
    except Exception as e:
        raise _wrap_chromium_error(e) from None
    import io
    img = Image.open(io.BytesIO(png))
    img.thumbnail((max_w, 8000))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=88)
    return buf.getvalue()
