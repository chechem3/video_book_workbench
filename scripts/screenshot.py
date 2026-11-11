# -*- coding: utf-8 -*-
"""
小小优趣自动截图 —— 雷电模拟器 ADB 截图脚本

用法:
  python scripts/screenshot.py              # 截一张，存到 work/screenshots/
  python scripts/screenshot.py -o D:/pics   # 截一张，存到指定目录
  python scripts/screenshot.py -i 30        # 每 30 秒截一张，Ctrl+C 停止
  python scripts/screenshot.py -i 10 -c 20  # 每 10 秒一张，共 20 张
  python scripts/screenshot.py -p 5555      # 指定模拟器 ADB 端口（多开时用）

依赖: 雷电模拟器自带的 adb.exe（自动定位，无需装 Android SDK）
"""

import argparse
import datetime as dt
import subprocess
import sys
import time
from pathlib import Path

# 常见雷电模拟器安装位置（按顺序探测）
LD_ADB_CANDIDATES = [
    r"E:\leidian\LDPlayer14\adb.exe",
    r"D:\leidian\LDPlayer9\adb.exe",
    r"C:\leidian\LDPlayer9\adb.exe",
    r"D:\Program Files\leidian\LDPlayer9\adb.exe",
    r"C:\Program Files\leidian\LDPlayer9\adb.exe",
]

DEFAULT_SERIAL = "emulator-5554"  # 雷电默认第一个实例

# 默认输出到项目 work/screenshots/（与脚本所在位置无关）
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "work" / "screenshots"


def find_adb() -> str:
    """定位 adb.exe：先看 PATH，再扫雷电脑目录。"""
    try:
        ver = subprocess.run(
            ["adb", "version"], capture_output=True, text=True, timeout=10
        )
        if ver.returncode == 0:
            return "adb"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    for p in LD_ADB_CANDIDATES:
        if Path(p).is_file():
            return p
    sys.exit("找不到 adb.exe，请把雷电话安装路径加进 LD_ADB_CANDIDATES")


def pick_device(adb: str) -> str:
    """返回在线设备 serial，没有则退出。

    同一台模拟器可能同时以 emulator-5554 和 127.0.0.1:5555 出现
    （本地 connect 与 USB 通道并存），优先取 emulator-* 并去重。
    """
    out = subprocess.run(
        [adb, "devices"], capture_output=True, text=True, timeout=10
    ).stdout
    devices = [
        line.split("\t")[0]
        for line in out.splitlines()[1:]
        if "\tdevice" in line
    ]
    if not devices:
        sys.exit("没有在线的模拟器设备，请先启动雷电模拟器")
    emus = [d for d in devices if d.startswith("emulator-")]
    serial = emus[0] if emus else devices[0]
    # 去掉与所选 emulator 同端口(5554+1)的 tcp 条目后再判断是否真多开
    dup_port = serial.split("-")[-1]
    others = [
        d for d in devices
        if d != serial and not d.endswith(f":{int(dup_port) + 1}")
    ]
    if others:
        print(f"检测到多台设备: {devices}，使用: {serial}")
    return serial


def shoot(adb: str, serial: str, out_dir: Path) -> Path:
    """截一张图，返回文件路径。exec-out 直接走二进制流，无换行污染。"""
    png = subprocess.run(
        [adb, "-s", serial, "exec-out", "screencap", "-p"],
        capture_output=True, timeout=30,
    ).stdout
    if not png.startswith(b"\x89PNG"):
        sys.exit(f"截图失败，adb 返回非 PNG 数据（{len(png)} 字节）")
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"shot_{ts}.png"
    path.write_bytes(png)
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="雷电模拟器 ADB 自动截图")
    ap.add_argument("-o", "--out", default=str(DEFAULT_OUT), help="输出目录")
    ap.add_argument("-i", "--interval", type=float, help="连续截图间隔秒数")
    ap.add_argument("-c", "--count", type=int, help="连续模式最多张数")
    args = ap.parse_args()

    adb = find_adb()
    # 雷电偶尔需要主动 connect 才出现在 devices 列表
    subprocess.run(
        [adb, "connect", "127.0.0.1:5555"],
        capture_output=True, text=True, timeout=10,
    )
    serial = pick_device(adb)
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"adb: {adb}  |  设备: {serial}  |  输出: {out_dir}")

    n = 0
    while True:
        n += 1
        path = shoot(adb, serial, out_dir)
        print(f"[{n:>3}] {path.name}  ({path.stat().st_size / 1024:.0f} KB)")
        if args.interval is None:
            break
        if args.count and n >= args.count:
            break
        time.sleep(args.interval)

    print(f"完成，共 {n} 张，目录: {out_dir}")


if __name__ == "__main__":
    main()
