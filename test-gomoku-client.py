# -*- coding: utf-8 -*-
"""端到端冒烟：用 gomoku.py 里的 ApiClient 打一局真·后台（不开窗口）"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gomoku import ApiClient, ensure_server, ApiError    # noqa: E402

passed, failed = 0, 0


def ok(cond, name):
    global passed, failed
    if cond:
        passed += 1
        print("PASS", name)
    else:
        failed += 1
        print("FAIL", name)


client = ApiClient()

ok_reachable, note = ensure_server(client)
ok(ok_reachable, f"后台可达（{note}）")
if not ok_reachable:
    print("\n后台不可用，后续用例跳过")
    sys.exit(1)

h = client.health()
ok(h.get("ok") and "alpha-beta" in h["engine"]["algorithm"], "health 返回引擎信息")

# 人机（人类执黑）
res = client.create_game("pve", "medium", "black")
gid, st = res["state"]["id"], res["state"]
ok(st["turn"] == "black" and st["moveCount"] == 0, "新建人机局（人类执黑）")

res = client.move(gid, 7, 7, True)
st = res["state"]
ok(st["moveCount"] == 2 and res["aiMove"], f"落子+AI应手成功 (AI 下在 {res['aiMove']})")
ok(res["aiMeta"]["elapsedMs"] >= 0, f"AI 耗时 {res['aiMeta']['elapsedMs']}ms（难度 {res['aiMeta']['difficulty']}）")

# 困难难度计时
res2 = client.create_game("pve", "hard", "black")
gid2 = res2["state"]["id"]
t0 = time.monotonic()
res2 = client.move(gid2, 7, 7, True)
wall = (time.monotonic() - t0) * 1000
ok(res2["state"]["moveCount"] == 2 and wall < 15000,
   f"困难难度一回合耗时 {wall:.0f}ms（含网络），AI 自报 {res2['aiMeta']['elapsedMs']}ms")

# 悔棋 / 重开
res = client.undo(gid)
ok(res["state"]["moveCount"] == 0, "悔棋清空（人机撤两手）")
res = client.move(gid, 5, 5, False)
res = client.reset(gid)
ok(res["state"]["moveCount"] == 0 and all(v == 0 for row in res["state"]["board"] for v in row),
   "重开棋盘清空")

# 切设置（会重开）
res = client.configure(gid, "pve", "easy", "white")
ok(res["state"]["difficulty"] == "easy" and res["state"]["humanColor"] == "white"
   and res["aiMove"] is not None, "切「简单+执白」后 AI 自动开局")

# 非法落子错误信息友好
try:
    client.move(gid, 7, 7, False)
    client.move(gid, 7, 7, False)
    ok(False, "重复落子应报错")
except ApiError as e:
    ok(e.code in ("occupied", "not-your-turn"), f"非法落子返回可读错误（{e.code}: {e}）")

# 连不上时的错误也友好
bad = ApiClient("http://127.0.0.1:59999")
try:
    bad.health()
    ok(False, "连不上应报错")
except ApiError as e:
    ok(e.code == "unreachable" and "连不上后台" in str(e), f"无法连接时提示友好（{e}）")

print(f"\n{passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
