# -*- coding: utf-8 -*-
"""
五子棋后台服务（Flask）

职责：
- 用内存维护「多局并存」的完整对局：新建、落子、悔棋、重开、难度/模式切换、胜负判定
- AI 走子由 gomoku_core 提供（α-β 剪枝搜索，困难难度带时间预算）
- 顺手托管网页版棋盘（web/ 目录），浏览器打开 http://127.0.0.1:8099 就能玩

启动：
    python gomoku_server.py                     # 默认 127.0.0.1:8099
    python gomoku_server.py --port 9000 --host 0.0.0.0
    python gomoku_server.py --debug             # Flask 调试模式（自动重载）

接口一览（全部返回 JSON）：
    GET    /api/health
    GET    /api/games
    POST   /api/games                       {mode, difficulty, humanColor}
    GET    /api/games/<gid>
    DELETE /api/games/<gid>
    POST   /api/games/<gid>/moves           {r, c, ai?}     → 落子（可带 AI 应手）
    POST   /api/games/<gid>/ai-move         {budget?}       → 让 AI 走一步
    POST   /api/games/<gid>/undo            {plies?}        → 悔棋
    POST   /api/games/<gid>/reset           {mode?, difficulty?, humanColor?}
    PATCH  /api/games/<gid>                 {difficulty?, mode?, humanColor?}
"""
import argparse
import os
import sys
import threading
import time
import uuid
from collections import OrderedDict

from flask import Flask, jsonify, request, send_from_directory

import gomoku_core as core

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HERE, "web")
VERSION = "1.0.0"

app = Flask(__name__, static_folder=None)
app.config["JSON_AS_ASCII"] = False

# 对局表：id -> Game（OrderedDict 便于淘汰最旧的）
GAMES = OrderedDict()
GAMES_LOCK = threading.RLock()
MAX_GAMES = 64
# 同时进行的 AI 搜索数量上限，避免多客户端一起点「困难」把 CPU 打满
AI_SLOTS = threading.Semaphore(max(1, (os.cpu_count() or 2) // 2))
DEFAULT_BUDGET_SCALE = 1.0
BUDGET_SCALE = DEFAULT_BUDGET_SCALE


# =========================================================
# 错误类型
# =========================================================
class ApiError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def fail(code, message, status=400):
    raise ApiError(code, message, status)


@app.errorhandler(ApiError)
def _handle_api_error(err):
    return jsonify({"error": {"code": err.code, "message": err.message}}), err.status


@app.errorhandler(404)
def _handle_404(_err):
    if request.path.startswith("/api/"):
        return jsonify({"error": {"code": "not-found", "message": f"no such endpoint: {request.path}"}}), 404
    return jsonify({"error": {"code": "not-found", "message": "not found"}}), 404


@app.errorhandler(500)
def _handle_500(err):
    return jsonify({"error": {"code": "internal", "message": str(err)}}), 500


# =========================================================
# 对局
# =========================================================
class Game:
    """一局棋的全部状态。所有改动都在 self.lock 下进行。"""

    def __init__(self, mode="pve", difficulty="medium", human_color=core.BLACK):
        self.id = "g-" + uuid.uuid4().hex[:12]
        self.lock = threading.RLock()
        self.created_at = time.time()
        self.thinking = False
        self.mode = mode
        self.difficulty = difficulty
        self.human_color = human_color
        self._reset_locked()

    # ---------- 内部 ----------
    def _reset_locked(self):
        self.board = core.empty_board()
        self.turn = core.BLACK
        self.history = []          # [(r, c, color), ...]
        self.last_move = None
        self.winner = None         # None=未结束, 0=平局, 1/2=胜者
        self.win_cells = None
        self.over = False
        self.revision = 0

    @property
    def ai_color(self):
        return core.other(self.human_color)

    def _place_locked(self, r, c, color):
        """落子并判定胜负，返回 True 表示这一手结束了棋局。"""
        self.board[r][c] = color
        self.last_move = (r, c)
        self.history.append((r, c, color))
        self.revision += 1
        win = core.winning_cells_at(self.board, r, c, color)
        if win:
            self.over, self.winner, self.win_cells = True, color, win
            return True
        if core.is_full(self.board):
            self.over, self.winner, self.win_cells = True, 0, None
            return True
        self.turn = core.other(color)
        return False

    def _ai_should_move_locked(self):
        return (self.mode == "pve" and not self.over and self.turn == self.ai_color)

    # ---------- 对外操作 ----------
    def move(self, r, c, with_ai=True, budget=None):
        """人类落子；(with_ai 且轮到 AI 时) 紧接着让 AI 应一手。"""
        with self.lock:
            if self.over:
                fail("game-over", "this game is already finished", 409)
            if not (isinstance(r, int) and isinstance(c, int)
                    and core.in_bounds(r, c)):
                fail("bad-coordinate", "r and c must be integers within 0..14")
            if self.board[r][c] != core.EMPTY:
                fail("occupied", f"cell ({r}, {c}) is already taken", 409)
            if self.mode == "pve" and self.turn != self.human_color:
                fail("not-your-turn", "it is the AI's turn", 409)

            self._place_locked(r, c, self.turn)
            ai_move = None
            ai_meta = None
            if with_ai and self._ai_should_move_locked():
                ai_move, ai_meta = self._ai_locked(budget)
            return self.state_locked(), ai_move, ai_meta

    def ai_move(self, budget=None):
        with self.lock:
            if self.over:
                fail("game-over", "this game is already finished", 409)
            if self.mode == "pve" and self.turn != self.ai_color:
                fail("not-ai-turn", "it is not the AI's turn", 409)
            move, meta = self._ai_locked(budget)
            return self.state_locked(), move, meta

    def _ai_locked(self, budget=None):
        """在持锁状态下跑一次 AI 搜索（同时受全局 AI 并发闸门限制）。"""
        self.thinking = True
        try:
            with AI_SLOTS:
                eff_budget = None
                if budget is None:
                    eff_budget = core.DEFAULT_BUDGET.get(self.difficulty, 1.0) * BUDGET_SCALE
                else:
                    eff_budget = max(0.0, float(budget))
                report = core.think(self.board, self.turn, core.other(self.turn),
                                    self.difficulty, eff_budget)
            move = report.move
            meta = {
                "difficulty": self.difficulty,
                "depth": report.depth,
                "elapsedMs": report.elapsed_ms,
                "budgetMs": int(report.budget_s * 1000),
                "budgetExceeded": report.elapsed_ms > int(report.budget_s * 1000),
                "color": core.COLOR_NAMES[self.turn],
            }
            if move is None or self.board[move[0]][move[1]] != core.EMPTY:
                # 没有可下之处（棋盘已满等）；正常情况下只在此时发生
                if not self.over:
                    self.over, self.winner, self.win_cells = True, 0, None
                return None, meta
            self._place_locked(move[0], move[1], self.turn)
            return list(move), meta
        finally:
            self.thinking = False

    def undo(self, plies=None):
        """悔棋。默认：人机模式退回自己上一手之前（AI 那一手也撤），双人模式退一手。"""
        with self.lock:
            if self.thinking:
                fail("busy", "the AI is still thinking", 409)
            if not self.history:
                fail("nothing-to-undo", "there is no move to undo", 409)

            removed = []
            if plies is None:
                if self.mode == "pvp":
                    target = 1
                else:
                    # 撤到「轮到人类」为止，至少撤一手
                    target = 1
                    if self.history and self.history[-1][2] == self.ai_color:
                        target = 2
                plies = target
            plies = max(1, min(int(plies), len(self.history)))
            for _ in range(plies):
                r, c, _color = self.history.pop()
                self.board[r][c] = core.EMPTY
                removed.append([r, c])
            self.over, self.winner, self.win_cells = False, None, None
            self.last_move = (self.history[-1][0], self.history[-1][1]) if self.history else None
            if self.mode == "pvp":
                self.turn = core.BLACK if len(self.history) % 2 == 0 else core.WHITE
            else:
                self.turn = self.human_color if len(self.history) % 2 == 0 else self.ai_color
            self.revision += 1
            return self.state_locked(), removed

    def reset(self, mode=None, difficulty=None, human_color=None):
        with self.lock:
            if self.thinking:
                fail("busy", "the AI is still thinking", 409)
            if mode is not None:
                self.mode = mode
            if difficulty is not None:
                self.difficulty = difficulty
            if human_color is not None:
                self.human_color = human_color
            self._reset_locked()
            # 人机模式且 AI 执黑 → 开局由 AI 先走
            ai_move = ai_meta = None
            if self._ai_should_move_locked():
                ai_move, ai_meta = self._ai_locked(None)
            return self.state_locked(), ai_move, ai_meta

    def configure(self, mode=None, difficulty=None, human_color=None):
        """改设置。会重开一局（跟界面上的单选按钮行为一致）。"""
        changed = False
        with self.lock:
            if mode is not None and mode != self.mode:
                changed = True
            if difficulty is not None and difficulty != self.difficulty:
                changed = True
            if human_color is not None and human_color != self.human_color:
                changed = True
            if not changed:
                return self.state_locked(), None, None
        return self.reset(mode=mode, difficulty=difficulty, human_color=human_color)

    # ---------- 序列化 ----------
    def status_locked(self):
        if self.over:
            if self.winner == 0:
                return "draw"
            return "won"
        return "playing"

    def state_locked(self):
        return {
            "id": self.id,
            "mode": self.mode,
            "difficulty": self.difficulty,
            "humanColor": core.COLOR_NAMES[self.human_color],
            "aiColor": core.COLOR_NAMES[self.ai_color] if self.mode == "pve" else None,
            "board": core.board_to_list(self.board),
            "size": core.N,
            "turn": core.COLOR_NAMES[self.turn] if not self.over else None,
            "status": self.status_locked(),
            "winner": None if self.winner is None else (
                "draw" if self.winner == 0 else core.COLOR_NAMES[self.winner]),
            "winCells": [[r, c] for r, c in self.win_cells] if self.win_cells else None,
            "lastMove": list(self.last_move) if self.last_move else None,
            "moveCount": len(self.history),
            "thinking": self.thinking,
            "revision": self.revision,
            "moves": [[r, c, core.COLOR_NAMES[color]] for r, c, color in self.history],
        }

    def state(self):
        with self.lock:
            return self.state_locked()

    def summary_locked(self):
        return {
            "id": self.id,
            "mode": self.mode,
            "difficulty": self.difficulty,
            "status": self.status_locked(),
            "moveCount": len(self.history),
            "createdAt": self.created_at,
        }


# =========================================================
# 参数解析与校验
# =========================================================
def want_json():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def parse_mode(value, default="pve"):
    if value is None:
        return default
    if value not in ("pve", "pvp"):
        fail("bad-mode", "mode must be 'pve' or 'pvp'")
    return value


def parse_difficulty(value, default="medium"):
    if value is None:
        return default
    if value not in core.DIFFICULTIES:
        fail("bad-difficulty", "difficulty must be one of: " + ", ".join(core.DIFFICULTIES))
    return value


def parse_color(value, default="black"):
    if value is None:
        return default
    if value not in core.COLOR_VALUES:
        fail("bad-color", "humanColor must be 'black' or 'white'")
    return value


def get_game(gid):
    with GAMES_LOCK:
        game = GAMES.get(gid)
    if game is None:
        fail("game-not-found", f"no game with id {gid!r}", 404)
    return game


def store_game(game):
    with GAMES_LOCK:
        GAMES[game.id] = game
        while len(GAMES) > MAX_GAMES:
            old_id, _ = GAMES.popitem(last=False)
            app.logger.info("evicted oldest game %s (limit %d)", old_id, MAX_GAMES)


# =========================================================
# API
# =========================================================
@app.get("/api/health")
def api_health():
    with GAMES_LOCK:
        count = len(GAMES)
    return jsonify({
        "ok": True,
        "service": "gomoku-server",
        "version": VERSION,
        "engine": {"algorithm": "negamax + alpha-beta pruning", "module": "gomoku_core"},
        "board": core.N,
        "games": count,
        "maxGames": MAX_GAMES,
        "budgetScale": BUDGET_SCALE,
        "time": time.time(),
    })


@app.get("/api/games")
def api_list_games():
    with GAMES_LOCK:
        items = [g.summary_locked() for g in GAMES.values()]
    return jsonify({"games": items, "count": len(items)})


@app.post("/api/games")
def api_create_game():
    body = want_json()
    mode = parse_mode(body.get("mode"))
    difficulty = parse_difficulty(body.get("difficulty"))
    human_color = core.COLOR_VALUES[parse_color(body.get("humanColor"))]
    game = Game(mode=mode, difficulty=difficulty, human_color=human_color)
    store_game(game)

    ai_move = None
    ai_meta = None
    if body.get("start", True):
        with game.lock:
            if game._ai_should_move_locked():
                ai_move, ai_meta = game._ai_locked(body.get("budget"))
    return jsonify({"gameId": game.id, "state": game.state(),
                    "aiMove": ai_move, "aiMeta": ai_meta}), 201


@app.get("/api/games/<gid>")
def api_get_game(gid):
    game = get_game(gid)
    return jsonify({"state": game.state()})


@app.delete("/api/games/<gid>")
def api_delete_game(gid):
    with GAMES_LOCK:
        existed = GAMES.pop(gid, None)
    if existed is None:
        fail("game-not-found", f"no game with id {gid!r}", 404)
    return jsonify({"ok": True, "deleted": gid})


@app.post("/api/games/<gid>/moves")
def api_move(gid):
    game = get_game(gid)
    body = want_json()
    if "r" not in body or "c" not in body:
        fail("bad-request", "body must include integer 'r' and 'c'")
    try:
        r, c = int(body["r"]), int(body["c"])
    except (TypeError, ValueError):
        fail("bad-coordinate", "'r' and 'c' must be integers")
    state, ai_move, ai_meta = game.move(r, c, with_ai=bool(body.get("ai", True)),
                                        budget=body.get("budget"))
    return jsonify({"state": state, "aiMove": ai_move, "aiMeta": ai_meta})


@app.post("/api/games/<gid>/ai-move")
def api_ai_move(gid):
    game = get_game(gid)
    body = want_json()
    state, ai_move, ai_meta = game.ai_move(budget=body.get("budget"))
    return jsonify({"state": state, "aiMove": ai_move, "aiMeta": ai_meta})


@app.post("/api/games/<gid>/undo")
def api_undo(gid):
    game = get_game(gid)
    body = want_json()
    plies = body.get("plies")
    state, removed = game.undo(plies=plies)
    return jsonify({"state": state, "removed": removed})


@app.post("/api/games/<gid>/reset")
def api_reset(gid):
    game = get_game(gid)
    body = want_json()
    state, ai_move, ai_meta = game.reset(
        mode=parse_mode(body.get("mode"), None) if "mode" in body else None,
        difficulty=parse_difficulty(body.get("difficulty"), None) if "difficulty" in body else None,
        human_color=core.COLOR_VALUES[parse_color(body.get("humanColor"), None)] if "humanColor" in body else None,
    )
    return jsonify({"state": state, "aiMove": ai_move, "aiMeta": ai_meta})


@app.patch("/api/games/<gid>")
def api_configure(gid):
    game = get_game(gid)
    body = want_json()
    state, ai_move, ai_meta = game.configure(
        mode=parse_mode(body.get("mode"), None) if "mode" in body else None,
        difficulty=parse_difficulty(body.get("difficulty"), None) if "difficulty" in body else None,
        human_color=core.COLOR_VALUES[parse_color(body.get("humanColor"), None)] if "humanColor" in body else None,
    )
    return jsonify({"state": state, "aiMove": ai_move, "aiMeta": ai_meta})


# =========================================================
# 网页版棋盘（静态文件）
# =========================================================
@app.get("/")
def web_index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/<path:filename>")
def web_files(filename):
    if filename.startswith("api/"):
        fail("not-found", f"no such endpoint: /{filename}", 404)
    return send_from_directory(WEB_DIR, filename)


def main(argv=None):
    global BUDGET_SCALE
    parser = argparse.ArgumentParser(description="五子棋后台服务（Flask）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8099, help="监听端口（默认 8099）")
    parser.add_argument("--debug", action="store_true", help="Flask 调试模式（自动重载）")
    parser.add_argument("--budget-scale", type=float, default=DEFAULT_BUDGET_SCALE,
                        help="AI 思考时间预算倍数（默认 1.0，调小更快、调大更强）")
    parser.add_argument("--quiet", action="store_true", help="少打日志")
    args = parser.parse_args(argv)

    BUDGET_SCALE = max(0.05, float(args.budget_scale))
    if not args.quiet:
        app.logger.setLevel("INFO")
        print(f"五子棋后台已就绪：http://{args.host}:{args.port}  （网页版棋盘 / API 都在这个地址）")
        print(f"  · 引擎：negamax + α-β 剪枝；思考预算倍数 ×{BUDGET_SCALE}")
        print("  · 停止服务：Ctrl+C")
    # threaded=True 让多个客户端（网页版 + Tkinter 版）能同时用
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True,
            use_reloader=args.debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
