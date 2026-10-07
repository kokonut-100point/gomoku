# 五子棋 Gomoku · Python + α-β 剪枝 AI

一个前后端分离的五子棋：**Flask 后台**负责棋局与 AI，**网页版**和**桌面版**两个前端都只是"显示器 + 遥控器"。
AI 用 negamax + **α-β 剪枝**，三档难度，困难档带思考时间预算。

> A Gomoku (five-in-a-row) game with a Flask backend and two frontends — a browser canvas board and a Tkinter desktop client.
> The engine is a negamax search with **alpha-beta pruning**, plus three difficulty levels and a time budget for the hard level.

## 特性

- **两种前端，同一份棋局**：后台维护权威状态，多局并存；网页版和桌面版可以同时开着，互不干扰
- **AI：negamax + α-β 剪枝**
  - 简单：会漏挡冲四，从前几名里随机挑（适合新手）
  - 中等：固定深度 2 搜索
  - 困难：迭代加深 2 → 4 层 + 时间预算（超时就用上一层结果）
- **网页版零依赖**：手写 canvas 绘制 + WebAudio 现场合成音效，不用任何前端库、不用 CDN
- **桌面版纯标准库**：tkinter 画木纹棋盘、光泽棋子、落子动画、胜利金线脉冲；winsound 播放程序生成的 WAV 音效
- **可玩的细节**：悔棋、重开、人机/双人、执黑执白、一键静音、AI 耗时与搜索深度回显
- **测试齐全**：核心逻辑 23 项 + 后台接口 34 项 + 端到端 11 项，共 68 项

## 快速开始

需要 **Python 3.9+**；后台需要 `Flask`（仅此一个依赖）。

```bash
pip install -r requirements.txt
```

### ① 网页版（推荐先试这个）

```bash
python gomoku_server.py
# 然后浏览器打开 http://127.0.0.1:8099
```

Windows 上也可以直接双击 `gomoku-web.bat`（起后台 + 自动开浏览器）。

### ② 桌面版（Tkinter）

```bash
python gomoku.py
```

Windows 上双击 `gomoku.bat`。桌面版**不需要 Flask**，它通过 HTTP 调后台；后台没启动时它会自己拉起来。
音效依赖 `winsound`，仅在 Windows 生效；其它平台会静音运行（界面照常可用）。

### ③ 只跑后台 / 换端口

```bash
python gomoku_server.py --port 9000          # 换端口
python gomoku_server.py --host 0.0.0.0       # 允许局域网访问（默认只监听本机）
python gomoku_server.py --budget-scale 0.5   # AI 思考预算打五折，出手更快
python gomoku_server.py --debug              # 调试模式（改代码自动重载）
```

桌面版连非默认端口：设环境变量 `GOMOKU_API=http://127.0.0.1:9000`。

### Windows 启动器怎么找 Python

`gomoku.bat` / `gomoku-web.bat` / `gomoku-server.bat` / `test-gomoku-all.bat` 按以下顺序找解释器：

1. 环境变量 `GOMOKU_PY`（临时指定）
2. 同目录的 `.python-path` 文件（写一行解释器完整路径，该文件被 `.gitignore` 忽略）
3. PATH 里的 `python`，再退到 `py`（Windows Python 启动器）

> 启动器故意只用 ASCII 文本：cmd 解析含中文的 .bat 容易出错，中文提示都放在本文档和程序界面里。

## 架构

```
                 ┌────────────────────────────────┐
   浏览器 ──────▶ │  gomoku_server.py（Flask 后台） │ ◀────── Tkinter 客户端
  网页版棋盘      │  · 多局并存的对局服务           │        gomoku.py
                 │  · AI：negamax + α-β 剪枝      │
                 │  · 托管 web/ 网页棋盘           │
                 └───────────┬────────────────────┘
                             │ 共用
                    ┌────────▼─────────┐
                    │ gomoku_core.py   │  纯逻辑：棋盘 / 胜负 / 评估 / 搜索
                    └──────────────────┘
```

## 文件说明

| 文件 | 作用 |
|---|---|
| `gomoku_core.py` | 核心逻辑：棋盘、胜负判定、全盘连子评估、候选点排序、α-β 剪枝 negamax、时间预算 |
| `gomoku_server.py` | Flask 后台：对局 API + 托管网页棋盘 |
| `gomoku.py` | Tkinter 桌面客户端（后台没开就自己拉起） |
| `web/` | 网页版棋盘：`index.html` / `style.css` / `app.js` |
| `test-gomoku.py` | 核心逻辑自测（23 项） |
| `test-gomoku-server.py` | 后台接口自测（34 项，用 Flask test_client，不占端口） |
| `test-gomoku-client.py` | 端到端自测（11 项，真走 HTTP，会自动拉起后台） |
| `*.bat` | Windows 启动器（`gomoku.bat` 桌面版 / `gomoku-web.bat` 网页版 / `gomoku-server.bat` 只起后台 / `test-gomoku-all.bat` 跑测试） |

## HTTP API

所有接口返回 JSON；出错时形如 `{"error": {"code": "...", "message": "..."}}`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 + 引擎信息 |
| GET | `/api/games` | 列出内存中的所有对局 |
| POST | `/api/games` | 新建对局 `{mode?, difficulty?, humanColor?}`；人类执白时 AI 自动开局 |
| GET | `/api/games/<id>` | 查询一局状态 |
| DELETE | `/api/games/<id>` | 删除一局 |
| POST | `/api/games/<id>/moves` | 落子 `{r, c, ai?}`，`ai=true`（默认）时紧接着让 AI 应手 |
| POST | `/api/games/<id>/ai-move` | 单独让 AI 走一步 |
| POST | `/api/games/<id>/undo` | 悔棋 `{plies?}`（人机默认连 AI 那手一起撤） |
| POST | `/api/games/<id>/reset` | 重开（可同时改设置） |
| PATCH | `/api/games/<id>` | 修改设置 `{mode?, difficulty?, humanColor?}`，改了会重开一局 |

状态对象示例：

```json
{
  "id": "g-1a2b3c4d5e6f",
  "mode": "pve", "difficulty": "hard",
  "humanColor": "black", "aiColor": "white",
  "board": [[0, 0, "..."], ["...15x15..."]],
  "size": 15, "turn": "black", "status": "playing",
  "winner": null, "winCells": null, "lastMove": [7, 7],
  "moveCount": 2, "moves": [[7, 7, "black"], [8, 8, "white"]]
}
```

`board` 里 `0` 空 / `1` 黑 / `2` 白；`status` 为 `playing` / `won` / `draw`。

```bash
curl http://127.0.0.1:8099/api/health

# 开一局困难人机，人类执黑
curl -X POST http://127.0.0.1:8099/api/games \
  -H "Content-Type: application/json" \
  -d '{"mode":"pve","difficulty":"hard","humanColor":"black"}'

# 落子（后台顺手让 AI 应一手）
curl -X POST http://127.0.0.1:8099/api/games/<id>/moves \
  -H "Content-Type: application/json" -d '{"r":7,"c":7}'
```

## AI 是怎么下的

1. **一步取胜 / 一步被成五** → 直接出手，不做搜索；
2. **候选点**：已有棋子周围 2 格内的空点（空盘走天元）；
3. **排序**：按「对己方价值 + 对敌方价值」降序取前 12 个，让剪枝更容易剪掉坏分支；
4. **搜索**：negamax + **α-β 剪枝**，估值用全盘连子模式评估（横、竖、两个斜向的连续段）；
5. **难度**：简单（启发式 + 随机扰动）/ 中等（深度 2）/ 困难（迭代加深 2 → 4 + 时间预算）；
6. 每步的耗时与深度通过 `aiMeta` 回传，界面上会显示。

## 测试

```bash
python test-gomoku.py           # 23 项：胜负判定、AI 补五连/堵冲四、时间预算、棋盘序列化…
python test-gomoku-server.py    # 34 项：建局/落子/悔棋/重开/切难度/错误码/静态托管…
python test-gomoku-client.py    # 11 项：真·HTTP 端到端（需后台在跑，会自动拉起）
```

Windows 上也可以双击 `test-gomoku-all.bat` 一次跑完。

## 常见问题

| 现象 | 处理 |
|---|---|
| 网页提示"连不上后台" | 先运行 `gomoku_server.py`；换过端口就用 `http://127.0.0.1:新端口` |
| 端口被占用 | `python gomoku_server.py --port 9000`，客户端设 `GOMOKU_API` |
| 桌面版没声音 | 音效是 Windows 专属（`winsound`），其它平台静音运行 |
| 桌面版打不开 | 确认 Python 带 tkinter（Windows 官方安装包默认包含） |
| AI 想太久 / 太弱 | `--budget-scale 0.5` 更快，`2` 更强更慢 |
| 找不到 Python（bat） | 装 Python 并加入 PATH，或在同目录放 `.python-path` 文件 |

## 许可

MIT License，详见 [LICENSE](LICENSE)。

---

## English summary

A five-in-a-row (Gomoku) game in Python with a clean split between engine, server and clients:

- **Engine** (`gomoku_core.py`) — board, win detection, whole-board pattern evaluation, candidate ordering, and a **negamax search with alpha-beta pruning**. Three difficulty levels; the hard level uses iterative deepening (depth 2 → 4) under a wall-clock budget.
- **Server** (`gomoku_server.py`) — Flask app exposing a small REST API for multiple concurrent games, and serving the browser board from `web/`.
- **Clients** — a dependency-free browser board (hand-written canvas rendering + WebAudio sound effects) and a Tkinter desktop client that keeps its original look, sounds and animations while delegating all game state and AI moves to the server.

Run the web version with `python gomoku_server.py` and open <http://127.0.0.1:8099>; run the desktop version with `python gomoku.py`.
Requires Python 3.9+ and Flask (server only). Licensed under MIT.
