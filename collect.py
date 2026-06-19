"""
collect.py — 后台截图采集

每30秒截一次全屏，自动跳过静止画面（锁屏/待机恢复不爆发），
自动清理过期截图。

用法:  python collect.py
      按 Ctrl+C 停止
"""

import os, time, json, shutil, threading, queue
from datetime import datetime, timedelta
from PIL import ImageGrab

# ── 绝对路径（支持 Task Scheduler / 开机自启）──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 加载配置
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
INTERVAL = 30
KEEP_DAYS = 7

if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
        INTERVAL = cfg.get("screenshot_interval", 30)
        KEEP_DAYS = cfg.get("keep_days", 7)

DIR = os.path.join(BASE_DIR, "screenshots")
os.makedirs(DIR, exist_ok=True)


def dhash(img, hash_size=8):
    """计算图像 dHash（差异哈希），返回 64-bit 整数。

    原理：缩放到 (9×8) → 灰度 → 比较相邻像素 → 64 位指纹。
    汉明距离 ≤ 3 视为同一画面（容忍时钟跳动、光标闪烁等微小变化）。
    """
    gray = img.convert("L")
    resized = gray.resize((hash_size + 1, hash_size))
    h = 0
    for y in range(hash_size):
        for x in range(hash_size):
            h <<= 1
            if resized.getpixel((x, y)) > resized.getpixel((x + 1, y)):
                h |= 1
    return h


def hamming(h1, h2):
    """汉明距离：两个哈希值相差的位数。"""
    return bin(h1 ^ h2).count("1")


def get_screen():
    """截图并返回 (dHash, Image对象)。失败/超时/空帧时返回 (None, None)。"""
    q = queue.Queue()

    def _grab():
        try:
            q.put(ImageGrab.grab())
        except Exception as e:
            q.put(e)

    t = threading.Thread(target=_grab, daemon=True)
    t.start()
    t.join(15)  # 最多等 15 秒（防 ImageGrab 死锁）
    if t.is_alive():
        print("   ⚠ 截图超时 (ImageGrab 无响应)")
        time.sleep(5)
        return None, None
    try:
        result = q.get_nowait()
    except queue.Empty:
        print("   ⚠ 截图线程异常退出")
        time.sleep(5)
        return None, None
    if isinstance(result, Exception):
        print(f"   ⚠ 截图失败: {result}")
        time.sleep(5)
        return None, None
    if result is None:
        print("   ⚠ 截图返回空 (可能无显示器)")
        time.sleep(5)
        return None, None
    h = dhash(result)
    return h, result


def cleanup():
    """删除超过 KEEP_DAYS 天的截图目录"""
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    for name in os.listdir(DIR):
        folder = os.path.join(DIR, name)
        if not os.path.isdir(folder):
            continue
        try:
            if datetime.strptime(name, "%Y-%m-%d") < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
                print(f"   🧹 清理过期截图: {name}")
        except ValueError:
            continue


print(f"📷 每 {INTERVAL} 秒截图一次 | 全屏 1920×1080 JPEG")
print(f"   目录: {DIR}")
print(f"   保留: {KEEP_DAYS} 天 | 静止画面自动跳过 (dHash ≤3)")
print(f"   按 Ctrl+C 停止\n")

last_clean = time.time()
last_hash = None

try:
    while True:
        now = time.time()

        # ── 截图（屏幕没明显变化就跳过）──
        h, img = get_screen()
        if h is None:
            time.sleep(INTERVAL)
            continue

        if last_hash is None or hamming(h, last_hash) > 3:
            t = datetime.now()
            # 凌晨 04:00 前算前一天（日界为 04:00）
            session_key = (t - timedelta(hours=4)).strftime("%Y-%m-%d")
            folder = os.path.join(DIR, session_key)
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
