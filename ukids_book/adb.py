# -*- coding: utf-8 -*-
"""设备层：adb 定位、shell/pull 封装。全部 subprocess 列表参数，不经 shell。"""

import subprocess
import sys
from pathlib import Path

from . import config


def find_adb() -> str:
    """PATH 优先，其次雷电安装目录列表。"""
    try:
        r = subprocess.run(["adb", "version"], capture_output=True, timeout=10)
        if r.returncode == 0:
            return "adb"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    for p in config.ADB_CANDIDATES:
        if Path(p).is_file():
            return p
    sys.exit("找不到 adb.exe，请检查 config.ADB_CANDIDATES")


class AdbDevice:
    def __init__(self, serial: str = config.DEVICE_SERIAL):
        self.adb = find_adb()
        self.serial = serial

    def shell(self, *args: str, timeout: float | None = None,
              check: bool = True) -> str:
        """执行 adb shell 命令，返回 stdout 文本。"""
        cmd = [self.adb, "-s", self.serial, "shell", *args]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if check and r.returncode != 0:
            raise RuntimeError(f"adb shell 失败: {args}\n{r.stderr}")
        return r.stdout

    def shell_bg(self, *args: str) -> subprocess.Popen:
        """启动阻塞型 shell 命令（如 screenrecord），返回 Popen 便于等待/终止。"""
        cmd = [self.adb, "-s", self.serial, "shell", *args]
        return subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def pull(self, remote: str, local: str | Path) -> None:
        r = subprocess.run(
            [self.adb, "-s", self.serial, "pull", remote, str(local)],
            capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"adb pull 失败: {remote}\n{r.stderr}")

    def screenshot(self) -> bytes:
        """截取当前屏幕，返回 PNG 字节。"""
        r = subprocess.run(
            [self.adb, "-s", self.serial, "exec-out", "screencap", "-p"],
            capture_output=True, timeout=30)
        if not r.stdout.startswith(b"\x89PNG"):
            raise RuntimeError("截图失败，adb 返回非 PNG 数据")
        return r.stdout

    def ensure_ready(self) -> None:
        """确保设备在线（雷电偶尔需要主动 connect）。"""
        subprocess.run(
            [self.adb, "connect", "127.0.0.1:5555"],
            capture_output=True, text=True, timeout=10)
        out = subprocess.run(
            [self.adb, "devices"], capture_output=True, text=True,
            timeout=10).stdout
        online = [
            ln.split("\t")[0] for ln in out.splitlines()[1:]
            if f"\tdevice" in ln
        ]
        if not online:
            raise RuntimeError("没有在线设备，请先启动雷电模拟器")
        if self.serial not in online and "emulator-" not in self.serial:
            raise RuntimeError(f"设备 {self.serial} 不在线: {online}")
