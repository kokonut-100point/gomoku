# -*- coding: utf-8 -*-
"""gomoku_server.py 后台接口自测（Flask test_client，不占端口、不需要真联网）"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gomoku_server as srv

passed, failed = 0, 0
client = srv.app.test_client()


def ok(cond, name):
    global passed, failed
    if cond:
        passed += 1
        print("PASS", name)
    else:
        failed += 1
        print("FAIL", name)


def post(path, body=None):
    r = client.post(path, data=json.dumps(body or {}), content_type="application/json")
    return r.status_code, (r.get_json() or {})


def get(path):
    r = client.get(path)
    return r.status_code, (r.get_json() or {})


def patch(path, body=None):
    r = client.patch(path, data=json.dumps(body or {}), content_type="application/json")
    return r.status_code, (r.get_json() or {})


def delete(path):
    r = client.delete(path)
    return r.status_code, (r.get_json() or {})


def new_game(**kw):
    body = {"mode": "pve", "difficulty": "medium", "humanColor": "black"}
    body.update(kw)
    code, data = post("/api/games", body)
    assert code == 201, (code, data)
    return data


# 1) 健康检查
code, data = get("/api/health")
ok(code == 200 and data.get("ok") and "alpha-beta" in data.get("engine", {}).get("algorithm", ""),
   f"健康检查返回引擎信息 ({data.get('engine', {}).get('algorithm')})")

# 2) 新建人机对局（人类执黑）
d = new_game()
gid, st = d["gameId"], d["state"]
ok(st["status"] == "playing" and st["turn"] == "black" and st["moveCount"] == 0
   and st["humanColor"] == "black" and st["aiColor"] == "white",
   "新建人机对局：轮黑、空盘")
ok(len(st["board"]) == 15 and all(len(row) == 15 for row in st["board"]), "棋盘尺寸 15×15")

# 3) 人类执白 → 后台自动让 AI 先走
d = new_game(humanColor="white")
st = d["state"]
ok(d["aiMove"] is not None and st["moveCount"] == 1 and st["turn"] == "white"
   and st["lastMove"] == d["aiMove"],
   f"人类执白时 AI 自动开局 (aiMove={d['aiMove']})")
ok(d["aiMeta"] is not None and "elapsedMs" in d["aiMeta"] and "depth" in d["aiMeta"],
   f"AI 元信息回传 (depth={d['aiMeta']['depth']}, {d['aiMeta']['elapsedMs']}ms)")

# 4) 落子 + AI 应手（一次请求完成一回合）
code, data = post(f"/api/games/{gid}/moves", {"r": 7, "c": 7, "ai": True})
st = data["state"]
ok(code == 200 and st["moveCount"] == 2 and data["aiMove"] is not None
   and st["lastMove"] == data["aiMove"] and st["turn"] == "black",
   "人类落子后 AI 立即应手（共 2 手）")

# 5) 禁止落在已占位置
code, data = post(f"/api/games/{gid}/moves", {"r": 7, "c": 7})
ok(code == 409 and data["error"]["code"] == "occupied", "重复落子被拒 (409 occupied)")

# 6) 不是人类回合时不能下（human 落子用 ai:false 后，仍轮 AI）
d2 = new_game()
gid2 = d2["gameId"]
post(f"/api/games/{gid2}/moves", {"r": 3, "c": 3, "ai": False})
code, data = post(f"/api/games/{gid2}/moves", {"r": 4, "c": 4, "ai": False})
ok(code == 409 and data["error"]["code"] == "not-your-turn", "AI 回合时人类不能落子 (409 not-your-turn)")

# 7) 让 AI 单独走一步
code, data = post(f"/api/games/{gid2}/ai-move", {})
ok(code == 200 and data["state"]["moveCount"] == 2 and data["aiMove"] is not None,
   "ai-move 接口可单独驱动 AI")

# 8) 参数校验
code, data = post("/api/games", {"difficulty": "nightmare"})
ok(code == 400 and data["error"]["code"] == "bad-difficulty", "非法难度被拒 (400)")
code, data = post("/api/games", {"mode": "solo"})
ok(code == 400 and data["error"]["code"] == "bad-mode", "非法模式被拒 (400)")
code, data = post(f"/api/games/{gid2}/moves", {"r": "x", "c": 1})
ok(code == 400 and data["error"]["code"] == "bad-coordinate", "非法坐标被拒 (400)")
code, data = post(f"/api/games/{gid2}/moves", {"r": 99, "c": 99})
ok(code == 400 and data["error"]["code"] == "bad-coordinate", "越界坐标被拒 (400)")

# 9) 双人对局走出五连 → 判定胜负 + 金线坐标
d3 = new_game(mode="pvp")
gid3 = d3["gameId"]
script = [(7, 3), (0, 0), (7, 4), (0, 1), (7, 5), (0, 2), (7, 6), (0, 3), (7, 7)]
for r, c in script:
    code, data = post(f"/api/games/{gid3}/moves", {"r": r, "c": c})
    if code != 200:
        break
st = data["state"]
ok(code == 200 and st["status"] == "won" and st["winner"] == "black" and st["turn"] is None,
   f"双人五连判胜 (status={st['status']}, winner={st['winner']})")
ok(st["winCells"] and len(st["winCells"]) >= 5
   and all(st["board"][r][c] == 1 for r, c in st["winCells"]),
   f"胜方五连坐标回传 ({st['winCells']})")

# 10) 已结束的局不能再下
code, data = post(f"/api/games/{gid3}/moves", {"r": 10, "c": 10})
ok(code == 409 and data["error"]["code"] == "game-over", "终局后落子被拒 (409 game-over)")

# 11) 悔棋：人机模式连 AI 那手一起撤
d4 = new_game()
gid4 = d4["gameId"]
post(f"/api/games/{gid4}/moves", {"r": 7, "c": 7, "ai": True})
code, data = post(f"/api/games/{gid4}/undo", {})
st = data["state"]
ok(code == 200 and st["moveCount"] == 0 and st["status"] == "playing"
   and st["turn"] == "black" and st["lastMove"] is None,
   "人机悔棋一次撤两手")
code, data = post(f"/api/games/{gid4}/undo", {})
ok(code == 409 and data["error"]["code"] == "nothing-to-undo", "空盘悔棋被拒 (409)")

# 12) 双人悔棋只撤一手
d5 = new_game(mode="pvp")
gid5 = d5["gameId"]
post(f"/api/games/{gid5}/moves", {"r": 1, "c": 1})
post(f"/api/games/{gid5}/moves", {"r": 2, "c": 2})
code, data = post(f"/api/games/{gid5}/undo", {})
ok(code == 200 and data["state"]["moveCount"] == 1 and data["state"]["turn"] == "white",
   "双人悔棋只撤一手，轮次正确")

# 13) 悔棋可指定手数
code, data = post(f"/api/games/{gid5}/undo", {"plies": 1})
ok(code == 200 and data["state"]["moveCount"] == 0, "悔棋可指定手数")

# 14) 重开
post(f"/api/games/{gid5}/moves", {"r": 5, "c": 5})
code, data = post(f"/api/games/{gid5}/reset", {})
ok(code == 200 and data["state"]["moveCount"] == 0
   and all(v == 0 for row in data["state"]["board"] for v in row),
   "重开清空棋盘")

# 15) 改难度（会重开一局）
code, data = patch(f"/api/games/{gid5}", {"difficulty": "hard"})
ok(code == 200 and data["state"]["difficulty"] == "hard" and data["state"]["moveCount"] == 0,
   "切换难度生效并重开")
code, data = patch(f"/api/games/{gid5}", {"difficulty": "hard"})
ok(code == 200 and data["state"]["difficulty"] == "hard", "重复设置同难度不重开也不报错")

# 16) 人机模式切「人类执白」→ 后台开新局并让 AI 先走
d6 = new_game(mode="pvp")
gid6 = d6["gameId"]
code, data = patch(f"/api/games/{gid6}", {"mode": "pve", "humanColor": "white"})
ok(code == 200 and data["state"]["mode"] == "pve" and data["state"]["humanColor"] == "white"
   and data["aiMove"] is not None and data["state"]["moveCount"] == 1,
   "切到人机+执白：AI 先走")

# 17) 对局列表与查询
code, data = get("/api/games")
ids = [g["id"] for g in data.get("games", [])]
ok(code == 200 and gid in ids and data["count"] >= 1, f"对局列表可查 ({data.get('count')} 局)")
code, data = get(f"/api/games/{gid}")
ok(code == 200 and data["state"]["id"] == gid, "按 id 查对局")

# 18) 不存在的对局
code, data = get("/api/games/g-nope")
ok(code == 404 and data["error"]["code"] == "game-not-found", "未知对局 404")
code, data = post("/api/games/g-nope/moves", {"r": 0, "c": 0})
ok(code == 404, "未知对局落子 404")

# 19) 删除对局
code, data = delete(f"/api/games/{gid}")
ok(code == 200 and data.get("ok"), "删除对局成功")
code, data = get(f"/api/games/{gid}")
ok(code == 404, "删除后查不到 (404)")

# 20) 网页版棋盘托管
r = client.get("/")
html = r.get_data(as_text=True)
ok(r.status_code == 200 and "五子棋" in html and "app.js" in html, "首页托管网页棋盘")
r = client.get("/app.js")
ok(r.status_code == 200 and "api/games" in r.get_data(as_text=True), "静态资源 app.js 可取")
r = client.get("/style.css")
ok(r.status_code == 200, "静态资源 style.css 可取")

# 21) 未知 API 端点
code, data = get("/api/nope")
ok(code == 404 and data["error"]["code"] == "not-found", "未知 API 端点 404")

print(f"\n{passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
