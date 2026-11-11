# -*- coding: utf-8 -*-
"""集中管理路径与参数常量，其他模块一律从这里取。"""

from pathlib import Path

# ---- 项目根与目录：work 可重建中间产物 / output 最终产物 ----
PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORK_DIR = PROJECT_ROOT / "work"
OUTPUT_DIR = PROJECT_ROOT / "output"

# ---- 外部工具 ----
ADB_CANDIDATES = [
    r"E:\leidian\LDPlayer14\adb.exe",
    r"D:\leidian\LDPlayer9\adb.exe",
    r"C:\leidian\LDPlayer9\adb.exe",
]
FFMPEG = r"D:\ProgramFiles\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe"
FFPROBE = r"D:\ProgramFiles\ffmpeg-master-latest-win64-gpl\bin\ffprobe.exe"

# 字幕渲染字体（页码/文本用，Windows 自带）
FONT_CJK = r"C:\Windows\Fonts\msyh.ttc"

# ---- 设备 ----
DEVICE_SERIAL = "emulator-5554"               # 雷电第一个实例

# ---- 录制 ----
RECORD_BITRATE = "16M"       # 1080p，打印用
RECORD_SIZE = "1920x1080"
SEGMENT_SECONDS = 175        # screenrecord 单次上限 180s，留重启余量
DEVICE_TMP_DIR = "/sdcard/ukcap"

# ---- 分帧与镜头 ----
EXTRACT_DT = 1.0             # 抽帧间隔（秒），下限 0.5
MIN_SHOTS = 4                # 最短镜头帧数（dt=1s 时 ≈4s）

# ---- 合成 ----
PAGE_WIDTH = 1200            # 长图宽度（px）
SEG_GAP = 40                 # 段间距
SUBTITLE_BAND_CROP = 0.18    # 主图底部字幕带裁剪比例（双行字幕上缘约 y=940）
