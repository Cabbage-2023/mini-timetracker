"""
collect.py — 后台截图采集
每30秒截一次全屏，存到 screenshots/YYYY-MM-DD/HH-MM-SS.jpg

用法:  python collect.py
      按 Ctrl+C 停止
"""

import os, time, json
from datetime import datetime
from PIL import ImageGrab

# 加载配置（只取截图间隔）
CONFIG_PATH = "config.json"
INTERVAL = 30
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
        INTERVAL = cfg.get("screenshot_interval", 30)

DIR = "screenshots"
os.makedirs(DIR, exist_ok=True)

print(f"📷 每 {INTERVAL} 秒截图一次 | 全屏 1920×1080 JPEG")
print(f"   目录: {os.path.abspath(DIR)}")
print(f"   按 Ctrl+C 停止\n")

try:
    while True:
        now = datetime.now()
        folder = os.path.join(DIR, now.strftime("%Y-%m-%d"))
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, now.strftime("%H-%M-%S.jpg"))
        ImageGrab.grab().save(path, "JPEG", quality=85)
        time.sleep(INTERVAL)
except KeyboardInterrupt:
    print("\n已停止")
