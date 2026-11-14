# 小小优趣视频画册（半自动 · 纯 Web）

模拟器播放视频 → 浏览器里启停录制 → 自动「拉回/分帧/切镜头」→ **Web 编辑器审校** → 长图 + A4 打印 PDF（带页码）。
全程唯一一条命令，其余都在浏览器完成。

方案：`doc/plan/202611111915_半自动截图画册实现方案.md`（v3）

## 启动（唯一命令）

```bash
conda activate py313_adb_auto        # Python 3.13 + pillow/fastapi/uvicorn/pyyaml
python ukids_book/webapp.py          # 自动打开 http://127.0.0.1:8765
```

可选参数：`--port 8765 --no-browser`。模拟器与浏览器各占半屏即为工作台。

## 使用流程

1. 新建任务（如 `E317`）→ 在模拟器里点开该集播放 → 点「开始录制」（预计时长默认 21:15 自动停）
2. 等待自动处理完成（拉回→分帧→切镜头，进度实时显示）→ 自动进入编辑视图
3. 逐镜头审校：换主图 / 删废镜头 / 勾删冗余帧 / 并入上一镜头 / 帧移到上(下)镜头或拆成新镜头；点图放大、全选反选
4. **台词校对**：弹层内逐帧「字幕截图 + 中英输入框」对照修改 OCR（自动保存，人工值永不被重识别覆盖；「🤖 识别缺失」镜头内即时 GPU 识别；留空的帧不出台词）
5. 「重出稿」→ 预览（长图/逐页）→ 下载 `book.pdf` 打印；配置抽屉可选纸张（A3/A4/A5）与横纵版；台词以字体排版（非截图拼接）
6. 怕改错可先「复制」任务（硬链接零占盘）在副本上编辑；返工也在 Web 内：配置抽屉改 dt / 阈值 → 确认覆盖 → 自动重跑

## 目录结构

```
├── doc/                 文档：idea/ 原始想法，plan/ 实现方案
├── ukids_book/          主源码包
│   ├── config.py        路径与参数常量（唯一修改点）
│   ├── adb.py           设备层：定位 adb / shell / pull / screencap / 录屏进程管理
│   ├── capture.py       RecordSession：可控链式录屏（Web 启停）
│   ├── extract.py       分帧：raw → shots/(JPEG q95) + thumbs/ + strips/ + frames.json
│   ├── shots.py         切镜头：差分+自适应阈值+min_shots → shots.yaml（单一事实源）
│   ├── subtitles.py     字幕带：抠条 + 白字字模去重
│   ├── ocr.py           字幕 OCR：分行放大识别 → subs.json 缓存（文字台词模式）
│   ├── compose.py       合成：长图 + PDF（纸张/横纵可选；图片条或文字台词）
│   ├── webapp.py        FastAPI 编排中枢 · 唯一入口（状态机/后台作业/API/静态页）
│   └── static/index.html  单页前端（原生 JS，无构建链）
├── work/<task>/         中间产物（gitignore）：task.json 状态机 · raw/ · shots/ ·
│                        thumbs/ · strips/ · frames.json · shots.yaml · pages/
└── output/<task>/       最终交付（gitignore）：book.png / book.pdf
```

## GPU 加速（可选）

字幕 OCR 默认走 **DirectML（GPU）**——RTX / Intel 核显等 DirectX 12 GPU 均可用，实测端到端 **2 倍加速**（432→218ms/条，识别结果与 CPU 完全一致）。依赖：

```bash
pip uninstall -y onnxruntime && pip install onnxruntime-directml
```

（rapidocr-onnxruntime 默认装 CPU 版 onnxruntime，需如上替换；未替换时自动回退 CPU，功能不受影响。）开关在 `ukids_book/config.py`：`OCR_DEVICE = "dml" | "cpu"`。

## 关键事实

- screenrecord 单次上限 180s → 175s 链式分段，段间 ~0.4s 盲区；停止用 SIGINT 让 mp4 正常收尾
- 分帧默认 dt=1.0s（JPEG q95）；抽帧时顺带产出 320px 缩略图与字幕带缓存（strips/）
- 镜头切分：80x45 灰度差分，阈值 = clamp(μ+3σ, 12, 45)，短于 min_shots 帧的镜头并入前一镜头
- 字幕带 y=930-1060（1080p 基准），白字二值化字模去重（阈值 190 / 5%）
- A4/A3/A5 300dpi 可选横纵版，装订边 25mm 奇偶页交替（双面打印），页码在外下角
- 主图裁底部 18% 字幕带；主图与字幕条、字幕条之间无缝拼接
- 服务重启不丢任务（task.json）；中断任务标 failed 可一键重试
