# mini-timetracker

截图 + AI 视觉分析，自动生成知道你"在干什么"的每日活动日报。

```
每30秒截屏 → LLM 看懂每张截图 → 时间线 + 统计图表 + AI 吐槽
```

不是"Chrome 4小时"那种垃圾统计，而是：

```
09:02～10:30  💬 在和 gal 群群友聊天（考研太卷了）
10:30～10:45  🎮 在 B 站看 up 主抽象游戏视频
10:45～11:00  📌 在 X 上搜施法素材
15:00～15:30  💬 在和 AI 聊性癖
```

---

## 快速上手

### 0. 环境要求

- **Windows**（PIL ImageGrab 在 Windows 上最稳定）
- Python 3.8+
- 不需要独显（LLM 走 API），不需要数据库（SQLite 是 Python 自带的）

### 1. 安装

```bash
pip install -U pillow openai matplotlib
```

三个依赖装完即用。

### 2. 配置

复制 `config.example.json` 为 `config.json`，填入你的 API Key：

```json
{
    "api_key": "sk-你的Key",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat"
}
```

随便选一家：

| 服务 | base_url | model | 月费 |
|------|----------|-------|------|
| **DeepSeek** 👑 | `https://api.deepseek.com` | `deepseek-chat` | ~3元 |
| **智谱 GLM-4V-Flash** | `https://open.bigmodel.cn/api/paas/v4` | `glm-4v-flash` | 免费 |
| **OpenAI** | `https://api.openai.com/v1` | `gpt-4o-mini` | ~$3 |

> DeepSeek 对成人内容最宽松，智谱免费但可能过滤 NSFW，GPT-4o 费用最高。
>
> 如果你发现 DeepSeek 经常认错截图里的文字，换成智谱 GLM-4V-Flash 试试。

### 3. 采集

```bash
python collect.py
```

丢后台不用管了。每 30 秒截一次屏，**屏幕没变化自动跳过**（dHash 识别，连时钟跳分钟、光标闪烁都不会触发误存）。锁屏或 UAC 弹窗不会崩溃，自动恢复截图。

按 `Ctrl+C` 停止。

> **💡 开机自启（计划任务）**：打开「任务计划程序」→ 创建基本任务 → 触发器选"计算机启动时"→ 操作选"启动程序"→ 程序填 `python`，参数填 `collect.py`，起始于填本项目的绝对路径。因为所有路径都基于脚本所在目录，不用担心工作目录不对。

> **💡 日界说明**：一天的周期是**凌晨 04:00 到次日 04:00**，不是自然日 0 点。熬夜到凌晨 3 点打游戏，算的是昨天的娱乐时间，不会蒸发。

### 4. 生成日报

```bash
# 一天结束后跑这个
python report.py               # 今天（04:00～次日04:00）
python report.py yesterday     # 昨天
python report.py 2026-06-19    # 指定日期
```

跑完后会：
1. 加载今天所有截图 → dHash 去重过滤静止画面
2. **并发**调用 LLM 分析截图（默认 5 线程，可在 config 调）
3. 把同类活动合并成时间段（跨午夜也正确计算）
4. 生成统计图表 + AI 幽默点评
5. 存入 SQLite（长期保留，一年不到 10MB）
6. 问你要不要删截图 → 输入 `y` 删除原图释放磁盘，分析数据留在数据库

---

## 日报长什么样

### 时间线表格

```
| 时间段 | 活动 | 应用 | 时长 |
|--------|------|------|------|
| 09:02～10:30 | 💬 在和 gal 群群友聊天 | WeChat | 1h28m |
| 10:30～10:45 | 🎮 在 B 站看 up 主抽象游戏视频 | Chrome | 15m |
| 10:45～11:00 | 📌 在 X 上搜施法素材 | Chrome | 15m |
| 14:00～15:00 | 💼 写代码 | VSCode | 1h0m |
| 15:00～15:30 | 💬 在和 AI 聊性癖 | DeepSeek | 30m |
```

### 统计图表

饼图（活动类型分布）+ 条形图（应用排行），自动保存在日报同目录。

### AI 点评

每次生成一段幽默的每日总结。比如：

> 上午摸鱼水准不错，gal 群聊出了 KPI 感。下午突然切到 VSCode 编程——疑似良心发现。睡前和 AI 的深层交流保持了稳定产出。建议明天把 X 搜索时间控制在 20 分钟内（虽然你不会听的）。

---

## 数据库查询

所有历史数据存入 `reports/data.db`（SQLite），随时查：

```bash
# 这周娱乐时间合计
python -c "
import sqlite3
db = sqlite3.connect('reports/data.db')
for r in db.execute('''
    SELECT date, SUM(duration_seconds)/60
    FROM daily_sessions
    WHERE activity_type='娱乐' AND date>=date('now','-7 days')
    GROUP BY date ORDER BY date
''').fetchall():
    print(f'{r[0]}: {r[1]}分钟')
"

# 本月应用排行
python -c "
import sqlite3
db = sqlite3.connect('reports/data.db')
for r in db.execute('''
    SELECT app, SUM(shot_count) as c
    FROM daily_sessions WHERE date LIKE '2026-06%'
    GROUP BY app ORDER BY c DESC LIMIT 5
''').fetchall():
    print(f'{r[0]}: {r[1]}次')
"

# 某天的 AI 点评
python -c "
import sqlite3
r = sqlite3.connect('reports/data.db').execute(
    'SELECT ai_commentary FROM daily_stats WHERE date=?',
    ('2026-06-19',)
).fetchone()
print(r[0] if r else '无')
"
```

---

## 配置项

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `api_key` | — | API Key |
| `base_url` | `https://api.deepseek.com` | API 地址 |
| `model` | `deepseek-chat` | 模型名 |
| `sample_rate` | `1` | 分析采样率。`1`=全部分析，`2`=隔一张分析一张 |
| `analysis_threads` | `5` | LLM 分析并发线程数。截图多时可以调大 |
| `keep_days` | `7` | 截图保留天数。过期自动清理 |
| `screenshot_interval` | `30` | 截图间隔（秒） |

---

## 文件结构

```
mini-timetracker/
├── config.json           ← 你的 API 配置（已加入 .gitignore，不会误传）
├── config.example.json   ← 配置模板
├── collect.py            ← 截图采集（后台常驻）
├── report.py             ← 日报生成（LLM + 图表 + 数据库）
├── requirements.txt      ← 依赖
├── screenshots/          ← 截图缓存（看完日报就删）
│   └── 2026-06-19/
│       └── 14-02-33.jpg
└── reports/              ← 日报输出
    ├── 2026-06-19.md     ← Markdown 日报
    ├── 2026-06-19.png    ← 统计图表
    ├── data.db           ← SQLite 历史数据（累积）
    └── .cache_*.json     ← 分析缓存（断点续跑用，30天自动清理）
```

---

## 隐私说明

- 截图存在你硬盘上，**不上传任何地方**
- LLM API 调用是加密传输，截图不存储到第三方
- 看完日报一键删除当天所有截图，**截图不留痕**；分析结果（detail/tags）保留在本地 SQLite 中，方便日后回顾，一年不到 10MB
- 如果你想彻底删除所有数据，直接删掉 `reports/` 文件夹即可
- 如果某张截图你不想发给 API 看（比如极度私密内容），直接删掉对应的 `.jpg` 再跑 `report.py`，它不会问你要不存在的东西
- `config.json` 在 `.gitignore` 里，API Key 不会误传到 GitHub

---

## 和其他方案对比

| | mini-timetracker | Screenpipe / Windrecorder |
|---|---|---|
| 日报颗粒度 | LLM看懂你在干什么（群聊内容、视频内容、搜索词） | 只记了应用名+OCR搜到的文字 |
| 隐私 | 截图看完就删，不留痕迹 | 全本地永久留存 |
| 费用 | API 月费≈0～3元 | 免费 / $21/月 |
| 代码量 | ~700 行，两个文件 | 大型项目 |
| 硬件要求 | 不需要独显 | 需要 GPU（OCR/向量化） |

---

## 已知限制

- **仅 Windows**（PIL ImageGrab 跨平台支持有限，但改成 mss 库可扩展到 macOS/Linux）
- **没有手机端**
- **LLM 分析需要联网**
- 截图多的时候分析可能要几分钟（并发 5 线程，去重后几百张 ≈ 1-3 分钟）
