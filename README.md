# 小小优趣视频画册（半自动）

模拟器播放视频 → ADB 录屏 → 按 dt 分帧 → 切镜头 → **Web 界面挑选主图/预览** → 长图 + A4 打印 PDF（带页码）。

方案：`doc/plan/202611111915_半自动截图画册实现方案.md`（v2）

## 环境

```bash
conda activate py313_adb_auto        # Python 3.13 + Pillow
```

ffmpeg 与雷电 adb 均为绿色版，路径收口在 `ukids_book/config.py`。

## 目录结构

```
├── doc/                 文档：idea/ 原始想法，plan/ 实现方案
├── ukids_book/          主源码包
│   ├── config.py        路径与参数常量（唯一修改点）
│   ├── adb.py           设备层：定位 adb / shell / pull / screenshot
│   ├── capture.py       record：链式录屏 + 任务目录隔离
│   ├── screenshot.py    shot：即时截图小工具
│   ├── subtitles.py     字幕带：从帧图抠条 + 白字字模去重
│   ├── compose.py       合成：长图 + A4 PDF（装订边交替、页码）
│   └── cli.py           命令行入口（record / shot，后续增补）
├── work/                中间产物（gitignore）：每任务一个 <名称>_<时间戳>/ 目录
│   └── <task>/          raw/ 录像分段 → shots/ 分帧 → shots.yaml 镜头序列
└── output/              最终交付（gitignore）：book.png / book.pdf
```

## 用法（按实施进度增补）

```bash
# 录制：模拟器里点开视频播放后执行（一集约 1275s）
python -m ukids_book.cli record --name E317 --duration 1275

# 即时截图（单张 / 每30秒连拍20张）
python -m ukids_book.cli shot [-i 30 -c 20]

# 待实现：extract / analyze / compose / make / serve（见方案 v2）
```

## 关键事实

- screenrecord 单次上限 180s → 175s 链式分段，段间 ~0.4s 盲区
- 分帧默认 dt=1.0s；字幕带 y=930-1060，白字二值化字模去重（阈值 190/5%）
- A4 300dpi，装订边 25mm 奇偶页交替（双面打印），页码在外下角
- 主图裁底部 18% 字幕带，避免与拼接字幕条重复
