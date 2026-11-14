# -*- coding: utf-8 -*-
"""ukids_book Web 服务 —— 编排中枢兼唯一入口。

启动：python ukids_book/webapp.py [--port 8765] [--no-browser]
职责：任务状态机 + 后台作业（拉回/分帧/切镜头/出稿）+ 编辑 API + 静态页。
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import argparse
import datetime as dtm
import io
import json
import os
import re
import shutil
import threading
import time
import webbrowser
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from ukids_book import compose, config, extract, ocr, subtitles
from ukids_book import shots as shots_mod
from ukids_book.adb import AdbDevice
from ukids_book.capture import RecordSession, new_task_dir

app = FastAPI(title="ukids_book", docs_url=None, redoc_url=None,
              lifespan=lambda app: _lifespan(app))

# ---------------- 全局状态 ----------------
_LOCK = threading.RLock()
_LIVE_LOCK = threading.Lock()
_DEV: AdbDevice | None = None
_RECORDER: RecordSession | None = None
_RECORDING_TASK: str | None = None
_JOBS: dict[str, "Job"] = {}
_LIVE: tuple[float, bytes] | None = None

ACTIVE_STATES = ("recording", "processing", "composing")
EDITABLE_STATES = ("editing", "ready")
VALID_NAME = re.compile(r"^[\w\-]{1,24}$")     # \w 含中文；目录名（新建任务）
VALID_LABEL = re.compile(r"^[\w\-. ]{1,32}$")  # 显示名（重命名）：可含空格/点
FILE_NAME = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _device() -> AdbDevice:
    global _DEV
    if _DEV is None:
        _DEV = AdbDevice()
    return _DEV


# ---------------- 后台作业 ----------------

class Job:
    """一个后台作业：步骤列表 + 进度，前端 1s 轮询读取。"""

    def __init__(self, kind: str, steps: list[str], spans: list[float]):
        self.kind = kind
        self.steps = [{"name": n, "status": "pending", "detail": ""}
                      for n in steps]
        self.spans = spans
        self.progress = 0.0
        self.error: str | None = None
        self.done = False
        self.thread: threading.Thread | None = None

    def step(self, i: int, status: str = "running", detail: str = "") -> None:
        self.steps[i]["status"] = status
        if detail:
            self.steps[i]["detail"] = detail

    def frac(self, i: int, f: float) -> None:
        self.progress = sum(self.spans[:i]) + \
            min(1.0, max(0.0, f)) * self.spans[i]

    def to_dict(self) -> dict:
        return {"kind": self.kind, "progress": round(self.progress),
                "steps": self.steps, "error": self.error, "done": self.done}


def _start_job(name: str, job: Job, fn) -> None:
    def _wrap():
        try:
            fn(job)
        except Exception as e:                       # noqa: BLE001
            import traceback
            traceback.print_exc()
            job.error = str(e)
            job.done = True
            _transition(name, "failed", error=f"{job.kind} 失败: {e}")
        else:
            job.done = True
    with _LOCK:
        old = _JOBS.get(name)
        if old and not old.done:
            raise HTTPException(409, "任务已有作业进行中")
        _JOBS[name] = job
        job.thread = threading.Thread(target=_wrap, daemon=True)
        job.thread.start()


def _ensure_no_job(name: str) -> None:
    j = _JOBS.get(name)
    if j and not j.done:
        raise HTTPException(409, "任务有作业进行中，请稍候")


# ---------------- 任务持久化 ----------------

def _tdir(name: str) -> Path:
    return config.WORK_DIR / name


def _load_task(name: str) -> dict | None:
    p = _tdir(name) / "task.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save(t: dict) -> None:
    d = _tdir(t["name"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "task.json.tmp"
    tmp.write_text(json.dumps(t, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(d / "task.json")


def _require_task(name: str) -> dict:
    t = _load_task(name)
    if not t:
        raise HTTPException(404, f"任务不存在: {name}")
    return t


def _transition(name: str, state: str, error: str | None = None) -> None:
    with _LOCK:
        t = _load_task(name)
        if not t:
            return
        t["state"] = state
        t["error"] = error
        t["updated"] = dtm.datetime.now().isoformat(timespec="seconds")
        _save(t)


def _save_state(t: dict, state: str | None = None, **fields) -> None:
    if state:
        t["state"] = state
    t.update(fields)
    t["updated"] = dtm.datetime.now().isoformat(timespec="seconds")
    with _LOCK:
        _save(t)


# ---------------- 作业函数 ----------------

def _do_extract(job: Job, i: int, tdir: Path, dt: float) -> list[dict]:
    job.step(i, "running")

    def _cb(done, total, label):
        job.step(i, detail=label)
        job.frac(i, done / total if total else 0.5)
    frames = extract.extract_frames(tdir, dt, progress=_cb)
    job.step(i, "done", f"{len(frames)} 帧")
    return frames


def _do_analyze(job: Job, i: int, tdir: Path, threshold=None, min_shots=None,
                keep_main_by_id=None) -> dict:
    job.step(i, "running")

    def _cb(done, total, label):
        job.step(i, detail=label)
        job.frac(i, done / total if total else 0.5)
    doc = shots_mod.detect_shots(tdir, threshold=threshold, min_shots=min_shots,
                                 keep_main_by_id=keep_main_by_id, progress=_cb)
    job.step(i, "done", f"{len(doc['shots'])} 镜头")
    return doc


def _job_pipeline(name: str, sess: RecordSession, dt: float) -> None:
    """停止录制 → 拉回 → 分帧 → 切镜头。"""

    def fn(job: Job):
        tdir = _tdir(name)
        job.step(0, "running", "结束当前段…")
        sess.stop()                                   # SIGINT 收尾（阻塞）
        if sess.error and not sess.segments:
            raise RuntimeError(f"录制异常: {sess.error}")
        dev, dev_dir = sess.dev, sess.dev_dir
        raw_dir = tdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        remotes = [f for f in dev.shell("ls", dev_dir).split()
                   if f.endswith(".mp4")]
        pulled = 0
        for f in remotes:
            dev.pull(f"{dev_dir}/{f}", raw_dir / f)
            pulled += 1
            job.step(0, detail=f"拉回 {pulled}/{len(remotes)} 段")
            job.frac(0, pulled / max(1, len(remotes)))
        dev.shell("rm", "-rf", dev_dir, check=False)
        if not list(raw_dir.glob("seg_*.mp4")):
            raise RuntimeError("没有拉回任何录像段")
        job.step(0, "done", f"{len(remotes)} 段")
        frames = _do_extract(job, 1, tdir, dt)
        doc = _do_analyze(job, 2, tdir)
        job.step(3, "running")                 # 字幕识别（只对检出的字幕帧）
        names = sorted({f for s in doc["shots"]
                        for f in subtitles.select_strip_names(
                            [tdir / "strips" / x for x in s["others"]])})
        def _p3(a, b, l):
            job.step(3, detail=l)
            job.frac(3, a / b if b else 1)
        ocr.ensure_subs(tdir, names, progress=_p3)
        for s in doc["shots"]:
            ocr.borrow_missing(tdir, s["others"])
        job.step(3, "done", "%d 帧识别" % len(names))
        t = _require_task(name)
        _save_state(t, "editing", error=None, dt=dt,
                    threshold=doc["meta"]["threshold"],
                    min_shots=doc["meta"]["min_shots"],
                    stats={"frames": len(frames), "shots": len(doc["shots"]),
                           "pages": 0})
    return fn


def _job_reextract(name: str, dt: float) -> None:
    def fn(job: Job):
        tdir = _tdir(name)
        frames = _do_extract(job, 0, tdir, dt)
        p_cache = tdir / ocr.CACHE_NAME
        if p_cache.is_file():                  # dt 变了帧内容全变，缓存作废
            p_cache.unlink()
        doc = _do_analyze(job, 1, tdir)
        names = sorted({f for s in doc["shots"]
                        for f in subtitles.select_strip_names(
                            [tdir / "strips" / x for x in s["others"]])})
        ocr.ensure_subs(tdir, names)
        for s in doc["shots"]:
            ocr.borrow_missing(tdir, s["others"])
        t = _require_task(name)
        st = t.get("stats") or {}
        _save_state(t, "editing", error=None, dt=dt,
                    threshold=doc["meta"]["threshold"],
                    min_shots=doc["meta"]["min_shots"],
                    stats={"frames": len(frames), "shots": len(doc["shots"]),
                           "pages": 0})
    return fn


def _job_reanalyze(name: str, threshold, min_shots) -> None:
    def fn(job: Job):
        tdir = _tdir(name)
        old = shots_mod.read_shots(tdir)
        keep = ({s["id"]: s["main"] for s in old["shots"]}
                if old else None)
        doc = _do_analyze(job, 0, tdir, threshold=threshold,
                          min_shots=min_shots, keep_main_by_id=keep)
        t = _require_task(name)
        st = t.get("stats") or {}
        _save_state(t, "editing",
                    threshold=doc["meta"]["threshold"],
                    min_shots=doc["meta"]["min_shots"],
                    stats={**st, "shots": len(doc["shots"])})
    return fn


def _shot_blocks(tdir: Path, doc: dict, texts: dict) -> list[dict]:
    """镜头列表 -> [{sid, blocks:[(path, lines),...], nlines}]（台词归最近主图）。"""
    out = []
    prev_nm = ""
    for sh in doc["shots"]:
        lines = []
        for en, zh, f in ocr.build_lines(texts, sh["others"]):
            nm = ocr.norm_line({"en": en, "zh": zh})
            if nm and nm == prev_nm:
                continue
            prev_nm = nm
            lines.append((en, zh, f))
        mains = sh.get("mains") or [sh["main"]]
        idx = {f: i for i, f in enumerate(sh["others"])}
        midx = [idx[m] for m in mains]
        per = {j: [] for j in range(len(mains))}
        for en, zh, f in lines:
            fi = idx.get(f, midx[0])
            j = min(range(len(midx)), key=lambda k: abs(midx[k] - fi))
            per[j].append((en, zh))
        blocks = [(tdir / "shots" / m, per[j]) for j, m in enumerate(mains)]
        out.append({"sid": sh["id"], "blocks": blocks,
                    "nlines": sum(len(b[1]) for b in blocks)})
    return out


def _job_rebuild(name: str, png: bool, pdf: bool) -> None:
    def fn(job: Job):
        tdir = _tdir(name)
        t0 = _require_task(name)
        paper = t0.get("paper", "a4")
        landscape = t0.get("orientation", "portrait") == "landscape"
        doc = _read_doc(tdir)
        if not doc or not doc["shots"]:
            raise RuntimeError("没有镜头可出稿")

        # ① 组稿：只读台词缓存（识别在管线/弹层做过了），纯排版秒级
        job.step(0, "running")
        texts = ocr.cached_texts(tdir)
        shots_data = _shot_blocks(tdir, doc, texts)
        pages = compose.pack_pages(shots_data, mode=t0.get("page_shots", "1"))
        job.step(0, "done",
                 "%d 条台词 · %d 张主图 · %d 页" % (
                     sum(s["nlines"] for s in shots_data),
                     sum(len(s["blocks"]) for s in shots_data),
                     len(pages)))

        # ② 渲染 PDF
        out = config.OUTPUT_DIR / name
        out.mkdir(parents=True, exist_ok=True)
        step = 1
        if png:
            job.step(step, "running", "渲染长图…")
            flat = [(p, l) for s in shots_data for p, l in s["blocks"]]
            compose.compose_book_text(flat, out / "book.png")
            job.step(step, "done")
            step += 1
        pages_dir = tdir / "pages"
        if pages_dir.exists():
            for f in pages_dir.glob("page_*.png"):
                f.unlink()
        pdf_pages = []
        for cells in pages:                    # 页 = [镜头格,…]，格 = [(path,lines),…]
            pdf_pages.append([s["blocks"] for s in cells])
        job.step(step, "running",
                 "渲染 PDF（%s%s · %d 页/%d 主图）…" % (
                     paper.upper(), " 横版" if landscape else "",
                     len(pages), sum(len(c) for c in pdf_pages)))
        compose.compose_pdf_multi(pdf_pages, out / "book.pdf",
                                  pages_dir=pages_dir, paper=paper,
                                  landscape=landscape)
        job.step(step, "done", "%d 页" % len(pages))
        t = _require_task(name)
        st = t.get("stats") or {}
        _save_state(t, "ready", error=None,
                    stats={**st, "pages": len(pages)})
    return fn


# ---------------- 录制看门狗：到时/异常自动停 ----------------

def _begin_stop(name: str) -> dict:
    """停止录制并自动进入处理管线（幂等保护：仅 recording 状态可触发）。"""
    global _RECORDER, _RECORDING_TASK
    with _LOCK:
        if _RECORDING_TASK != name or _RECORDER is None:
            raise HTTPException(409, f"任务 {name} 不在录制中")
        _ensure_no_job(name)
        task = _require_task(name)
        if task["state"] != "recording":
            raise HTTPException(409, "当前状态不能停止")
        sess = _RECORDER
        _RECORDER = None
        _RECORDING_TASK = None
        _save_state(task, "processing", error=None)
        dtv = float(task.get("dt") or config.EXTRACT_DT)
        job = Job("pipeline", ["拉回录像", "分帧", "切镜头", "字幕识别"], [30, 40, 15, 15])
        _start_job(name, job, _job_pipeline(name, sess, dtv))
        return {"ok": True, "state": "processing"}


def _watchdog() -> None:
    while True:
        time.sleep(1.0)
        with _LOCK:
            name, sess = _RECORDING_TASK, _RECORDER
            if not name or sess is None or sess.alive():
                continue
        try:                                        # 预计时长到 / 录制异常
            _begin_stop(name)
        except Exception as e:                      # noqa: BLE001
            print(f"[watchdog] 自动停止失败 {name}: {e}", flush=True)


# ---------------- API ----------------

class TaskIn(BaseModel):
    name: str


class RecordStartIn(BaseModel):
    duration_hint: float | None = None


class MainIn(BaseModel):
    main: str | None = None
    mains: list[str] | None = None        # 多主图（★ 逐帧开关后的全量）
    merge: bool = False


class FramesIn(BaseModel):
    remove: list[str]


class ReextractIn(BaseModel):
    dt: float


class ReanalyzeIn(BaseModel):
    th: float | None = None
    min_shots: int | None = None


class RebuildIn(BaseModel):
    png: bool = False                     # 长图已弃用，默认只出 PDF
    pdf: bool = True


class TaskPatchIn(BaseModel):
    paper: str | None = None
    orientation: str | None = None
    page_shots: str | None = None         # auto | 1 | 2 | 4（每页镜头数）
    label: str | None = None          # 重命名（仅显示名，目录不动）


class MoveIn(BaseModel):
    frames: list[str]
    target: str            # prev | next | new


class TextsIn(BaseModel):
    texts: dict[str, dict]     # {帧名: {"en":…, "zh":…}}


def _fno(f: str) -> int:
    """帧文件名 f012345.jpg → 帧序号（排序用）。"""
    return int(f[1:7])


@app.get("/api/tasks")
def tasks_list():
    out = []
    if config.WORK_DIR.is_dir():
        for d in config.WORK_DIR.iterdir():
            if not (d / "task.json").is_file():
                continue
            t = _load_task(d.name)
            if not t:
                continue
            j = _JOBS.get(d.name)
            item = {k: t.get(k) for k in
                    ("name", "label", "state", "created", "updated",
                     "error", "stats", "dt", "threshold", "min_shots")}
            item["job"] = j.to_dict() if (j and not j.done) else None
            out.append(item)
    out.sort(key=lambda x: x.get("updated") or "", reverse=True)
    return {"tasks": out}


@app.post("/api/tasks")
def tasks_create(body: TaskIn):
    name = body.name.strip()
    if not VALID_NAME.fullmatch(name):
        raise HTTPException(400, "名称仅限中英文/数字/下划线/连字符，≤24 字符")
    with _LOCK:
        d = new_task_dir(name)
        now = dtm.datetime.now().isoformat(timespec="seconds")
        task = {"name": d.name, "label": name, "state": "created",
                "created": now, "updated": now, "error": None,
                "dt": config.EXTRACT_DT, "threshold": None,
                "min_shots": config.MIN_SHOTS, "stats": {},
                "paper": "a4", "orientation": "portrait"}
        _save(task)
    return task


@app.get("/api/tasks/{t}")
def task_detail(t: str):
    task = _require_task(t)
    resp = dict(task)
    j = _JOBS.get(t)
    resp["job"] = j.to_dict() if j else None
    if _RECORDING_TASK == t and _RECORDER is not None:
        resp["record"] = {"elapsed": round(_RECORDER.elapsed()),
                          "segments": len(_RECORDER.segments),
                          "size_mb": _RECORDER.size_mb(),
                          "hint": _RECORDER.duration_hint,
                          "error": _RECORDER.error}
    out = config.OUTPUT_DIR / t
    resp["has_output"] = (out / "book.pdf").is_file() or \
        (out / "book.png").is_file()
    return resp


@app.delete("/api/tasks/{t}")
def task_delete(t: str):
    with _LOCK:
        _require_task(t)
        if _RECORDING_TASK == t:
            raise HTTPException(409, "录制中，请先停止再删除")
        _ensure_no_job(t)
        _JOBS.pop(t, None)
        shutil.rmtree(_tdir(t), ignore_errors=True)
        shutil.rmtree(config.OUTPUT_DIR / t, ignore_errors=True)
    return {"ok": True}


@app.post("/api/tasks/{t}/clone")
def task_clone(t: str):
    """复制任务（编辑前备份）：帧图/录像硬链接零占盘；可变文件真拷贝防串改。"""
    with _LOCK:
        src = _require_task(t)
        if _RECORDING_TASK == t:
            raise HTTPException(409, "录制中，请先停止再复制")
        _ensure_no_job(t)
        label = (src.get("label") or t).removesuffix("副本") + "副本"
        sdir = _tdir(t)
        d = new_task_dir(label)
        link_dirs = {"raw", "shots", "thumbs", "strips"}   # 只读不变 → 硬链接
        for item in sdir.iterdir():
            if item.name == "task.json":
                continue
            if item.is_dir():
                cf = os.link if item.name in link_dirs else shutil.copy2
                shutil.copytree(item, d / item.name, dirs_exist_ok=True,
                                copy_function=cf)
            else:
                shutil.copy2(item, d / item.name)          # yaml/json 会改写
        now = dtm.datetime.now().isoformat(timespec="seconds")
        task = {k: v for k, v in src.items() if k != "job"}
        task.update({"name": d.name, "label": label, "error": None,
                     "created": now, "updated": now,
                     "state": "editing" if (d / "shots.yaml").is_file()
                     else "created"})
        _save(task)
    return task


@app.post("/api/tasks/{t}/record/start")
def record_start(t: str, body: RecordStartIn):
    global _RECORDER, _RECORDING_TASK
    with _LOCK:
        task = _require_task(t)
        if task["state"] != "created":
            raise HTTPException(409, "仅「新建」状态的任务可开始录制")
        if _RECORDING_TASK:
            raise HTTPException(409, f"设备正被任务 {_RECORDING_TASK} 占用")
        _ensure_no_job(t)
        dev = _device()
        try:
            dev.ensure_ready()
        except Exception as e:                       # noqa: BLE001
            raise HTTPException(503, str(e))
        hint = body.duration_hint
        if hint is not None:
            hint = max(10.0, min(4 * 3600.0, float(hint)))
        sess = RecordSession(dev, _tdir(t))
        try:
            sess.start(duration_hint=hint)
        except Exception as e:                       # noqa: BLE001
            raise HTTPException(503, f"启动录屏失败: {e}")
        _RECORDER, _RECORDING_TASK = sess, t
        _save_state(task, "recording", duration_hint=hint)
    return {"ok": True, "state": "recording"}


@app.post("/api/tasks/{t}/record/stop")
def record_stop(t: str):
    return _begin_stop(t)


@app.post("/api/tasks/{t}/retry")
def task_retry(t: str):
    with _LOCK:
        task = _require_task(t)
        if task["state"] != "failed":
            raise HTTPException(409, "仅失败任务可重试")
        _ensure_no_job(t)
        tdir = _tdir(t)
        if shots_mod.read_shots(tdir):
            _save_state(task, "editing", error=None)   # 数据完好直接编辑
            return {"ok": True, "state": "editing"}
        if list((tdir / "raw").glob("seg_*.mp4")):
            dtv = float(task.get("dt") or config.EXTRACT_DT)
            _save_state(task, "processing", error=None)
            job = Job("reextract", ["分帧", "切镜头"], [60, 40])
            _start_job(t, job, _job_reextract(t, dtv))
            return {"ok": True, "state": "processing"}
        _save_state(task, "created", error=None)       # 无数据回新建
        return {"ok": True, "state": "created"}


@app.get("/api/tasks/{t}/live.jpg")
def live_img(t: str):
    _require_task(t)
    global _LIVE
    with _LIVE_LOCK:
        now = time.time()
        if _LIVE and now - _LIVE[0] < config.LIVE_MAX_AGE:
            return Response(_LIVE[1], media_type="image/jpeg")
        try:
            img = Image.open(io.BytesIO(_device().screenshot()))
            img.thumbnail((config.LIVE_WIDTH, 4096))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "JPEG", quality=80)
            data = buf.getvalue()
        except Exception as e:                       # noqa: BLE001
            raise HTTPException(503, f"设备画面获取失败: {e}")
        _LIVE = (now, data)
    return Response(data, media_type="image/jpeg")


def _read_doc(tdir: Path) -> dict | None:
    """读 shots.yaml 并归一化（老数据补 mains 字段）。"""
    doc = shots_mod.read_shots(tdir)
    if doc:
        for s in doc["shots"]:
            shots_mod.maintain_shot(s)
    return doc


def _require_editable(t: str) -> Path:
    task = _require_task(t)
    if task["state"] not in EDITABLE_STATES:
        raise HTTPException(409, f"当前状态 {task['state']} 不可编辑")
    _ensure_no_job(t)
    return _tdir(t)


def _apply_edit(t: str, tdir: Path, doc: dict) -> dict:
    for i, s in enumerate(doc["shots"], 1):          # 重排 id
        s["id"] = i
    shots_mod.write_shots(tdir, doc)
    task = _require_task(t)
    st = task.get("stats") or {}
    _save_state(task, "editing" if task["state"] == "ready" else task["state"],
                stats={**st, "shots": len(doc["shots"])})
    return doc


@app.patch("/api/tasks/{t}")
def task_patch(t: str, body: TaskPatchIn):
    task = _require_task(t)
    if body.paper is not None:
        if body.paper not in compose.PAPERS:
            raise HTTPException(400, f"纸张仅限 {list(compose.PAPERS)}")
        task["paper"] = body.paper
    if body.orientation is not None:
        if body.orientation not in ("portrait", "landscape"):
            raise HTTPException(400, "orientation 须为 portrait/landscape")
        task["orientation"] = body.orientation
    if body.page_shots is not None:
        if body.page_shots not in ("1", "2", "4"):
            raise HTTPException(400, "page_shots 须为 1/2/4")
        task["page_shots"] = body.page_shots
    if body.label is not None:
        label = body.label.strip()
        if not VALID_LABEL.fullmatch(label):
            raise HTTPException(400, "名称仅限中英文/数字/空格/._-，≤32 字符")
        task["label"] = label
    with _LOCK:
        _save(task)
    return {"paper": task.get("paper", "a4"),
            "orientation": task.get("orientation", "portrait"),
            "page_shots": task.get("page_shots", "1"),
            "label": task["label"]}


@app.get("/api/tasks/{t}/shots")
def shots_get(t: str):
    _require_task(t)
    doc = _read_doc(_tdir(t))
    if not doc:
        raise HTTPException(404, "尚无 shots.yaml（先完成处理管线）")
    fj = _tdir(t) / "frames.json"
    if fj.is_file():                                # 附带帧时间轴（UI 显示用）
        try:
            fr = json.loads(fj.read_text(encoding="utf-8"))["frames"]
            doc["times"] = {f["file"]: f["t"] for f in fr}
        except Exception:
            pass
    doc["texts"] = ocr.cached_texts(_tdir(t))       # 台词缓存（OCR 预填+人工校对）
    return doc


@app.patch("/api/tasks/{t}/shots/{sid}")
def shot_patch(t: str, sid: int, body: MainIn):
    tdir = _require_editable(t)
    doc = _read_doc(tdir)
    if not doc:
        raise HTTPException(404, "尚无 shots.yaml")
    shots = doc["shots"]
    pos = next((i for i, s in enumerate(shots) if s["id"] == sid), None)
    if pos is None:
        raise HTTPException(404, f"镜头 {sid} 不存在")
    sh = shots[pos]
    if body.merge:
        if pos == 0:
            raise HTTPException(400, "第一个镜头不能并入上一镜头")
        prev = shots[pos - 1]
        prev["others"] = sorted(set(prev["others"]) | set(sh["others"]),
                                key=lambda f: int(f[1:7]))
        shots_mod.maintain_shot(prev,
                                extra_mains=sh.get("mains") or [sh["main"]])
        prev["subs"] = shots_mod.count_subs(tdir, prev["others"])
        shots.remove(sh)
    elif body.mains is not None:
        bad = [f for f in body.mains if f not in sh["others"]]
        if bad:
            raise HTTPException(400, f"主图必须是该镜头内的帧: {bad[:3]}")
        if not body.mains:
            raise HTTPException(400, "至少保留一张主图")
        sh["mains"] = sorted(set(body.mains), key=_fno)
        sh["main"] = sh["mains"][0]
        shots_mod.maintain_shot(sh)
    elif body.main:
        if body.main not in sh["others"]:
            raise HTTPException(400, "主图必须是该镜头内的帧")
        sh["mains"] = [body.main]           # 单主图=独占语义（兼容旧接口）
        sh["main"] = body.main
        shots_mod.maintain_shot(sh)
    elif body.main is None and not body.merge:
        raise HTTPException(400, "需要 main 或 merge 参数")
    return _apply_edit(t, tdir, doc)


@app.delete("/api/tasks/{t}/shots/{sid}")
def shot_delete(t: str, sid: int):
    tdir = _require_editable(t)
    doc = _read_doc(tdir)
    if not doc:
        raise HTTPException(404, "尚无 shots.yaml")
    shots = doc["shots"]
    pos = next((i for i, s in enumerate(shots) if s["id"] == sid), None)
    if pos is None:
        raise HTTPException(404, f"镜头 {sid} 不存在")
    shots.remove(shots[pos])
    return _apply_edit(t, tdir, doc)


@app.patch("/api/tasks/{t}/shots/{sid}/frames")
def frames_patch(t: str, sid: int, body: FramesIn):
    tdir = _require_editable(t)
    doc = _read_doc(tdir)
    if not doc:
        raise HTTPException(404, "尚无 shots.yaml")
    sh = next((s for s in doc["shots"] if s["id"] == sid), None)
    if not sh:
        raise HTTPException(404, f"镜头 {sid} 不存在")
    if not body.remove:
        raise HTTPException(400, "remove 列表为空")
    if len(body.remove) >= len(sh["others"]):
        raise HTTPException(400, "镜头至少保留一帧（要删整个镜头请用删除镜头）")
    rms = set(body.remove)
    sh["others"] = [f for f in sh["others"] if f not in rms]
    shots_mod.maintain_shot(sh)
    sh["subs"] = shots_mod.count_subs(tdir, sh["others"])
    return _apply_edit(t, tdir, doc)


@app.post("/api/tasks/{t}/shots/{sid}/move")
def shot_move(t: str, sid: int, body: MoveIn):
    """把当前镜头中勾选的帧移到上一/下一镜头，或拆分为新镜头。"""
    tdir = _require_editable(t)
    doc = _read_doc(tdir)
    if not doc:
        raise HTTPException(404, "尚无 shots.yaml")
    shots = doc["shots"]
    pos = next((i for i, s in enumerate(shots) if s["id"] == sid), None)
    if pos is None:
        raise HTTPException(404, f"镜头 {sid} 不存在")
    sh = shots[pos]
    fr = set(body.frames)
    if not fr or not fr.issubset(set(sh["others"])):
        raise HTTPException(400, "frames 必须是当前镜头内的帧")
    dst = None
    if body.target == "prev":
        if pos == 0:
            raise HTTPException(400, "没有上一镜头")
        dst = shots[pos - 1]
    elif body.target == "next":
        if pos == len(shots) - 1:
            raise HTTPException(400, "没有下一镜头")
        dst = shots[pos + 1]
    elif body.target != "new":
        raise HTTPException(400, "target 须为 prev/next/new")

    sh["others"] = [f for f in sh["others"] if f not in fr]
    moved_mains = [f for f in sh.get("mains", []) if f in fr]
    if dst is not None:
        dst["others"] = sorted(set(dst["others"]) | fr, key=_fno)
        shots_mod.maintain_shot(dst, extra_mains=moved_mains)
        dst["subs"] = shots_mod.count_subs(tdir, dst["others"])
    else:
        new_sh = {"id": 0, "main": "", "candidates": [], "mains": [],
                  "others": sorted(fr, key=_fno), "subs": 0}
        shots_mod.maintain_shot(new_sh)
        new_sh["subs"] = shots_mod.count_subs(tdir, new_sh["others"])
        shots.insert(pos + 1, new_sh)

    emptied = False
    if not sh["others"]:
        shots.remove(sh)
        emptied = True
    else:
        shots_mod.maintain_shot(sh)
        sh["subs"] = shots_mod.count_subs(tdir, sh["others"])
    doc = _apply_edit(t, tdir, doc)
    doc["emptied"] = emptied
    return doc


@app.get("/api/tasks/{t}/shots/{sid}/preview.jpg")
def shot_preview(t: str, sid: int):
    """该镜头所在整页的预览（多主图同页堆叠，与出稿同一逻辑）。"""
    task = _require_task(t)
    if task["state"] not in EDITABLE_STATES:
        raise HTTPException(409, "当前状态不可预览")
    tdir = _tdir(t)
    doc = _read_doc(tdir)
    if not doc:
        raise HTTPException(404, "尚无 shots.yaml")
    if not any(s["id"] == sid for s in doc["shots"]):
        raise HTTPException(404, "镜头 %d 不存在" % sid)
    texts = ocr.cached_texts(tdir)
    shots_data = _shot_blocks(tdir, doc, texts)
    pages = compose.pack_pages(shots_data, mode=task.get("page_shots", "1"))
    page = next((p for p in pages if any(s["sid"] == sid for s in p)), None)
    if not page:
        raise HTTPException(404, "无可预览内容")
    pw, ph = compose.page_geom(task.get("paper", "a4"),
                               task.get("orientation", "portrait") == "landscape")
    cells = []
    for s in page:
        b = []
        for path, lines in s["blocks"]:
            img = Image.open(path).convert("RGB")
            w2, h2 = img.size
            b.append((img.crop((0, 0, w2,
                     int(h2 * (1 - config.SUBTITLE_BAND_CROP)))), lines))
        cells.append(b)
    page_img = compose.render_page_multi(cells, 0, 1, pw, ph)
    page_img.thumbnail((880, 4000))
    buf = io.BytesIO()
    page_img.convert("RGB").save(buf, "JPEG", quality=88)
    return Response(buf.getvalue(), media_type="image/jpeg")


@app.get("/api/tasks/{t}/shots/{sid}/strips")
def shot_strips(t: str, sid: int):
    """该镜头有字幕的帧名 + 台词缓存（校对区预览用）。"""
    task = _require_task(t)
    if task["state"] not in EDITABLE_STATES:
        raise HTTPException(409, "当前状态不可查看")
    tdir = _tdir(t)
    doc = _read_doc(tdir)
    if not doc:
        raise HTTPException(404, "尚无 shots.yaml")
    sh = next((s for s in doc["shots"] if s["id"] == sid), None)
    if not sh:
        raise HTTPException(404, f"镜头 {sid} 不存在")
    names = subtitles.select_strip_names(
        [tdir / "strips" / f for f in sh["others"]])
    return {"frames": names,
            "texts": ocr.cached_texts(tdir, names)}


@app.put("/api/tasks/{t}/shots/{sid}/texts")
def shot_texts_put(t: str, sid: int, body: TextsIn):
    """保存人工校对的台词（帧必须属于该镜头；留空=该帧不出台词）。"""
    tdir = _require_editable(t)
    doc = _read_doc(tdir)
    if not doc:
        raise HTTPException(404, "尚无 shots.yaml")
    sh = next((s for s in doc["shots"] if s["id"] == sid), None)
    if not sh:
        raise HTTPException(404, f"镜头 {sid} 不存在")
    others = set(sh["others"])
    bad = [f for f in body.texts if f not in others]
    if bad:
        raise HTTPException(400, f"帧不属于镜头 {sid}: {bad[:3]}")
    ocr.save_texts(tdir, body.texts)
    return {"ok": True, "saved": len(body.texts)}


@app.post("/api/tasks/{t}/shots/{sid}/ocr")
def shot_ocr(t: str, sid: int):
    """「识别缺失」：识别/重试本镜头全部帧（人工输入的非空值绝不覆盖；
    空值重试——清空某行再点此按钮即可强制重读）。"""
    tdir = _require_editable(t)
    doc = _read_doc(tdir)
    if not doc:
        raise HTTPException(404, "尚无 shots.yaml")
    sh = next((s for s in doc["shots"] if s["id"] == sid), None)
    if not sh:
        raise HTTPException(404, f"镜头 {sid} 不存在")
    names = subtitles.select_strip_names(
        [tdir / "strips" / f for f in sh["others"]])
    ocr.ensure_subs(tdir, sh["others"], retry=True)
    ocr.borrow_missing(tdir, sh["others"])
    return {"frames": names, "texts": ocr.cached_texts(tdir, sh["others"])}


@app.post("/api/tasks/{t}/reextract")
def reextract(t: str, body: ReextractIn):
    with _LOCK:
        task = _require_task(t)
        if task["state"] not in (*EDITABLE_STATES, "failed"):
            raise HTTPException(409, "当前状态不能重抽帧")
        _ensure_no_job(t)
        if not list((_tdir(t) / "raw").glob("seg_*.mp4")):
            raise HTTPException(400, "raw/ 无录像，无法重抽帧")
        dtv = min(3.0, max(0.5, float(body.dt)))
        _save_state(task, "processing", error=None, dt=dtv)
        job = Job("reextract", ["分帧", "切镜头"], [60, 40])
        _start_job(t, job, _job_reextract(t, dtv))
    return {"ok": True, "state": "processing"}


@app.post("/api/tasks/{t}/reanalyze")
def reanalyze(t: str, body: ReanalyzeIn):
    with _LOCK:
        task = _require_task(t)
        if task["state"] not in EDITABLE_STATES:
            raise HTTPException(409, "当前状态不能重分析")
        _ensure_no_job(t)
        if not (_tdir(t) / "frames.json").is_file():
            raise HTTPException(400, "缺少 frames.json，请先抽帧")
        _save_state(task, "processing", error=None)
        job = Job("reanalyze", ["切镜头"], [100])
        _start_job(t, job, _job_reanalyze(t, body.th, body.min_shots))
    return {"ok": True, "state": "processing"}


@app.post("/api/tasks/{t}/rebuild")
def rebuild(t: str, body: RebuildIn):
    with _LOCK:
        task = _require_task(t)
        if task["state"] not in EDITABLE_STATES:
            raise HTTPException(409, "当前状态不能出稿")
        _ensure_no_job(t)
        if not shots_mod.read_shots(_tdir(t)):
            raise HTTPException(400, "没有镜头数据，无法出稿")
        steps, spans = ["组稿排版"], [15]
        if body.png:
            steps.append("长图")
            spans.append(20)
        if body.pdf:
            steps.append("PDF")
            spans.append(40)
        _save_state(task, "composing", error=None)
        job = Job("rebuild", steps, spans)
        _start_job(t, job, _job_rebuild(t, body.png, body.pdf))
    return {"ok": True, "state": "composing"}


_MEDIA = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
          "png": "image/png", "pdf": "application/pdf"}


@app.get("/files/{t}/{kind}/{name}")
def files(t: str, kind: str, name: str):
    _require_task(t)
    if not FILE_NAME.fullmatch(name) or ".." in name:
        raise HTTPException(400, "非法文件名")
    if kind == "output":
        base = (config.OUTPUT_DIR / t).resolve()
    elif kind in ("shots", "thumbs", "strips", "pages"):
        base = (_tdir(t) / kind).resolve()
    else:
        raise HTTPException(404, "未知类型")
    p = (base / name).resolve()
    if not str(p).startswith(str(base)) or not p.is_file():
        raise HTTPException(404, "文件不存在")
    media = _MEDIA.get(p.suffix[1:].lower())
    if not media:
        raise HTTPException(400, "仅支持 jpg/png/pdf")
    return FileResponse(p, media_type=media)


# ---------------- 启动 ----------------

@asynccontextmanager
async def _lifespan(_app):
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)
    for d in config.WORK_DIR.iterdir():              # 重启恢复
        if not (d / "task.json").is_file():
            continue
        t = _load_task(d.name)
        if t and t.get("state") in ACTIVE_STATES:
            _save_state(t, "failed", error="服务重启中断，可重试")
    threading.Thread(target=_watchdog, daemon=True).start()
    yield


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static",
                           html=True), name="static")


def main() -> None:
    ap = argparse.ArgumentParser(prog="ukids_book")
    ap.add_argument("--host", default=config.WEB_HOST)
    ap.add_argument("--port", type=int, default=config.WEB_PORT)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(
            f"http://{args.host}:{args.port}")).start()
    print(f"ukids_book 工作台: http://{args.host}:{args.port}  （Ctrl+C 退出）")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
