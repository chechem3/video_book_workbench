# -*- coding: utf-8 -*-
"""字幕 OCR 与台词库：分行识别 → subs.json（OCR 预填 + 人工校对，人工值永不被覆盖）。

字幕带布局（1080p 基准，y=930-1060 共 130px）：英文行约 4%-32%，中文行约 52%-81%，
中间隔带——分而行之再放大识别，避免双行挤在一起互相干扰。
subs.json: {帧名: {"en": ..., "zh": ...}}（旧版 "EN / ZH" 字符串自动迁移）。
"""

import json
import re
from pathlib import Path

import numpy as np
from PIL import Image

from . import config
from .subtitles import _band_of, _text_mask

CACHE_NAME = "subs.json"
_EN_TOP, _EN_BOTTOM = 0.0, 0.45      # 英文行（相对带高）
_ZH_TOP = 0.45                        # 中文行

_engine = None
_dml_patched = False


def _patch_dml() -> bool:
    """把 RapidOCR 内部的 onnxruntime session 切到 DirectML（GPU）。

    rapidocr 只原生支持 cpu/cuda；这里在引擎创建前替换其 utils 命名空间里的
    InferenceSession，忽略传入的 providers 固定注入 DML（CPU 兜底）。
    """
    global _dml_patched
    if _dml_patched:
        return True
    try:
        import onnxruntime as ort
        if "DmlExecutionProvider" not in ort.get_available_providers():
            return False                        # 未装 onnxruntime-directml
        from rapidocr_onnxruntime import utils as ru
        _orig = ru.InferenceSession

        def _dml_session(path, sess_options=None, providers=None, **kw):
            return _orig(path, sess_options=sess_options,
                         providers=["DmlExecutionProvider",
                                    "CPUExecutionProvider"], **kw)

        ru.InferenceSession = _dml_session
        _dml_patched = True
        return True
    except Exception:
        return False


def _eng():
    global _engine
    if _engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as e:
            raise RuntimeError(
                "未安装 OCR 引擎：pip install rapidocr-onnxruntime") from e
        if config.OCR_DEVICE == "dml":
            _patch_dml()
        _engine = RapidOCR()
    return _engine


def _fix_spaces(s: str) -> str:
    """轻修 OCR 空格丢失：小写→大写交界插空格（如 Therearesome → There are some）。"""
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    s = re.sub(r"([,.!?])(?=[A-Za-z\u4e00-\u9fff])", r"\1 ", s)
    return s.strip()


_PLAYER_UI = re.compile(
    r"选集|倍速|顺序播放|单集循环|双语字幕|全屏|下一集|\d{1,2}:\d{2}\s*/\s*\d{1,2}:\d{2}")
_WATERMARK = re.compile(r"剧审网字|[\(（]\s*20\d{2}\s*[\)）]")


def _clean_text(s: str) -> str:
    """识别结果清洗：剔水印片段；播放器 UI 文字（进度/倍速/选集）整行丢弃。

    录制中途碰过屏幕的帧，控制条会遮住字幕，识别出的只会是 UI 文字；
    片源水印（剧审网字+年份）常拼在台词尾部，只剔片段不丢整行。
    """
    if _PLAYER_UI.search(s):
        return ""
    return _WATERMARK.sub("", s).strip(" )）.、，,")


def _ocr_img(img: Image.Image, scale: int = 2) -> str:
    big = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
    result, _ = _eng()(np.asarray(big))
    if not result:
        return ""
    return _clean_text(_fix_spaces(" ".join(r[1] for r in result)))


def _soft(img: Image.Image) -> Image.Image:
    """亮部（字幕）保留、背景压暗——保边缘反锯齿，针对文字压在视频画面上的行。"""
    return img.point(lambda v: v if v > 232 else int(v * 0.35))


def _maskbin(img: Image.Image) -> Image.Image:
    """字模二值化 → 黑字白底（描边判据剥离花背景，最兜底）。"""
    from PIL import ImageOps
    mask, _ = _text_mask(img)
    return ImageOps.invert(mask.convert("L"))


def _ocr_line(img: Image.Image) -> str:
    """单行级联识别：灰度2x（空格最全）→ 压暗2x → 字模2x → 字模3x → 压暗3x。

    逐级兜底到出字为止；深层只对失败行执行，整体耗时可控。
    实测（NeedFOrBlazingSpeed_1）：EN 缺失 20→0，ZH 缺失 50→3。
    """
    if img.width < 10 or img.height < 6:
        return ""
    g = img.convert("L")
    for prep, k in ((lambda x: x, 2), (_soft, 2), (_maskbin, 2),
                    (_maskbin, 3), (_soft, 3)):
        try:
            t = _ocr_img(prep(g), k)
        except Exception:
            t = ""
        if t:
            return t
    return ""


def recognize_band(band: Image.Image) -> dict:
    """整条字幕带 → {"en":…, "zh":…}（分行 + 级联识别）。"""
    w, h = band.size
    en = _ocr_line(band.crop((0, int(h * _EN_TOP), w, int(h * _EN_BOTTOM))))
    zh = _ocr_line(band.crop((0, int(h * _ZH_TOP), w, h)))
    return {"en": en, "zh": zh}


def _load_cache(task_dir: Path) -> dict:
    """读 subs.json；旧版 'EN / ZH' 字符串条目迁移为 {en, zh}。"""
    p = task_dir / CACHE_NAME
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    for k, v in raw.items():
        if isinstance(v, str):                  # 旧格式迁移
            en, _, zh = v.partition(" / ")
            raw[k] = {"en": en, "zh": zh}
    return raw


def _save_cache(task_dir: Path, cache: dict) -> None:
    (task_dir / CACHE_NAME).write_text(
        json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def _is_user(entry) -> bool:
    return isinstance(entry, dict) and entry.get("man")


def ensure_subs(task_dir: Path, names: list[str], progress=None,
                retry: bool = False) -> dict:
    """OCR 补识别。

    默认只识别缓存中不存在的帧（人工校对值、已识别值永不覆盖）；
    retry=True（「识别缺失」按钮）额外重试「OCR 识别为空且非人工清空」的条目。
    """
    cache = _load_cache(task_dir)
    strips = task_dir / "strips"
    todo = [n for n in names
            if n not in cache
            or (retry and not _is_user(cache[n])
                and not (cache[n].get("en") or cache[n].get("zh")))]
    for i, n in enumerate(todo, 1):
        p = strips / n
        old = cache.get(n) or {}
        try:
            got = recognize_band(_band_of(p)) if p.is_file() \
                else {"en": "", "zh": ""}
        except Exception:
            got = {"en": "", "zh": ""}
        got = {"en": got["en"] or old.get("en", ""),
               "zh": got["zh"] or old.get("zh", "")}
        if _is_user(old):
            got = old                          # 保险：人工值绝不覆盖
        cache[n] = got
        if progress:
            progress(i, len(todo), f"识别 {i}/{len(todo)}")
        if i % 10 == 0:                    # 中途落盘，中断不亏
            _save_cache(task_dir, cache)
    if todo:
        _save_cache(task_dir, cache)
    return {n: {k: cache.get(n, {}).get(k, "") for k in ("en", "zh")}
            for n in names}


def save_texts(task_dir: Path, texts: dict) -> dict:
    """合并人工校对的台词 {帧名: {"en","zh"}}。

    语义：输入非空 = 人工值（man 标记，重识别永不覆盖）；
    清空 = 解除保护（「识别缺失」会重试该行）；彻底去掉一行请删帧。
    """
    cache = _load_cache(task_dir)
    for n, t in texts.items():
        en, zh = str(t.get("en", "")), str(t.get("zh", ""))
        entry = {"en": en, "zh": zh}
        if en or zh:
            entry["man"] = 1
        cache[n] = entry
    _save_cache(task_dir, cache)
    return cache


def cached_texts(task_dir: Path, names: list[str] | None = None) -> dict:
    """只读缓存（预览用，不触发识别）；输出不含 man 标记。"""
    cache = _load_cache(task_dir)
    out = {n: {k: v.get(k, "") for k in ("en", "zh")}
           for n, v in cache.items() if isinstance(v, dict)}
    if names is None:
        return out
    return {n: out[n] for n in names if n in out}


_PUNCT = re.compile(r"[\W_]+", re.UNICODE)     # 空白+全部标点（CJK 字符属 \w 保留）


def norm_line(t: dict) -> str:
    """台词归一化（去空白/标点、英文小写）——同句判等用。"""
    return _PUNCT.sub("", (t.get("en") or "").lower()) or \
        _PUNCT.sub("", t.get("zh") or "")


def build_lines(texts: dict, others: list[str]) -> list[tuple[str, str, str]]:
    """按帧序出台词行，**相邻同句去重**（文本归一化相等只保留首条）。

    返回 [(en, zh, 帧名)]——帧名供多主图场景把台词归属到最近的主图。
    """
    lines: list[tuple[str, str, str]] = []
    prev_norm = ""
    for f in others:
        t = texts.get(f)
        if not t or not (t.get("en") or t.get("zh")):
            continue
        nm = norm_line(t)
        if nm and nm == prev_norm:
            continue                        # 与上一条同句 → 只保留首条
        prev_norm = nm
        lines.append((t.get("en", ""), t.get("zh", ""), f))
    return lines


def borrow_missing(task_dir: Path, others: list[str]) -> None:
    """相邻帧借词：某行被播放器控制条遮住时，从邻帧借对应语言。

    条件：邻帧另一种语言与 本帧完全相等（同一句的强信号）且目标语言非空。
    """
    cache = _load_cache(task_dir)
    changed = False
    for i, n in enumerate(others):
        t = cache.get(n)
        if not isinstance(t, dict) or t.get("man"):
            continue
        for me, peer, other in (("en", "zh", "en"), ("zh", "en", "zh")):
            if t.get(me) and not t.get(peer):
                for j in (i - 1, i + 1):
                    if 0 <= j < len(others):
                        nt = cache.get(others[j])
                        if isinstance(nt, dict) and nt.get(peer) \
                                and nt.get(other) == t.get(me):
                            t[peer] = nt[peer]
                            changed = True
                            break
    if changed:
        _save_cache(task_dir, cache)
