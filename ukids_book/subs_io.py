# -*- coding: utf-8 -*-
"""台词导出/导回：OCR 初稿 → 编号清单交给 AI 校对 → 结果粘贴导回 subs.json。

导出格式（与出稿 build_lines 完全同源：逐镜头相邻同句去重，镜头内首条为准）：

    #001 f000812
    EN There are some clouds.
    ZH 天上有云。

「#编号 帧名」行是回写锚点（帧名是 subs.json 的键）；导回时按帧名落盘，
编号仅兜底（AI 丢了帧名时按当前清单顺序对号）。解析只认这三类行，
AI 回复里夹带的说明文字一律忽略。导回的值走 save_texts：非空即 man 标记，
永不被重识别覆盖；空条目跳过不落盘。
"""

import datetime as dtm
import re

from . import ocr

_HEAD = re.compile(
    r"^#\s*(\d{1,4})(?:\s+([A-Za-z0-9_.\-]+))?\s*(?:[|：:#].*)?$")
_FIELD = re.compile(r"^(EN|ZH)(?![A-Za-z])\s*[:：|]?\s?(.*)$", re.I)


def book_lines(doc: dict, texts: dict) -> list[tuple[str, str, str]]:
    """全部镜头的出稿台词平铺：[(en, zh, 帧名)]（与出稿同规则）。"""
    out: list[tuple[str, str, str]] = []
    for sh in doc["shots"]:
        out.extend(ocr.build_lines(texts, sh["others"]))
    return out


def build_export(label: str, doc: dict, texts: dict) -> tuple[str, list[str]]:
    """台词 → 交给 AI 的校对清单；返回 (文本, 帧名列表)。"""
    lines = book_lines(doc, texts)
    head = (
        f"# 小小优趣画册 · AI 台词校对\n"
        f"# 任务：{label} · {len(lines)} 条 · {dtm.date.today().isoformat()}\n"
        f"# —— 以下说明请连同数据一起交给 AI ——\n"
        f"# 这是 OCR 生成的视频双语字幕初稿，请逐条校对：\n"
        f"# 1) EN：修正识别错漏、拼写、大小写、标点；拿不准就保留原文。\n"
        f"# 2) ZH：修正错字并润色成通顺、适合儿童的中文，以自然达意为准，不必直译。\n"
        f"# 3) 缺失的一侧（EN 或 ZH 整行没给）可补译。\n"
        f"# 4) 输出全部条目：保留「#编号 帧名」行原样，EN/ZH 行替换为校对后文本；\n"
        f"#    不要合并、拆分、增删条目；回复里加多少解释文字都可以，导入时自动忽略。\n")
    body, frames = [], []
    for sh in doc["shots"]:
        gl = ocr.build_lines(texts, sh["others"])
        if gl:
            body.append(f"## 镜头 {sh['id']}")
        for en, zh, fname in gl:
            body.append(f"#{len(frames) + 1:03d} {fname}")
            if en.strip():
                body.append(f"EN {en.strip()}")
            if zh.strip():
                body.append(f"ZH {zh.strip()}")
            frames.append(fname)
    return head + "\n".join(body) + "\n", frames


def parse_reply(text: str) -> list[dict]:
    """AI 回复 → [{num, frame, en, zh}]；只认 #编号行 / EN行 / ZH行，其余忽略。

    长行被 AI 折行时，普通文本行续接到最近一个字段。
    """
    recs: list[dict] = []
    cur: dict | None = None
    last_field = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("##"):
            continue
        m = _HEAD.match(line)
        if m:
            cur = {"num": int(m.group(1)), "frame": m.group(2),
                   "en": "", "zh": ""}
            last_field = ""
            recs.append(cur)
            continue
        m = _FIELD.match(line)
        if m and cur is not None:
            key = m.group(1).lower()
            cur[key] = m.group(2).strip()
            last_field = key
            continue
        if cur is not None and last_field:      # 折行续接：两侧均为字母数字才补空格
            a = cur[last_field]
            sep = " " if re.match(r"[A-Za-z0-9]", line[:1]) \
                and re.search(r"[A-Za-z0-9]$", a) else ""
            cur[last_field] = a + sep + line
    return recs


def run_import(task_dir, doc: dict, texts: dict, reply: str) -> dict:
    """解析 AI 回复并回写 subs.json；返回统计（含映射失败明细）。

    定位：优先帧名（导出锚点，随镜头增删不漂移）；无帧名时按编号对到
    当前清单同序条目。两者都对不上 → 计入 dropped，不写盘。
    """
    mapping = book_lines(doc, texts)
    by_frame = {fname: i for i, (_, _, fname) in enumerate(mapping)}
    recs = parse_reply(reply)
    apply: dict[str, dict] = {}
    unknown, dropped, empty = [], [], 0
    for r in recs:
        en, zh = r["en"].strip(), r["zh"].strip()
        if not en and not zh:
            empty += 1
            continue
        if r["frame"]:
            idx = by_frame.get(r["frame"])   # 帧名对不上=结构已变，宁可丢弃不猜
        else:
            idx = r["num"] - 1 if 0 <= r["num"] - 1 < len(mapping) else None
        if idx is None:
            (unknown if r["frame"] else dropped).append(
                r["frame"] or f"#{r['num']:03d}")
            continue
        fname = mapping[idx][2]
        apply[fname] = {"en": en, "zh": zh}
    if apply:
        ocr.save_texts(task_dir, apply)
    changed = sum(1 for f, t in apply.items()
                  if texts.get(f, {}).get("en", "") != t["en"]
                  or texts.get(f, {}).get("zh", "") != t["zh"])
    return {"blocks": len(recs), "applied": len(apply), "changed": changed,
            "unchanged": len(apply) - changed, "skipped_empty": empty,
            "unknown_frames": unknown, "dropped": dropped,
            "total": len(mapping)}
