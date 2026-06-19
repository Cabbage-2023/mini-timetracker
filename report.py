#!/usr/bin/env python
"""
report.py — 日报生成器

分析当天截图 → LLM 识别活动 → 生成带时间线 + 统计图表 + AI 点评的日报

用法:
    python report.py               # 今天
    python report.py 2026-06-19    # 指定日期
    python report.py yesterday     # 昨天
"""

import os, sys, json, base64, shutil, re, time, io, sqlite3
from datetime import datetime, date, timedelta
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from PIL import Image
from openai import OpenAI
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# =====================================================================
#  配置
# =====================================================================
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH    = os.path.join(BASE_DIR, "config.json")
SCREENSHOT_DIR = os.path.join(BASE_DIR, "screenshots")
REPORT_DIR     = os.path.join(BASE_DIR, "reports")
DATABASE_PATH  = os.path.join(REPORT_DIR, "data.db")
GAP_MINUTES    = 120       # 同类型活动间隔 < 2h 合并（避免静止画面误断开）


# =====================================================================
#  工具函数
# =====================================================================

def load_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"xxx 找不到 {CONFIG_PATH}")
        print(f"   请复制 config.json 并填入你的 API Key")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    if "sk-在这里填" in cfg.get("api_key", ""):
        print("xxx 请先在 config.json 中填入你的 API Key")
        sys.exit(1)
    return cfg


def parse_date_arg(arg):
    if not arg:
        return date.today()
    arg = arg.strip().lower()
    if arg == "today":
        return date.today()
    if arg == "yesterday":
        return date.today() - timedelta(days=1)
    try:
        return datetime.strptime(arg, "%Y-%m-%d").date()
    except ValueError:
        print(f"xxx 日期格式错误: {arg}，应为 YYYY-MM-DD")
        sys.exit(1)


def load_screenshots(date_str):
    """加载截图（collect.py 已按 04:00 日界分文件夹，直接读即可）。"""
    folder = os.path.join(SCREENSHOT_DIR, date_str)
    if not os.path.isdir(folder):
        return []
    return sorted(
        os.path.join(folder, f)
        for f in os.listdir(folder) if f.endswith(".jpg")
    )


def dhash(img, hash_size=8):
    """dHash（差异哈希），64-bit 整数。"""
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
    """汉明距离：两个 dHash 相差的位数。"""
    return bin(h1 ^ h2).count("1")


def image_hash(path):
    with Image.open(path) as img:
        return dhash(img)


def dedup(paths):
    """dHash 去重，汉明距离 ≤ 3 视为同一画面。"""
    if not paths:
        return []
    result = [paths[0]]
    last_h = image_hash(paths[0])
    for p in paths[1:]:
        h = image_hash(p)
        if hamming(h, last_h) > 3:
            result.append(p)
            last_h = h
    return result


def encode_image(path, max_size=2048):
    with Image.open(path) as img:
        if img.width > max_size or img.height > max_size:
            img.thumbnail((max_size, max_size), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode()


def fmt_dur(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}秒"
    m = seconds // 60
    if m < 60:
        return f"{m}分钟"
    return f"{m // 60}小时{m % 60}分钟"


def extract_json(text):
    text = text.strip()
    for pattern in [
        lambda t: json.loads(t),
        lambda t: json.loads(re.search(r'\{.*\}', t, re.DOTALL).group()),
    ]:
        try:
            return pattern(text)
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue
    m = re.search(r'```(?:json)?\s*\n?(\{.*?\})\s*\n?```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    return None


def fallback_result(err):
    return {
        "app": "unknown", "activity": "分析失败",
        "detail": str(err), "tags": [], "activity_type": "其他"
    }


# =====================================================================
#  缓存管理
# =====================================================================

def _cache_path(ds):
    return os.path.join(REPORT_DIR, f".cache_{ds}.json")


def load_cache(ds):
    p = _cache_path(ds)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(ds, cache):
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(_cache_path(ds), "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# =====================================================================
#  LLM 分析一张截图
# =====================================================================

ANALYSIS_PROMPT = """分析这张截图，用户在干什么？
返回纯JSON（不要其他文字）：
{
  "app": "应用名",
  "activity": "一句话描述活动",
  "detail": "详细内容描述",
  "tags": ["标签1", "标签2"],
  "activity_type": "娱乐/社交/工作/学习/生活/其他"
}"""


def analyze_screenshot(client, model, image_path):
    b64 = encode_image(image_path)
    for attempt in range(2):
        try:
            kwargs = dict(
                model=model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": ANALYSIS_PROMPT},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
                    ]
                }],
                temperature=0.1,
                max_tokens=256,
            )
            # 首次请求使用 json_object 模式（准确率更高）
            if attempt == 0:
                kwargs["response_format"] = {"type": "json_object"}
            resp = client.chat.completions.create(**kwargs)
            result = extract_json(resp.choices[0].message.content)
            if result is None:
                if attempt == 0:
                    continue  # 重试一次，不用 json_object
                return fallback_result("回复不是合法JSON")
            result.setdefault("app", "unknown")
            result.setdefault("activity", "未知")
            result.setdefault("detail", "")
            result.setdefault("tags", [])
            result.setdefault("activity_type", "其他")
            return result
        except Exception as e:
            if attempt == 0:
                continue  # 重试一次
            return fallback_result(e)


def batch_analyze(paths, config, date_str, verbose=True):
    """并发分析截图，默认 5 线程。"""
    cache = load_cache(date_str)
    model = config.get("model", "gpt-4o-mini")
    sr = config.get("sample_rate", 1)
    sampled = paths[::sr]
    max_workers = config.get("analysis_threads", 5)

    if verbose:
        print(f"   采样率 1/{sr}，待分析 {len(sampled)} 张")

    results = [None] * len(sampled)
    uncached = []

    for i, path in enumerate(sampled):
        ts = os.path.basename(path).replace(".jpg", "")
        if path in cache:
            r = dict(cache[path])   # shallow copy → 不污染缓存对象
            r["time"] = ts
            results[i] = r
            if verbose:
                print(f"  [{i+1}/{len(sampled)}] {ts} -> (缓存) {r['activity']}")
        else:
            uncached.append((i, path))

    if verbose and uncached:
        print(f"   需分析 {len(uncached)} 张，并发 {max_workers} 线程")

    if uncached:
        # 每线程独立 client（httpx 连接池非线程安全）
        clients = [
            OpenAI(api_key=config["api_key"],
                   base_url=config.get("base_url"), timeout=60)
            for _ in range(max_workers)
        ]

        lock = threading.Lock()
        completed = 0

        def analyze_one(idx, path):
            nonlocal completed
            ts = os.path.basename(path).replace(".jpg", "")
            r = analyze_screenshot(clients[idx % max_workers], model, path)
            r["time"] = ts
            r["path"] = path
            with lock:
                cache[path] = r
                completed += 1
                if verbose:
                    print(f"  [{completed}/{len(uncached)}] {ts} -> {r['activity']}")
                if completed % 20 == 0:
                    save_cache(date_str, cache)
            return idx, r

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(analyze_one, idx, path) for idx, path in uncached]
            for fut in as_completed(futures):
                idx, r = fut.result()
                results[idx] = r

    save_cache(date_str, cache)
    return results


# =====================================================================
#  合并活动为时间段
# =====================================================================

def _dt_from_path(path, time_str):
    """从截图路径还原真实时间。

    collect.py 建文件夹时用了 (真实时间 - 4h)，所以文件夹名比真实日期偏早。
    这里先解析出 session 时间，如果小时 < 4 说明被偏移吞了一天，加回来。
    """
    folder = os.path.basename(os.path.dirname(path))
    dt = datetime.strptime(f"{folder} {time_str}", "%Y-%m-%d %H-%M-%S")
    if dt.hour < 4:
        dt += timedelta(days=1)
    return dt


def merge_sessions(analyses):
    if not analyses:
        return []

    sessions = []
    cur = None

    def fresh(a):
        dt = a.get("_dt")
        return {
            "start": a["time"], "end": a["time"],
            "start_dt": dt, "end_dt": dt,
            "app": a["app"], "activity": a["activity"],
            "detail": a["detail"],
            "tags": a["tags"][:], "activity_type": a["activity_type"],
            "shot_count": 1,
            "apps": set(), "activities": set(),
        }

    for a in analyses:
        a["_dt"] = _dt_from_path(a.get("path", ""), a["time"])

        if cur is None:
            cur = fresh(a)
            cur["apps"].add(a["app"])
            cur["activities"].add(a["activity"])
            continue

        same_type = a["activity_type"] == cur["activity_type"]
        same_app  = a["app"] == cur["app"]
        gap = (a["_dt"] - cur["end_dt"]).total_seconds() / 60

        if same_type and same_app and gap <= GAP_MINUTES:
            cur["end"] = a["time"]
            cur["end_dt"] = a["_dt"]
            cur["shot_count"] += 1
            cur["apps"].add(a["app"])
            cur["activities"].add(a["activity"])
            cur["tags"] = list(set(cur["tags"] + a["tags"]))
        else:
            sessions.append(cur)
            cur = fresh(a)
            cur["apps"].add(a["app"])
            cur["activities"].add(a["activity"])

    if cur:
        sessions.append(cur)

    for s in sessions:
        acts = s["activities"]
        s["activity"] = " | ".join(sorted(acts)) if len(acts) > 1 else list(acts)[0]
        apps = s["apps"]
        s["app"] = " / ".join(sorted(apps)) if len(apps) > 1 else list(apps)[0]
        s["duration_seconds"] = max(30, int((s["end_dt"] - s["start_dt"]).total_seconds()) + 30)
        # 清理内部临时字段
        del s["start_dt"], s["end_dt"], s["apps"], s["activities"]

    return sessions


# =====================================================================
#  统计
# =====================================================================

def compute_stats(sessions):
    type_cnt = Counter()
    app_cnt  = Counter()
    tag_cnt  = Counter()
    total    = sum(s["shot_count"] for s in sessions) or 1

    for s in sessions:
        type_cnt[s["activity_type"]] += s["shot_count"]
        app_cnt[s["app"]]            += s["shot_count"]
        for t in s["tags"]:
            tag_cnt[t] += s["shot_count"]

    return {
        "activity_type_dist": dict(type_cnt.most_common()),
        "activity_type_pct": {k: round(v / total * 100, 1)
                              for k, v in type_cnt.most_common()},
        "top_apps": dict(app_cnt.most_common(10)),
        "top_tags": dict(tag_cnt.most_common(15)),
        "total_shots": total,
    }


# =====================================================================
#  图表
# =====================================================================

def _setup_font():
    try:
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
        plt.rcParams["axes.unicode_minus"] = False
    except Exception:
        pass


def generate_chart(stats, output_path):
    _setup_font()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5.5))
    fig.patch.set_facecolor("#fafafa")

    dist = stats["activity_type_dist"]
    if dist:
        labels, sizes = list(dist.keys()), list(dist.values())
        colors = ["#ff9999", "#66b3ff", "#99ff99", "#ffcc99",
                   "#c2c2f0", "#ffb3e6", "#ffd966"]
        wedges, _, autotexts = ax1.pie(
            sizes, labels=None, autopct="%1.1f%%",
            startangle=90, colors=colors[:len(labels)],
            pctdistance=0.78, wedgeprops={"edgecolor": "white", "linewidth": 1.5},
        )
        for t in autotexts:
            t.set_fontsize(9)
        ax1.legend(
            wedges,
            [f"{l}  {s}次" for l, s in zip(labels, sizes)],
            loc="lower center", bbox_to_anchor=(0.5, -0.15),
            ncol=2, fontsize=9,
        )
        ax1.set_title("活动类型", fontsize=13, fontweight="bold", pad=15)
    else:
        ax1.text(0.5, 0.5, "暂无数据", ha="center", va="center")

    apps = stats["top_apps"]
    if apps:
        names  = list(apps.keys())[:8]
        counts = list(apps.values())[:8]
        bars = ax2.barh(range(len(names)), counts,
                        color="#66b3ff", edgecolor="white", height=0.6)
        ax2.set_yticks(range(len(names)))
        ax2.set_yticklabels(names, fontsize=9)
        ax2.set_title("应用使用次数 Top", fontsize=13, fontweight="bold", pad=15)
        ax2.tick_params(axis="x", labelsize=8)
        ax2.spines["top"].set_visible(False)
        ax2.spines["right"].set_visible(False)
        for bar, c in zip(bars, counts):
            ax2.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                     str(c), va="center", fontsize=9)
    else:
        ax2.text(0.5, 0.5, "暂无数据", ha="center", va="center")

    plt.tight_layout()
    plt.savefig(output_path, dpi=160, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close()


# =====================================================================
#  AI 点评（纯文字，不发图）
# =====================================================================

def generate_commentary(sessions, config):
    client = OpenAI(api_key=config["api_key"],
                    base_url=config.get("base_url"), timeout=30)
    model = config.get("model", "gpt-4o-mini")

    timeline = ["今日活动时间线："]
    for s in sessions:
        dur = s["duration_seconds"]
        timeline.append(f"- {s['start']}~{s['end']}（{fmt_dur(dur)}）{s['activity']}")

    prompt = (
        "以下是我今天的时间线，给一段幽默的每日点评。\n"
        "不要装正经，像朋友聊天一样自然。200字以内。\n\n"
        + "\n".join(timeline) + "\n\n点评："
    )

    try:
        resp = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}],
            temperature=0.8, max_tokens=300
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"（AI 点评生成失败: {e}）"


# =====================================================================
#  写 Markdown 日报
# =====================================================================

def write_report(date_str, sessions, stats, chart_rel, commentary):
    os.makedirs(REPORT_DIR, exist_ok=True)
    out = os.path.join(REPORT_DIR, f"{date_str}.md")

    emoji = {"娱乐": "🎮", "社交": "💬", "工作": "💼",
             "学习": "📚", "生活": "🏠", "其他": "📌"}

    lines = [f"# {date_str} 活动日报\n"]

    if os.path.exists(os.path.join(REPORT_DIR, chart_rel)):
        lines.append(f"![]({chart_rel})\n")

    lines.append("---\n## 时间线\n")
    lines.append("| 时间段 | 活动 | 应用 | 时长 |")
    lines.append("|--------|------|------|------|")
    for s in sessions:
        dur = s["duration_seconds"]
        e = emoji.get(s["activity_type"], "> ")
        lines.append(
            f"| {s['start']}~{s['end']} | {e} {s['activity']} "
            f"| {s['app'][:24]} | {fmt_dur(dur)} |"
        )

    lines.append("\n---\n## 统计\n")
    lines.append(f"- 分析有效截图：{stats['total_shots']} 张\n")
    lines.append("**活动类型占比：**\n")
    for at, pct in stats.get("activity_type_pct", {}).items():
        bar = "|" * max(int(pct / 4), 1)
        lines.append(f"- {at}: {bar} {pct}%\n")
    lines.append("\n**高频标签：**\n")
    for tag, cnt in list(stats.get("top_tags", {}).items())[:10]:
        lines.append(f"- {tag}（{cnt}）\n")

    lines.append("\n---\n## AI 点评\n")
    lines.append(f"> {commentary}\n")

    with open(out, "w", encoding="utf-8") as f:
        f.write("".join(lines))
    return out


# =====================================================================
#  数据库存储（结构化数据持久化）
# =====================================================================

def init_db():
    os.makedirs(REPORT_DIR, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            duration_seconds INTEGER,
            activity TEXT NOT NULL,
            app TEXT NOT NULL,
            activity_type TEXT,
            tags TEXT,
            detail TEXT,
            shot_count INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            total_shots INTEGER,
            activity_type_dist TEXT,
            top_apps TEXT,
            top_tags TEXT,
            ai_commentary TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def save_to_db(date_str, sessions, stats, commentary):
    conn = sqlite3.connect(DATABASE_PATH)

    # 覆盖式写入（重新跑 report.py 不会重复）
    conn.execute("DELETE FROM daily_sessions WHERE date = ?", (date_str,))
    conn.execute("DELETE FROM daily_stats WHERE date = ?", (date_str,))

    for s in sessions:
        dur = s["duration_seconds"]
        conn.execute("""
            INSERT INTO daily_sessions
                (date, start_time, end_time, duration_seconds, activity, app,
                 activity_type, tags, detail, shot_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            date_str, s["start"], s["end"], int(dur),
            s["activity"], s["app"], s["activity_type"],
            json.dumps(s["tags"], ensure_ascii=False),
            s.get("detail", ""), s["shot_count"]
        ))

    conn.execute("""
        INSERT INTO daily_stats
            (date, total_shots, activity_type_dist, top_apps, top_tags, ai_commentary)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        date_str, stats["total_shots"],
        json.dumps(stats["activity_type_dist"], ensure_ascii=False),
        json.dumps(stats["top_apps"], ensure_ascii=False),
        json.dumps(stats["top_tags"], ensure_ascii=False),
        commentary
    ))

    conn.commit()
    conn.close()


# =====================================================================
#  删除截图
# =====================================================================

def ask_delete(date_str):
    ans = input("\n确认日报无误，可以删除今日截图？(y/n): ").strip().lower()
    if ans == "y":
        folder = os.path.join(SCREENSHOT_DIR, date_str)
        try:
            if os.path.isdir(folder):
                shutil.rmtree(folder, ignore_errors=True)
                print(f"已删除截图: {date_str}")
        except Exception as e:
            print(f"删除截图文件夹失败: {e}")

        cp = _cache_path(date_str)
        if os.path.exists(cp):
            try:
                os.remove(cp)
                print(f"已删除分析缓存")
            except Exception as e:
                print(f"删除缓存文件失败: {e}")


def clean_old_caches():
    """删除超过 30 天的 .cache_*.json 文件。"""
    cutoff = time.time() - 30 * 86400
    if not os.path.isdir(REPORT_DIR):
        return
    for f in os.listdir(REPORT_DIR):
        if f.startswith(".cache_") and f.endswith(".json"):
            p = os.path.join(REPORT_DIR, f)
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
                    print(f"   🧹 清理过期缓存: {f}")
            except Exception:
                pass


# =====================================================================
#  主入口
# =====================================================================

def main():
    clean_old_caches()
    config = load_config()
    target = parse_date_arg(sys.argv[1] if len(sys.argv) > 1 else None)
    ds = target.isoformat()

    print(f"分析 {ds} 的活动...\n")

    all_shots = load_screenshots(ds)
    if not all_shots:
        print(f"xxx {ds} 没有截图数据，请先运行: python collect.py")
        sys.exit(1)
    print(f"原始截图: {len(all_shots)} 张")

    unique = dedup(all_shots)
    print(f"去重后:   {len(unique)} 张（静止画面已过滤）")

    print(f"\n开始 LLM 分析...")
    analyses = batch_analyze(unique, config, ds)

    sessions = merge_sessions(analyses)
    print(f"\n合并为 {len(sessions)} 个活动时间段")

    stats = compute_stats(sessions)

    chart_rel = f"{ds}.png"
    try:
        generate_chart(stats, os.path.join(REPORT_DIR, chart_rel))
        print(f"图表已生成")
    except Exception as e:
        print(f"图表生成失败: {e}")

    print(f"生成 AI 点评...")
    commentary = generate_commentary(sessions, config)

    report_path = write_report(ds, sessions, stats, chart_rel, commentary)
    print(f"\n日报已保存: {report_path}")

    init_db()
    save_to_db(ds, sessions, stats, commentary)
    print(f"数据库已更新: {DATABASE_PATH}")

    with open(report_path, encoding="utf-8") as f:
        content = f.read()
    print("\n" + "=" * 56)
    print(content[:2500] + ("\n...(截断)" if len(content) > 2500 else ""))
    print("=" * 56)

    ask_delete(ds)


if __name__ == "__main__":
    main()
