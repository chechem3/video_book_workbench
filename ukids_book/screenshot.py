# -*- coding: utf-8 -*-
"""即时截图小工具：单张或定时连拍，输出 PNG 到指定目录。"""

import datetime as dt
import time
from pathlib import Path

from . import config
from .adb import AdbDevice


def shoot(out_dir: Path, interval: float | None = None,
          count: int | None = None, serial: str = config.DEVICE_SERIAL) -> list[Path]:
    """截图：interval 为空截一张；否则按间隔连拍，count 为空则到 Ctrl+C。

    返回已保存的文件路径列表。
    """
    dev = AdbDevice(serial)
    dev.ensure_ready()
    out_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    n = 0
    try:
        while True:
            t0 = time.time()
            ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = out_dir / f"shot_{ts}.png"
            path.write_bytes(dev.screenshot())
            paths.append(path)
            n += 1
            print(f"[{n:>3}] {path.name}  ({path.stat().st_size / 1024:.0f} KB)")
            if interval is None or (count and n >= count):
                break
            time.sleep(max(0.0, interval - (time.time() - t0)))
    except KeyboardInterrupt:
        pass
    print(f"完成，共 {n} 张 → {out_dir}")
    return paths
