"""
collect.py — 后台截图采集

每30秒截一次全屏，自动跳过静止画面（锁屏/待机恢复不爆发），
自动清理过期截图。

用法:  python collect.py
      按 Ctrl+C 停止
"""

import os, time, json, shutil
from datetime import datetime, timedelta
from hashlib import md5
from PIL import ImageGrab

# 加载配置
CONFIG_PATH = "config.json"
INTERVAL = 30
KEEP_DAYS = 7

if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
        INTERVAL = cfg.get("screenshot_interval", 30)
        KEEP_DAYS = cfg.get("keep_days", 7)

DIR = "screenshots"
os.makedirs(DIR, exist_ok=True)


def screen_hash():
    """当前屏幕的内容指纹 + Image 对象，避免重复截屏"""
    img = ImageGrab.grab()
    small = img.resize((16, 16))
    h = md5(small.tobytes()).hexdigest()
    return h, img


def cleanup():
    """删除超过 KEEP_DAYS 天的截图目录"""
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    for name in os.listdir(DIR):
        folder = os.path.join(DIR, name)
        if not os.path.isdir(folder):
            continue
        try:
            if datetime.strptime(name, "%Y-%m-%d") < cutoff:
                shutil.rmtree(folder)
                print(f"   🧹 清理过期截图: {name}")
        except ValueError:
            continue


print(f"📷 每 {INTERVAL} 秒截图一次 | 全屏 1920×1080 JPEG")
print(f"   目录: {os.path.abspath(DIR)}")
print(f"   保留: {KEEP_DAYS} 天 | 静止画面自动跳过")
print(f"   按 Ctrl+C 停止\n")

last_clean = time.time()
last_hash = None

try:
    while True:
        now = time.time()

        # ── 截图（屏幕没变就跳过）──
        h, img = screen_hash()
        if h != last_hash:
            t = datetime.now()
            folder = os.path.join(DIR, t.strftime("%Y-%m-%d"))
            os.makedirs(folder, exist_ok=True)
            img.save(
                os.path.join(folder, t.strftime("%H-%M-%S.jpg")),
                "JPEG", quality=85,
            )
            last_hash = h

        # ── 每小时清理一次过期截图 ──
        if now - last_clean > 3600:
            cleanup()
            last_clean = now

        time.sleep(INTERVAL)

except KeyboardInterrupt:
    print("\n已停止")
