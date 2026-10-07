# -*- coding: utf-8 -*-
"""
五子棋 · Gomoku（Tkinter 客户端版）

- 界面与音效跟以前一样：木纹棋盘、光泽立体棋子、落子动画、胜利金线脉冲、结算音效
- 但「棋局状态 + AI 走子」现在由后台服务负责（gomoku_server.py）：
  落子、悔棋、重开、难度/模式切换都通过 HTTP 调用后台，AI 的 α-β 剪枝搜索在后台跑
- 后台没启动时，本程序会自己把它拉起来（日志写到 .gomoku-server.log）
- 纯标准库：tkinter + winsound + wave + urllib（HTTP 客户端），零第三方依赖

运行：双击 gomoku.bat，或 python gomoku.py
      想换后台地址：设置环境变量 GOMOKU_API，例如 http://127.0.0.1:9000
"""
import json
import math
import os
import random
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
import queue
import tkinter as tk

try:
    import winsound
except ImportError:      # 非 Windows 上仍可运行（只是没声音）
    winsound = None

import gomoku_core as core

N = core.N
EMPTY, BLACK, WHITE = core.EMPTY, core.BLACK, core.WHITE

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_SCRIPT = os.path.join(HERE, "gomoku_server.py")
SERVER_LOG = os.path.join(HERE, ".gomoku-server.log")
DEFAULT_API = os.environ.get("GOMOKU_API", "http://127.0.0.1:8099")
HTTP_TIMEOUT = 60


# =========================================================
# 与后台通信
# =========================================================
class ApiError(Exception):
    def __init__(self, message, code=None, status=None):
        super().__init__(message)
        self.code = code
        self.status = status


class ApiClient:
    def __init__(self, base_url=DEFAULT_API):
        self.base = base_url.rstrip("/")

    def _call(self, method, path, payload=None):
        url = self.base + path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            message, code = raw, None
            try:
                err = json.loads(raw).get("error") or {}
                message, code = err.get("message", raw), err.get("code")
            except Exception:
                pass
            raise ApiError(message, code, e.code) from None
        except urllib.error.URLError as e:
            raise ApiError(f"连不上后台（{self.base}）：{e.reason}", "unreachable") from None
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            raise ApiError("后台返回了无法解析的内容", "bad-json") from None

    # ---- 具体接口 ----
    def health(self):
        return self._call("GET", "/api/health")

    def create_game(self, mode, difficulty, human_color):
        return self._call("POST", "/api/games", {
            "mode": mode, "difficulty": difficulty, "humanColor": human_color,
        })

    def move(self, game_id, r, c, with_ai=True):
        return self._call("POST", f"/api/games/{game_id}/moves", {"r": r, "c": c, "ai": with_ai})

    def undo(self, game_id):
        return self._call("POST", f"/api/games/{game_id}/undo", {})

    def reset(self, game_id):
        return self._call("POST", f"/api/games/{game_id}/reset", {})

    def configure(self, game_id, mode, difficulty, human_color):
        return self._call("PATCH", f"/api/games/{game_id}", {
            "mode": mode, "difficulty": difficulty, "humanColor": human_color,
        })


def ensure_server(client, status_cb=None):
    """后台没起来就自己拉一个（detached），返回 (是否可用, 说明文字)。"""
    try:
        client.health()
        return True, "后台已在运行"
    except ApiError:
        pass
    if not os.path.exists(SERVER_SCRIPT):
        return False, f"找不到后台脚本：{SERVER_SCRIPT}"
    if status_cb:
        status_cb("正在启动后台服务…")
    try:
        log = open(SERVER_LOG, "a", encoding="utf-8")
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([sys.executable, SERVER_SCRIPT, "--quiet"],
                         cwd=HERE, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                         creationflags=flags, close_fds=True)
    except Exception as e:      # 拉不起来就给个明白话，界面之外不崩
        return False, f"启动后台失败：{e}"
    for _ in range(50):         # 最多等 10 秒
        time.sleep(0.2)
        try:
            client.health()
            return True, "后台已启动"
        except ApiError:
            continue
    return False, f"后台启动超时，详见日志：{SERVER_LOG}"


# =========================================================
# 音效：程序生成 WAV + winsound 播放（Windows 标准库）
# =========================================================
class SoundManager:
    RATE = 44100

    def __init__(self, root):
        self.root = root
        self.muted = False
        self.dir = os.path.join(HERE, ".gomoku-sounds")
        self.files = {}
        if winsound is None:
            self.muted = True
            return
        os.makedirs(self.dir, exist_ok=True)
        self.files = {
            "stone": self._save("stone", self._stone()),
            "win": self._save("win", self._win()),
            "draw": self._save("draw", self._draw()),
            "undo": self._save("undo", self._undo()),
        }

    # ---------- 采样合成 ----------
    def _tone(self, freq, dur, vol=0.5, shape="sine", decay=True):
        n = int(self.RATE * dur)
        out = []
        for i in range(n):
            t = i / self.RATE
            if shape == "sine":
                v = math.sin(2 * math.pi * freq * t)
            elif shape == "triangle":
                v = 2 / math.pi * math.asin(math.sin(2 * math.pi * freq * t))
            else:  # noise
                v = random.uniform(-1, 1)
            env = math.exp(-t * (3.0 / dur)) if decay else 1.0
            out.append(max(-1.0, min(1.0, v * env * vol)))
        return out

    def _stone(self):
        # 木子落盘：低频短促敲击 + 轻微噪声
        s = self._tone(170, 0.09, 0.55, "sine") + self._tone(90, 0.07, 0.35, "sine")
        s = [a * 0.6 + b * 0.4 for a, b in zip(s, self._tone(0, 0.09, 0, "sine"))] if len(s) else s
        return s

    def _win(self):
        # 胜利：上行琶音 C5-E5-G5-C6
        out = []
        for f in (523.25, 659.25, 783.99, 1046.50):
            out += self._tone(f, 0.16, 0.5, "sine")
        return out

    def _draw(self):
        # 平局：两声下行
        return self._tone(392, 0.18, 0.45) + self._tone(330, 0.22, 0.45)

    def _undo(self):
        return self._tone(240, 0.08, 0.4) + self._tone(180, 0.1, 0.35)

    def _save(self, name, samples):
        path = os.path.join(self.dir, name + ".wav")
        if os.path.exists(path):
            return path
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.RATE)
            w.writeframes(b"".join(struct.pack("<h", int(s * 32767)) for s in samples))
        return path

    def play(self, name):
        if self.muted or winsound is None or name not in self.files:
            return
        try:
            winsound.PlaySound(self.files[name], winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            pass


# =========================================================
# Tkinter 界面
# =========================================================
CELL = 40
PAD = 34
SIZE = PAD * 2 + CELL * (N - 1)
OFFSET = 6          # 木框内缩，跟内层棋盘对齐

BG = "#131831"
PANEL = "#1e2547"
WOOD_OUTER = "#b98a4a"
WOOD = "#e5b877"
WOOD_DARK = "#d3a15f"
GRID = "#5c3a14"
STAR = "#5c3a14"
TEXT = "#eef1fb"
MUTED = "#aab2d4"
ACCENT = "#6ee7b7"
GOLD = "#fbbf24"
RED = "#f87171"
BTN = "#3b4380"
BTN_OK = "#0ea371"


class GomokuApp:
    def __init__(self, root, client=None):
        self.root = root
        root.title("五子棋 · Gomoku（后台版）")
        root.configure(bg=BG)
        root.resizable(False, False)

        self.client = client or ApiClient()
        self.game_id = None
        self.state = None            # 后台返回的棋局状态
        self.mode = "pve"
        self.difficulty = "medium"
        self.human_color = "black"

        self.thinking = False        # 请求进行中
        self.hover = None
        self.anim = {}               # (r,c) -> 起始毫秒，用于落子动画
        self.pulse_on = True
        self.sound = SoundManager(root)
        self.results = queue.Queue()  # 工作线程 → 主线程
        self.ai_tip = ""

        self._build_ui()
        self.root.after(60, self._poll_results)
        self.root.after(30, self._boot)

    # ---------- 界面构建 ----------
    def _build_ui(self):
        top = tk.Frame(self.root, bg=BG)
        top.pack(pady=(14, 6))
        tk.Label(top, text="五 子 棋", font=("Microsoft YaHei", 22, "bold"),
                 fg=GOLD, bg=BG).pack()
        tk.Label(top, text="GOMOKU · 连珠 · 后台版", font=("Microsoft YaHei", 9),
                 fg=MUTED, bg=BG).pack()

        # 棋盘（外层木框用 canvas 绘制）
        self.canvas = tk.Canvas(self.root, width=SIZE, height=SIZE, bg=WOOD_OUTER,
                                highlightthickness=0, cursor="hand2")
        self.canvas.pack(padx=16, pady=8)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(None))

        # 控制区
        ctrl = tk.Frame(self.root, bg=BG)
        ctrl.pack(pady=6)

        self.mode_var = tk.StringVar(value="pve")
        self.diff_var = tk.StringVar(value="medium")
        self.color_var = tk.StringVar(value="black")

        for lbl, var, opts in (
            ("模式", self.mode_var, [("人机", "pve"), ("双人", "pvp")]),
            ("难度", self.diff_var, [("简单", "easy"), ("中等", "medium"), ("困难", "hard")]),
            ("执子", self.color_var, [("黑·先", "black"), ("白·后", "white")]),
        ):
            f = tk.Frame(ctrl, bg=BG)
            f.pack(side="left", padx=7)
            tk.Label(f, text=lbl, fg=MUTED, bg=BG, font=("Microsoft YaHei", 10)).pack(side="left", padx=(0, 3))
            for text, val in opts:
                tk.Radiobutton(f, text=text, value=val, variable=var, fg=TEXT, bg=BG,
                               selectcolor=PANEL, activebackground=BG, activeforeground=TEXT,
                               font=("Microsoft YaHei", 10), command=self._on_option).pack(side="left", padx=1)

        btns = tk.Frame(self.root, bg=BG)
        btns.pack(pady=(4, 6))
        self.undo_btn = tk.Button(btns, text="↩ 悔棋", command=self._undo, width=8, relief="flat",
                                  bg=BTN, fg=TEXT, activebackground="#2c3365", activeforeground=TEXT,
                                  font=("Microsoft YaHei", 10))
        self.undo_btn.pack(side="left", padx=6)
        self.new_btn = tk.Button(btns, text="重新开始", command=self._new_game, width=10, relief="flat",
                                 bg=BTN_OK, fg="#04211a", activebackground="#0b8a5f",
                                 font=("Microsoft YaHei", 10, "bold"))
        self.new_btn.pack(side="left", padx=6)
        self.sound_btn = tk.Button(btns, text="🔊 音效：开", command=self._toggle_sound,
                                   width=10, relief="flat", bg=PANEL, fg=TEXT,
                                   activebackground="#2c3365", font=("Microsoft YaHei", 10))
        self.sound_btn.pack(side="left", padx=6)

        self.status = tk.Label(self.root, text="", fg=TEXT, bg=BG, font=("Microsoft YaHei", 11, "bold"))
        self.status.pack(pady=(0, 2))
        self.foot = tk.Label(self.root, text="", fg=MUTED, bg=BG, font=("Microsoft YaHei", 8))
        self.foot.pack(pady=(0, 12))

    # ---------- 启动：确认后台 → 开一局 ----------
    def _boot(self):
        self.status.config(text="连接后台中…")

        def work():
            ok, note = ensure_server(self.client, lambda t: self.results.put(("status", t)))
            if not ok:
                self.results.put(("fatal", note))
                return
            try:
                res = self.client.create_game(self.mode, self.difficulty, self.human_color)
                self.results.put(("created", (res, note)))
            except ApiError as e:
                self.results.put(("fatal", e.args[0]))

        threading.Thread(target=work, daemon=True).start()

    # ---------- 工作线程 → 主线程 ----------
    def _poll_results(self):
        try:
            while True:
                kind, payload = self.results.get_nowait()
                self._handle_result(kind, payload)
        except queue.Empty:
            pass
        self.root.after(60, self._poll_results)

    def _handle_result(self, kind, payload):
        if kind == "status":
            self.status.config(text=payload, fg=GOLD)
            return
        if kind == "fatal":
            self.status.config(text=f"⚠ {payload}", fg=RED)
            self.foot.config(text="后台：%s（可用 python gomoku_server.py 手动启动）" % self.client.base)
            return
        if kind == "error":
            self.thinking = False
            self.status.config(text=f"⚠ {payload}", fg=RED)
            self._sync_buttons()
            return
        if kind == "created":
            res, note = payload
            self.thinking = False
            self._apply(res, animate=True, silent=True)
            self.foot.config(text=f"后台：{self.client.base} · {note}")
            return
        if kind in ("moved", "undone", "reset", "configured"):
            self.thinking = False
            self._apply(payload, animate=(kind != "undone"),
                        silent=(kind == "undone"))
            return

    # ---------- 绘制 ----------
    def _xy(self, r, c):
        return PAD + c * CELL, PAD + r * CELL

    def _cell(self, x, y):
        c = round((x - PAD - OFFSET) / CELL)
        r = round((y - PAD - OFFSET) / CELL)
        return (r, c) if core.in_bounds(r, c) else None

    def _stone_at(self, r, c):
        return self.state["board"][r][c] if self.state else EMPTY

    def _draw(self):
        cv = self.canvas
        cv.delete("all")
        # 木框
        cv.create_rectangle(0, 0, SIZE, SIZE, fill=WOOD_OUTER, outline="")
        cv.create_rectangle(OFFSET, OFFSET, SIZE - OFFSET, SIZE - OFFSET, fill=WOOD_DARK, outline="")
        cv.create_rectangle(OFFSET * 2, OFFSET * 2, SIZE - OFFSET * 2, SIZE - OFFSET * 2,
                            fill=WOOD, outline="")
        # 网格
        for i in range(N):
            x0, y0 = self._xy(i, 0)
            x1, y1 = self._xy(i, N - 1)
            cv.create_line(x0 + OFFSET, y0 + OFFSET, x1 + OFFSET, y1 + OFFSET, fill=GRID)
            cv.create_line(y0 + OFFSET, x0 + OFFSET, y1 + OFFSET, x1 + OFFSET, fill=GRID)
        cv.create_rectangle(PAD + OFFSET, PAD + OFFSET,
                            PAD + OFFSET + CELL * (N - 1), PAD + OFFSET + CELL * (N - 1),
                            outline=GRID, width=2)
        # 星位
        for r, c in ((3, 3), (3, 11), (11, 3), (11, 11), (7, 7)):
            x, y = self._xy(r, c)
            cv.create_oval(x + OFFSET - 3, y + OFFSET - 3, x + OFFSET + 3, y + OFFSET + 3,
                           fill=STAR, outline="")

        # 悬停虚影
        if self.hover and self._can_play():
            r, c = self.hover
            if self._stone_at(r, c) == EMPTY:
                color = BLACK if self.state["turn"] == "black" else WHITE
                self._stone(r, c, color, ghost=True, scale=0.55)

        # 棋子
        if self.state:
            now = self.root.tk.call("clock", "milliseconds")
            for r in range(N):
                for c in range(N):
                    v = self.state["board"][r][c]
                    if v == EMPTY:
                        continue
                    scale = 1.0
                    if (r, c) in self.anim:
                        p = min(1.0, (now - self.anim[(r, c)]) / 150.0)
                        scale = 1.25 - 0.25 * p          # 从略大落到正常，像拍下去
                    if scale > 0.999:
                        self.anim.pop((r, c), None)
                    self._stone(r, c, v, scale=scale)
            last = self.state.get("lastMove")
            if last:
                self._stone(last[0], last[1], self._stone_at(last[0], last[1]), last=True)
            cells = self.state.get("winCells")
            if cells and len(cells) >= 2:
                (r0, c0), (r1, c1) = cells[0], cells[-1]
                x0, y0 = self._xy(r0, c0)
                x1, y1 = self._xy(r1, c1)
                width = 6 if self.pulse_on else 3
                cv.create_line(x0 + OFFSET, y0 + OFFSET, x1 + OFFSET, y1 + OFFSET,
                               fill=GOLD, width=width, capstyle="round")

    def _stone(self, r, c, color, last=False, ghost=False, scale=1.0):
        cv = self.canvas
        x, y = self._xy(r, c)
        x += OFFSET
        y += OFFSET
        rad = CELL * 0.42 * scale
        # 阴影
        if not ghost:
            cv.create_oval(x - rad + 1, y - rad + 3, x + rad + 1, y + rad + 5,
                           fill="#000000", outline="", stipple="gray50")
        if color == BLACK:
            cv.create_oval(x - rad, y - rad, x + rad, y + rad, fill="#1b2230", outline="#0a0d14")
            cv.create_oval(x - rad * 0.62, y - rad * 0.72, x - rad * 0.02, y - rad * 0.12,
                           fill="#4b5563", outline="")
        else:
            cv.create_oval(x - rad, y - rad, x + rad, y + rad, fill="#f1f3f7", outline="#b6bdc8")
            cv.create_oval(x - rad * 0.62, y - rad * 0.72, x - rad * 0.02, y - rad * 0.12,
                           fill="#ffffff", outline="")
        if last:
            cv.create_oval(x - 4, y - 4, x + 4, y + 4, fill=GOLD, outline="")

    # ---------- 交互 ----------
    def _set_hover(self, cell):
        if cell != self.hover:
            self.hover = cell
            self._draw()

    def _on_motion(self, e):
        self._set_hover(self._cell(e.x, e.y))

    def _on_click(self, e):
        if not self._can_play():
            return
        cell = self._cell(e.x, e.y)
        if not cell:
            return
        r, c = cell
        if self._stone_at(r, c) != EMPTY:
            return
        self._request("moved", lambda: self.client.move(self.game_id, r, c, True))

    def _toggle_sound(self):
        self.sound.muted = not self.sound.muted
        self.sound_btn.config(text="🔇 音效：关" if self.sound.muted else "🔊 音效：开")

    def _on_option(self):
        mode = self.mode_var.get()
        difficulty = self.diff_var.get()
        human_color = self.color_var.get()
        if (mode, difficulty, human_color) == (self.mode, self.difficulty, self.human_color):
            return
        if not self.game_id:
            self.mode, self.difficulty, self.human_color = mode, difficulty, human_color
            return
        self._request("configured",
                      lambda: self.client.configure(self.game_id, mode, difficulty, human_color))

    def _undo(self):
        if self.thinking or not self.game_id or not self.state or not self.state["moveCount"]:
            return
        self._request("undone", lambda: self.client.undo(self.game_id))

    def _new_game(self):
        if self.thinking:
            return
        if not self.game_id:
            self.root.after(30, self._boot)
            return
        self._request("reset", lambda: self.client.reset(self.game_id))

    # ---------- 请求调度（工作线程里跑 HTTP，主线程更新界面） ----------
    def _request(self, kind, fn):
        self.thinking = True
        self._sync_buttons()
        self._update_status()

        def work():
            try:
                self.results.put((kind, fn()))
            except ApiError as e:
                self.results.put(("error", e.args[0]))

        threading.Thread(target=work, daemon=True).start()

    def _apply(self, res, animate=True, silent=False):
        """把后台返回的状态装到界面，并按需要播放音效/动画。"""
        prev = self.state
        prev_count = prev["moveCount"] if prev else 0
        self.state = res["state"]
        self.game_id = self.state["id"]
        self.mode = self.state["mode"]
        self.difficulty = self.state["difficulty"]
        self.human_color = self.state["humanColor"]

        # 同步单选按钮（后台才是权威）
        self.mode_var.set(self.mode)
        self.diff_var.set(self.difficulty)
        self.color_var.set(self.human_color)

        status = self.state["status"]
        now = self.root.tk.call("clock", "milliseconds")
        new_moves = self.state["moves"][prev_count:]
        if animate and new_moves:
            for i, mv in enumerate(new_moves):
                self.anim[(mv[0], mv[1])] = now + i * 110
            if not silent:
                # 每颗子依次响一声
                for i in range(len(new_moves)):
                    self.root.after(i * 110, lambda: self.sound.play("stone"))
        elif not silent and new_moves:
            self.sound.play("stone")

        if not silent:
            if status == "won":
                human_won = (self.mode == "pvp" or self.state["winner"] == self.human_color)
                self.root.after(80 * max(1, len(new_moves)), lambda: self.sound.play("win" if human_won else "draw"))
            elif status == "draw":
                self.sound.play("draw")

        meta = res.get("aiMeta")
        if meta:
            self.ai_tip = f"AI：{meta['difficulty']} · 深度 {meta['depth']} · {meta['elapsedMs']}ms"
        self.thinking = False
        self.hover = None
        self._refresh()
        if self.state["status"] == "won":
            self.pulse_on = True
            self._pulse_loop()
        self._animate_loop()

    # ---------- 动画与刷新 ----------
    def _animate_loop(self):
        if not self.anim:
            return
        self._draw()
        self.root.after(16, self._animate_loop)

    def _pulse_loop(self):
        if self.state and self.state["status"] == "won":
            self.pulse_on = not self.pulse_on
            self._draw()
            self.root.after(160, self._pulse_loop)

    def _can_play(self):
        s = self.state
        if not s or self.thinking or s["status"] != "playing":
            return False
        if s["mode"] == "pvp":
            return True
        return s["turn"] == s["humanColor"]

    def _refresh(self):
        self._draw()
        self._update_status()
        self._sync_buttons()

    def _sync_buttons(self):
        state = "disabled" if (self.thinking or not self.state) else "normal"
        self.undo_btn.config(state=state)
        self.new_btn.config(state=state)

    def _update_status(self):
        s = self.state
        txt, color = "", TEXT
        if self.thinking:
            txt, color = "AI 思考中…", GOLD
        elif not s:
            txt, color = "连接后台中…", MUTED
        elif s["status"] == "won":
            if s["mode"] == "pvp":
                txt = ("黑棋" if s["winner"] == "black" else "白棋") + "获胜！"
            elif s["winner"] == s["humanColor"]:
                txt = "🎉 你赢了！"
            else:
                txt, color = "AI 获胜，再接再厉！", RED
        elif s["status"] == "draw":
            txt = "平局 · 棋盘已满"
        elif s["mode"] == "pvp":
            txt = ("黑棋" if s["turn"] == "black" else "白棋") + "的回合"
        else:
            who = "黑棋" if s["turn"] == "black" else "白棋"
            txt = ("你的回合 · " if s["turn"] == s["humanColor"] else "AI 的回合 · ") + who
        if s and s["moveCount"]:
            txt += f" · 第 {s['moveCount']} 手"
        if self.ai_tip and not self.thinking:
            txt += f"　（{self.ai_tip}）"
        self.status.config(text=txt, fg=color)


def main():
    root = tk.Tk()
    GomokuApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
