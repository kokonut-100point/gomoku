# -*- coding: utf-8 -*-
"""gomoku_core.py 核心逻辑自测（无界面、无网络）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gomoku_core as g

passed, failed = 0, 0


def ok(cond, name):
    global passed, failed
    if cond:
        passed += 1
        print("PASS", name)
    else:
        failed += 1
        print("FAIL", name)


# 1) 横向五连
b = g.empty_board()
for c in range(3, 8):
    b[7][c] = g.BLACK
ok(g.check_win_at(b, 7, 5, g.BLACK), "横向五连判定")
ok(not g.check_win_at(b, 7, 5, g.WHITE), "异色不算")

# 2) 斜向五连
b = g.empty_board()
for i in range(5):
    b[2 + i][2 + i] = g.WHITE
ok(g.check_win_at(b, 4, 4, g.WHITE), "斜向五连判定")

# 3) AI 直接取胜（四子缺一）
b = g.empty_board()
for c in range(5, 9):
    b[7][c] = g.BLACK
mv = g.find_best_move(b, g.BLACK, g.WHITE, "medium")
ok(mv == (7, 4) or mv == (7, 9), f"AI 补五连 (got {mv})")

# 4) AI 堵截对手四子
b = g.empty_board()
for c in range(5, 9):
    b[7][c] = g.WHITE
mv = g.find_best_move(b, g.BLACK, g.WHITE, "medium")
ok(mv == (7, 4) or mv == (7, 9), f"AI 堵截冲四 (got {mv})")

# 5) 合法落子
b = g.empty_board()
b[7][7] = g.BLACK
mv = g.find_best_move(b, g.WHITE, g.BLACK, "easy")
ok(mv is not None and b[mv[0]][mv[1]] == g.EMPTY, f"合法空位 (got {mv})")

# 6) 自对弈（中等 vs 中等）必须正常结束
import random
random.seed(42)
b = g.empty_board()
turn, moves, winner = g.BLACK, 0, None
while moves < g.N * g.N:
    ai, hu = turn, (g.WHITE if turn == g.BLACK else g.BLACK)
    mv = g.find_best_move(b, ai, hu, "medium")
    if mv is None or b[mv[0]][mv[1]] != g.EMPTY:
        failed += 1
        print("FAIL 自对弈非法落子", mv)
        break
    b[mv[0]][mv[1]] = ai
    moves += 1
    if g.check_win_at(b, mv[0], mv[1], ai):
        winner = ai
        break
    turn = hu
ok(winner is not None or moves >= g.N * g.N, f"自对弈结束 (胜者={winner}, {moves}手)")

# 7) 五连坐标（给界面画金线用）
b = g.empty_board()
for i in range(5):
    b[6][4 + i] = g.BLACK
cells = g.winning_cells_at(b, 6, 6, g.BLACK)
ok(cells is not None and len(cells) >= 5 and all(b[r][c] == g.BLACK for r, c in cells),
   f"五连坐标返回 (got {cells})")
ok(g.winning_cells_at(b, 6, 6, g.WHITE) is None, "无五连返回 None")

# 8) 棋盘序列化往返 + 校验
b = g.empty_board()
b[0][0], b[14][14], b[7][7] = g.BLACK, g.WHITE, g.BLACK
ok(g.board_from_list(g.board_to_list(b)) == b, "棋盘序列化往返一致")
for bad in ([], [[0] * 15] * 14, [[0] * 15] * 14 + [[0] * 14 + [3]]):
    try:
        g.board_from_list(bad)
        ok(False, f"非法棋盘应报错 (len={len(bad)})")
    except ValueError:
        ok(True, f"非法棋盘被拒 (len={len(bad)})")

# 9) 棋盘满 / 空位统计
full = [[g.BLACK] * g.N for _ in range(g.N)]
ok(g.is_full(full) and not g.is_full(g.empty_board()), "is_full 判定")
ok(g.stone_count(b) == 3, "stone_count 统计")

# 10) 空盘开局：候选点只有天元
ok(g.candidate_moves(g.empty_board()) == [(g.N // 2, g.N // 2)], "空盘候选点为天元")

# 11) 困难难度带时间预算（α-β 搜索 + 迭代加深）必须给出合法落子且不超预算太多
import time
b = g.empty_board()
b[7][7], b[7][8], b[8][7] = g.BLACK, g.WHITE, g.WHITE
t0 = time.monotonic()
rep = g.think(b, g.BLACK, g.WHITE, "hard", 0.4)
elapsed = time.monotonic() - t0
ok(rep.move is not None and b[rep.move[0]][rep.move[1]] == g.EMPTY,
   f"困难难度返回合法落子 (got {rep.move})")
ok(elapsed < 3.0, f"时间预算生效 (耗时 {elapsed:.2f}s, 预算 {rep.budget_s}s)")

# 12) 三种难度都能出招
for diff in g.DIFFICULTIES:
    b = g.empty_board()
    b[7][7] = g.BLACK
    mv = g.find_best_move(b, g.WHITE, g.BLACK, diff, 0.3)
    ok(mv is not None and b[mv[0]][mv[1]] == g.EMPTY, f"难度 {diff} 能出招 (got {mv})")

# 13) 非法难度要报错
try:
    g.find_best_move(g.empty_board(), g.BLACK, g.WHITE, "nightmare")
    ok(False, "非法难度应报错")
except ValueError:
    ok(True, "非法难度被拒")

# 14) 棋盘已满时无棋可下
b = [[g.BLACK] * g.N for _ in range(g.N)]
ok(g.find_best_move(b, g.WHITE, g.BLACK, "medium") is None, "棋盘满返回 None")

print(f"\n{passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
