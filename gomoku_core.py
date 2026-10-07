# -*- coding: utf-8 -*-
"""
五子棋核心逻辑（无界面、无 IO，供后台 / 客户端 / 测试共用）

- 棋盘表示：N×N 的 list[list[int]]，0 空 / 1 黑 / 2 白
- 胜负判定：check_win_at / winning_cells_at
- 局面评估：全盘连子模式评估（LINES + line_score）
- AI：候选点启发式排序 + α-β 剪枝 negamax（本文件的 search）
      困难难度用「迭代加深 + 时间预算」包一层，剪枝算法本身不变

这个模块不依赖 tkinter / winsound，可在服务器、测试、无头环境里直接跑。
"""
import random
import time
from collections import namedtuple

# =========================================================
# 常量与基础工具
# =========================================================
N = 15
EMPTY, BLACK, WHITE = 0, 1, 2
DIRS = [(1, 0), (0, 1), (1, 1), (1, -1)]
WIN_SCORE = 1_000_000_000

COLOR_NAMES = {BLACK: "black", WHITE: "white"}
COLOR_VALUES = {"black": BLACK, "white": WHITE}
DIFFICULTIES = ("easy", "medium", "hard")

# 每种难度的默认"思考时间上限"（秒）。搜索本身按深度，预算是保险丝。
DEFAULT_BUDGET = {"easy": 0.15, "medium": 1.0, "hard": 4.0}
# 困难难度的搜索深度阶梯（迭代加深）
HARD_DEPTHS = (2, 4)
BREADTH = 12


def empty_board():
    return [[EMPTY] * N for _ in range(N)]


def in_bounds(r, c):
    return 0 <= r < N and 0 <= c < N


def board_from_list(data):
    """把外部（JSON 等）传来的 15×15 数组转成内部棋盘，顺便校验。"""
    if not isinstance(data, list) or len(data) != N:
        raise ValueError(f"board must be a {N}×{N} list")
    board = []
    for row in data:
        if not isinstance(row, list) or len(row) != N:
            raise ValueError(f"board must be a {N}×{N} list")
        out = []
        for v in row:
            if v not in (EMPTY, BLACK, WHITE):
                raise ValueError("board cells must be 0 (empty), 1 (black) or 2 (white)")
            out.append(int(v))
        board.append(out)
    return board


def board_to_list(board):
    return [list(row) for row in board]


def is_full(board):
    return all(cell != EMPTY for row in board for cell in row)


def stone_count(board):
    return sum(1 for row in board for cell in row if cell != EMPTY)


def other(color):
    return WHITE if color == BLACK else BLACK


# =========================================================
# 局部点位评估（启发式排序用）
# =========================================================
def shape_score(count, open_):
    if count >= 5:
        return 100_000_000
    if count == 4:
        return 1_000_000 if open_ >= 2 else (100_000 if open_ == 1 else 0)
    if count == 3:
        return 50_000 if open_ >= 2 else (5_000 if open_ == 1 else 0)
    if count == 2:
        return 2_000 if open_ >= 2 else (200 if open_ == 1 else 0)
    if count == 1:
        return 50 if open_ >= 2 else (10 if open_ == 1 else 0)
    return 0


def point_score_at(board, r, c, player):
    total = 0
    for dr, dc in DIRS:
        count, open_ = 1, 0
        rr, cc = r + dr, c + dc
        while in_bounds(rr, cc) and board[rr][cc] == player:
            count += 1
            rr += dr
            cc += dc
        if in_bounds(rr, cc) and board[rr][cc] == EMPTY:
            open_ += 1
        rr, cc = r - dr, c - dc
        while in_bounds(rr, cc) and board[rr][cc] == player:
            count += 1
            rr -= dr
            cc -= dc
        if in_bounds(rr, cc) and board[rr][cc] == EMPTY:
            open_ += 1
        total += shape_score(count, open_)
    return total


def candidate_moves(board):
    """已有棋子周围 2 格内的空点。棋盘全空时给天元；棋盘已满时返回空列表。"""
    cells, seen, stones = [], set(), 0
    for r in range(N):
        for c in range(N):
            if board[r][c] != EMPTY:
                stones += 1
                for dr in (-2, -1, 0, 1, 2):
                    for dc in (-2, -1, 0, 1, 2):
                        rr, cc = r + dr, c + dc
                        if in_bounds(rr, cc) and board[rr][cc] == EMPTY:
                            key = rr * N + cc
                            if key not in seen:
                                seen.add(key)
                                cells.append((rr, cc))
    if not cells and stones == 0:
        cells.append((N // 2, N // 2))     # 空盘开局走天元
    return cells


# =========================================================
# 胜负判定
# =========================================================
def check_win_at(board, r, c, player):
    for dr, dc in DIRS:
        count = 1
        rr, cc = r + dr, c + dc
        while in_bounds(rr, cc) and board[rr][cc] == player:
            count += 1
            rr += dr
            cc += dc
        rr, cc = r - dr, c - dc
        while in_bounds(rr, cc) and board[rr][cc] == player:
            count += 1
            rr -= dr
            cc -= dc
        if count >= 5:
            return True
    return False


def winning_cells_at(board, r, c, player):
    """返回构成五连的坐标列表（给界面画金线用），没连成返回 None。"""
    for dr, dc in DIRS:
        cells = [(r, c)]
        rr, cc = r + dr, c + dc
        while in_bounds(rr, cc) and board[rr][cc] == player:
            cells.append((rr, cc))
            rr += dr
            cc += dc
        rr, cc = r - dr, c - dc
        while in_bounds(rr, cc) and board[rr][cc] == player:
            cells.append((rr, cc))
            rr -= dr
            cc -= dc
        if len(cells) >= 5:
            return cells
    return None


# =========================================================
# 全盘评估（所有横/竖/两个斜向的连续段）
# =========================================================
def _build_lines():
    lines = []
    for r in range(N):
        lines.append([(r, c) for c in range(N)])
    for c in range(N):
        lines.append([(r, c) for r in range(N)])
    for r in range(N):
        line = [(r + i, i) for i in range(N) if r + i < N]
        if len(line) >= 5:
            lines.append(line)
    for c in range(1, N):
        line = [(i, c + i) for i in range(N) if c + i < N]
        if len(line) >= 5:
            lines.append(line)
    for r in range(N):
        line = [(r + i, N - 1 - i) for i in range(N) if r + i < N]
        if len(line) >= 5:
            lines.append(line)
    for c in range(N - 2, -1, -1):
        line = [(i, c - i) for i in range(N) if c - i >= 0]
        if len(line) >= 5:
            lines.append(line)
    return lines


LINES = _build_lines()


def run_score(length, open_):
    if length >= 5:
        return 10_000_000
    if length == 4:
        return 100_000 if open_ == 2 else (10_000 if open_ == 1 else 0)
    if length == 3:
        return 1_000 if open_ == 2 else (200 if open_ == 1 else 0)
    if length == 2:
        return 100 if open_ == 2 else (20 if open_ == 1 else 0)
    if length == 1:
        return 10 if open_ == 2 else 0
    return 0


def line_score(board, cells, player):
    score, i, L = 0, 0, len(cells)
    while i < L:
        r, c = cells[i]
        if board[r][c] != player:
            i += 1
            continue
        j = i
        while j < L and board[cells[j][0]][cells[j][1]] == player:
            j += 1
        length = j - i
        open_ = 0
        if i > 0 and board[cells[i - 1][0]][cells[i - 1][1]] == EMPTY:
            open_ += 1
        if j < L and board[cells[j][0]][cells[j][1]] == EMPTY:
            open_ += 1
        score += run_score(length, open_)
        i = j
    return score


def evaluate_board(board, ai, human):
    score = 0
    for cells in LINES:
        score += line_score(board, cells, ai)
        score -= line_score(board, cells, human)
    return score


def ordered_moves(board, ai, human, breadth):
    """候选点按「对双方的价值之和」排序后截断 —— 让 α-β 更快剪掉坏分支。"""
    moves = []
    for r, c in candidate_moves(board):
        s = point_score_at(board, r, c, ai) + point_score_at(board, r, c, human)
        moves.append((s, r, c))
    moves.sort(reverse=True)
    return moves[:breadth]


# =========================================================
# α-β 剪枝 negamax 搜索（核心算法保持原样）
# =========================================================
def search(board, depth, alpha, beta, ai, human, breadth, deadline=None, stats=None):
    """
    negamax：ai 为当前行棋方并取最大。返回 (最佳估值, 最佳落子)。

    deadline / stats 是可选的"保险丝"，用于给搜索加时间上限：
    - deadline：time.monotonic() 时间点，超时后不再展开同级后续着法（至少评估一个）
    - stats：计数与截断标记（nodes / truncated），不参与搜索决策
    这两者不改变剪枝逻辑与估值函数，只是允许提前收工。
    """
    if stats is not None:
        stats["nodes"] = stats.get("nodes", 0) + 1
    moves = ordered_moves(board, ai, human, breadth)
    best, best_move = -float("inf"), moves[0][1:]
    for idx, (s, r, c) in enumerate(moves):
        if deadline is not None and idx > 0 and time.monotonic() > deadline:
            if stats is not None:
                stats["truncated"] = True
            break
        board[r][c] = ai
        if check_win_at(board, r, c, ai):
            val = WIN_SCORE + depth
        elif depth <= 1:
            val = evaluate_board(board, ai, human)
        else:
            val = -search(board, depth - 1, -beta, -alpha, human, ai, breadth, deadline, stats)[0]
        board[r][c] = EMPTY
        if val > best:
            best, best_move = val, (r, c)
        if best > alpha:
            alpha = best
        if alpha >= beta:
            break
    return best, best_move


def find_best_move(board, ai, human, difficulty, time_budget_s=None):
    """
    选一个落子。难度：
    - easy：会漏挡冲四，且从前几名里随机挑（保留原行为）
    - medium：固定深度 2 的 α-β 搜索
    - hard：迭代加深 2 → 4，超出时间预算就用上一层的结果（剪枝不变）
    返回 (r, c)；棋盘已满等无解情况返回 None。
    """
    if difficulty not in DIFFICULTIES:
        raise ValueError(f"unknown difficulty: {difficulty!r}")

    cells = candidate_moves(board)
    if not cells:
        return None

    # 一步取胜 / 一步被成五 —— 直接出手，不必搜索
    for r, c in cells:
        if point_score_at(board, r, c, ai) >= 100_000_000:
            return (r, c)
    for r, c in cells:
        if point_score_at(board, r, c, human) >= 100_000_000:
            return (r, c)

    if difficulty == "easy":
        for r, c in cells:
            if point_score_at(board, r, c, human) >= 1_000_000 and random.random() < 0.8:
                return (r, c)
        scored = sorted(
            ((point_score_at(board, r, c, ai) + point_score_at(board, r, c, human) * 1.1, r, c)
             for r, c in cells), reverse=True)
        top = scored[:min(4, len(scored))]
        _, r, c = random.choice(top)
        return (r, c)

    if difficulty == "medium":
        _, move = search(board, 2, -float("inf"), float("inf"), ai, human, BREADTH)
        return move

    # hard：迭代加深 + 时间预算
    budget = DEFAULT_BUDGET["hard"] if time_budget_s is None else float(time_budget_s)
    deadline = time.monotonic() + budget if budget > 0 else None
    move = None
    for depth in HARD_DEPTHS:
        stats = {"nodes": 0, "truncated": False}
        _, candidate = search(board, depth, -float("inf"), float("inf"), ai, human,
                              BREADTH, deadline, stats)
        if stats["truncated"]:
            # 这一层没搜完，不如用上一层的完整结果（没有就退化成这一层的现有最好）
            if move is None:
                move = candidate
            break
        move = candidate
    return move


SearchReport = namedtuple("SearchReport", "move depth nodes elapsed_ms truncated budget_s")


def think(board, ai, human, difficulty, time_budget_s=None):
    """给后台用：返回落子 + 搜索元信息（耗时/节点数/是否被预算截断）。"""
    budget = DEFAULT_BUDGET.get(difficulty, 1.0) if time_budget_s is None else float(time_budget_s)
    started = time.monotonic()
    move = find_best_move(board, ai, human, difficulty, budget)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    depth = 0
    if difficulty == "medium":
        depth = 2
    elif difficulty == "hard":
        depth = HARD_DEPTHS[-1]
    return SearchReport(move=move, depth=depth, nodes=0, elapsed_ms=elapsed_ms,
                        truncated=elapsed_ms >= budget * 1000 - 1, budget_s=budget)
