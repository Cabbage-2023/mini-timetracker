# mini-timetracker

每日电脑活动追踪 + LLM 日报生成。截图 + 视觉 AI 分析，知道你在干什么，而不是只记应用名。

## 它做什么

```
每30秒截一次全屏
       ↓
一天结束后跑 report.py
       ↓
LLM 看所有截图，识别你在干什么
       ↓
生成日报：时间线 + 统计图表 + AI 点评
```

**日报长这样：**

```
09:02～10:30  在和 gal 群群友聊天（关于考研太卷了）
10:30～10:45  在 B 站看 up 主的抽象游戏视频
10:45～11:00  在 X 上搜施法素材
...
```

而不是 "Chrome 4小时" 这种没用的统计。

## 安装

```bash
pip install pillow openai matplotlib
```

三个依赖，不需要数据库，不需要独显（LLM 走 API，截图只用 CPU）。

## 配置

复制 `config.example.json` 为 `config.json`，填入你的 API Key：

```json
{
    "api_key": "sk-你的Key",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "sample_rate": 1
}
```

### 支持的 API（任选一个）

| 服务 | base_url | model | 费用 |
|------|----------|-------|------|
| **DeepSeek** | https://api.deepseek.com | deepseek-chat | ≈3元/月 |
| **智谱 GLM-4V-Flash** | https://open.bigmodel.cn/api/paas/v4 | glm-4v-flash | 免费 |
| **OpenAI** | https://api.openai.com/v1 | gpt-4o-mini | ≈$3/月 |

> 如果 API 返回内容被过滤（NSFW 内容），DeepSeek 最宽松，智谱次之。

`sample_rate`: 采样率。1=全部分析，2=隔一张分析一张。调大省钱但可能漏活动。

## 使用

### 采集

```bash
python collect.py
```

后台运行，每 30 秒截一次屏。按 Ctrl+C 停止。

截图存在 `screenshots/2026-06-19/14-02-33.jpg` 这种目录结构。

### 生成日报

```bash
python report.py           # 今天
python report.py yesterday # 昨天
python report.py 2026-06-19 # 指定日期
```

流程：
1. 加载当天截图 → 去重（静止画面不重复分析）
2. LLM 分析每张截图 → 识别在干什么
3. 合并同类活动为时间段 → 生成统计
4. 生成图表 + AI 幽默点评
5. 保存 Markdown 日报到 `reports/` 目录
6. 结构化数据写入 SQLite（方便后续查询统计）
7. 询问是否删除今日截图

### 分析缓存

report.py 有缓存机制，中途崩溃可恢复。缓存文件在 `reports/.cache_日期.json`。

想重新分析的话删掉缓存文件就行。

### 每天用完后

```
python report.py    # 看日报
y                   # 确认没问题，删截图
```

截图只保留到你确认日报无误那一刻，隐私安全。

## 数据库查询

每次跑 `report.py` 时，所有结构化数据（活动时间段、时长、标签、AI 点评等）会自动存入 `reports/data.db`（SQLite），方便长期汇总分析。

### 表结构

```sql
-- 每条活动时间段
daily_sessions:  date | start_time | end_time | duration_seconds |
                 activity | app | activity_type | tags | shot_count

-- 每天统计摘要
daily_stats:     date | total_shots | activity_type_dist |
                 top_apps | top_tags | ai_commentary
```

### 查询示例

```bash
# 这周每天花了多少时间在娱乐上
python -c "
import sqlite3
db = sqlite3.connect('reports/data.db')
rows = db.execute('''
    SELECT date, SUM(duration_seconds) as total
    FROM daily_sessions
    WHERE activity_type = '娱乐' AND date >= date('now', '-7 days')
    GROUP BY date ORDER BY date
''').fetchall()
for r in rows:
    print(f'{r[0]}: {r[1]//60}分钟')
"

# 本月最高频的应用 Top 5
python -c "
import sqlite3
db = sqlite3.connect('reports/data.db')
rows = db.execute('''
    SELECT app, SUM(shot_count) as cnt
    FROM daily_sessions WHERE date LIKE '2026-06%'
    GROUP BY app ORDER BY cnt DESC LIMIT 5
''').fetchall()
for r in rows:
    print(f'{r[0]}: {r[1]}次')
"

# 查某天的 AI 点评
python -c "
import sqlite3
db = sqlite3.connect('reports/data.db')
r = db.execute('SELECT ai_commentary FROM daily_stats WHERE date = ?', ('2026-06-19',)).fetchone()
print(r[0] if r else '暂无')
"
```

数据跑不掉，想怎么查怎么查。

## 文件结构

```
mini-timetracker/
├── config.json        ← API 配置
├── collect.py         ← 后台截图（30s/张）
├── report.py          ← 日报生成（LLM + 图表）
├── requirements.txt   ← 依赖
├── screenshots/       ← 截图（看完日报就删）
│   └── 2026-06-19/
│       └── 14-02-33.jpg
└── reports/           ← 日报输出
    ├── 2026-06-19.md   ← Markdown 日报
    ├── 2026-06-19.png  ← 统计图表
    ├── data.db         ← SQLite 数据库（累积所有历史）
    └── .cache_2026-06-19.json  ← 分析缓存
```

## 和其他方案对比

| | mini-timetracker | Screenpipe / Windrecorder |
|---|---|---|
| 核心思路 | LLM 理解截图内容 | OCR 搜文字 |
| 日报质量 | 知道你在干什么（聊天内容、视频内容等） | 只记了应用名+截图上搜到的文字 |
| 隐私 | 全本地，截图看完就删 | 全本地留存 |
| 费用 | API 几块钱/月 | 免费 / $21/月 |
| 代码量 | 2 个文件共 ~300 行 | 大型项目 |

## 隐私

- 截图存在你硬盘上，不上传任何地方
- LLM API 调用是加密传输，截图不存储到任何第三方
- 看完日报可一键删除当天所有截图
- 不想发给 API 的截图（比如极度私密内容）→ 采集时自动去重跳过，或者直接删掉对应截图文件再跑 report.py

## 已知限制

- Windows only（PIL ImageGrab）
- 没有手机端
- LLM 分析需要联网
- 大量截图分析可能需要几分钟
