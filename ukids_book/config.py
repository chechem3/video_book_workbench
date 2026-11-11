# -*- coding: utf-8 -*-
"""集中管理路径与参数常量，其他模块一律从这里取。"""

from pathlib import Path

# ---- 项目根与三大目录：assets 只读素材 / work 可重建中间产物 / output 最终产物 ----
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PROJECT_ROOT / "assets"          # 从设备拉的 SRT 等
WORK_DIR = PROJECT_ROOT / "work"              # 分段录像、抽帧等中间产物，可随时删
OUTPUT_DIR = PROJECT_ROOT / "output"          # 长图、PDF 等最终交付

# ---- 外部工具 ----
# 雷电模拟器自带 adb，按顺序探测，第一个存在者生效
ADB_CANDIDATES = [
    r"E:\leidian\LDPlayer14\adb.exe",
    r"D:\leidian\LDPlayer9\adb.exe",
    r"C:\leidian\LDPlayer9\adb.exe",
]
FFMPEG = r"D:\ProgramFiles\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe"
FFPROBE = r"D:\ProgramFiles\ffmpeg-master-latest-win64-gpl\bin\ffprobe.exe"

# 字幕渲染字体（微软雅黑，Windows 自带）
FONT_CJK = r"C:\Windows\Fonts\msyh.ttc"

# ---- 设备 ----
DEVICE_SERIAL = "emulator-5554"               # 雷电第一个实例
# App 包名与设备侧路径
UKIDS_PACKAGE = "com.ukids.client.phone"
UKIDS_SRT_DIR = (
    "/sdcard/Android/data/com.ukids.client.phone/files/ukids/vod/222"
)

# ---- 录制 ----
RECORD_BITRATE = "16M"       # 1080p，打印用略高码率
RECORD_SIZE = "1920x1080"
SEGMENT_SECONDS = 175        # screenrecord 单次上限 180s，留重启余量
DEVICE_TMP_DIR = "/sdcard/ukcap"

# ---- 分片 ----
SEG_MODE = "scene"           # scene | gop | fixed
SCENE_THRESHOLD = 0.3        # 场景切分阈值
MIN_SEG_SECONDS = 8          # 最小段长，避免碎片段
MAX_SEG_SECONDS = 20         # 最大段长，超长强制切
SRT_TOLERANCE = 1.5          # 字幕时间匹配容差（秒）

# ---- 合成 ----
PAGE_WIDTH = 1200            # 长图宽度（px）
SEG_GAP = 40                 # 段间距
SUBTITLE_BAND_CROP = 0.18    # 关键帧底部字幕带裁剪比例（双行字幕上缘约 y=940）
