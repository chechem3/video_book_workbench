# 小小优趣自动截图 → 画册

把小小优趣（雷电模拟器）里播放的旋风战车队等视频，通过 ADB 录屏 + SRT 字幕，
自动分片截图并合成可打印的双语画册（长图 / A4 PDF）。

设计文档见 `doc/plan/202611092240_基于ADB的视频自动截图实现方案.md`。

## 环境

```bash
conda create -n py313_adb_auto python=3.13 pillow   # 已建好
conda activate py313_adb_auto
```

- Python 3.13（`py313_adb_auto`）
- ffmpeg：本机绿色版，路径在 `ukids_book/config.py`（`D:\ProgramFiles\ffmpeg-master-latest-win64-gpl`）
- 雷电模拟器自带 adb，无需另装（`E:\leidian\LDPlayer14`）

## 目录布局

```
├── doc/                 文档：idea/ 原始想法，plan/ 实现方案
├── ukids_book/          主源码包（adb/capture/subtitles/segment/compose/pipeline/cli）
├── scripts/             独立小工具（screenshot.py 截图脚本）
├── assets/              只读素材：从设备拉取的 SRT 字幕等
│   └── subtitles/
├── work/                中间产物（可随时删除重建）：分段录像、抽帧、旧测试截图
│   └── <视频名>/raw|frames/...
├── output/              最终交付：每集的长图 book.png 与画册 book.pdf
└── requirements.txt
```

数据流：模拟器播放 → ADB 录屏 → `work/` 分片抽帧 → `output/` 合成长图/PDF。

## 用法

```bash
# 截图小工具（单张 / 每30秒连拍）
python scripts/screenshot.py [-i 30]

# 1) 录制：模拟器里点开视频播放后执行（一集约 1275s）
python -m ukids_book.cli record --name E316 --duration 1275

# 2) 处理：自动对齐字幕 → 长图 + A4 PDF（装订边、中字小英字大）
python -m ukids_book.cli build --task work/E316_* --srt "assets/subtitles/*.srt"

# 字幕模式：auto(默认,对齐可靠用SRT文本,否则抠字幕条) | srt | band
python -m ukids_book.cli build --task work/xxx --sub band --no-png
```

## 已知事实

- 缓存视频为 AES-PRIVATE 加密，解密路线已放弃，改走录屏（见 idea 文档）
- SRT 字幕位于设备 `files/ukids/vod/222/`，UTF-8 带 BOM，中英双语
- screenrecord 单次上限 180s，采用 175s 链式分段录制
- 自动对齐：字幕带变化事件 × SRT 边界 Jaccard 匹配（无 OCR），得分≥0.75 用 SRT 文本
- band 模式：白字二值化字模去重，不依赖任何字幕文件
