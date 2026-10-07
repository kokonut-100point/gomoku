/* 五子棋网页版前端：所有棋局状态与 AI 都由后台（gomoku_server.py）负责 */
(() => {
  "use strict";

  const API = window.location.origin;
  const N = 15;
  const CELL = 40, PAD = 34, SIZE = PAD * 2 + CELL * (N - 1);
  const EMPTY = 0, BLACK = 1, WHITE = 2;

  const cv = document.getElementById("board");
  const ctx = cv.getContext("2d");
  const statusEl = document.getElementById("status");
  const engineEl = document.getElementById("engine");
  const apiBaseEl = document.getElementById("apiBase");
  const undoBtn = document.getElementById("undo");
  const restartBtn = document.getElementById("restart");
  const soundBtn = document.getElementById("sound");

  apiBaseEl.textContent = API;

  let game = null;          // 后台返回的棋局状态
  let gameId = null;
  let busy = false;         // 请求进行中（含 AI 思考）
  let hover = null;         // {r, c}
  let soundOn = true;
  let pulse = 0;            // 胜利金线脉冲相位
  let anim = {};            // "r,c" -> 起始时间戳（落子动画）
  let aiTip = "";

  // ---------------- 音效（WebAudio 现场合成，零音频文件） ----------------
  const Sound = (() => {
    let ac = null;
    const ensure = () => (ac ??= new (window.AudioContext || window.webkitAudioContext)());
    const tone = (freq, dur, vol = 0.25, delay = 0) => {
      if (!soundOn) return;
      try {
        const c = ensure();
        const t0 = c.currentTime + delay;
        const osc = c.createOscillator();
        const gain = c.createGain();
        osc.type = "sine";
        osc.frequency.setValueAtTime(freq, t0);
        gain.gain.setValueAtTime(vol, t0);
        gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
        osc.connect(gain).connect(c.destination);
        osc.start(t0);
        osc.stop(t0 + dur + 0.02);
      } catch { /* 浏览器未授权音频时静默 */ }
    };
    return {
      stone: () => { tone(170, 0.09, 0.28); tone(90, 0.07, 0.18); },
      win: () => [523.25, 659.25, 783.99, 1046.5].forEach((f, i) => tone(f, 0.16, 0.22, i * 0.12)),
      draw: () => { tone(392, 0.18, 0.2); tone(330, 0.22, 0.2, 0.16); },
      undo: () => { tone(240, 0.08, 0.2); tone(180, 0.1, 0.16, 0.08); },
    };
  })();

  // ---------------- HTTP ----------------
  async function api(method, path, body) {
    const res = await fetch(API + path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch { /* 可能没有响应体 */ }
    if (!res.ok) {
      const err = new Error((data?.error?.message) || `HTTP ${res.status}`);
      err.code = data?.error?.code;
      err.status = res.status;
      throw err;
    }
    return data;
  }

  // ---------------- 状态显示 ----------------
  const canPlay = () => !!game && !busy && game.status === "playing" &&
    (game.mode === "pvp" || game.turn === game.humanColor);

  function statusLine() {
    if (!game) return "连接后台中…";
    if (busy) return "AI 思考中…";
    if (game.status === "won") {
      if (game.mode === "pvp") return (game.winner === "black" ? "黑棋" : "白棋") + "获胜！";
      return game.winner === game.humanColor ? "🎉 你赢了！" : "AI 获胜，再接再厉！";
    }
    if (game.status === "draw") return "平局 · 棋盘已满";
    const who = game.turn === "black" ? "黑棋" : "白棋";
    if (game.mode === "pvp") return who + "的回合";
    return (game.turn === game.humanColor ? "你的回合 · " : "AI 的回合 · ") + who;
  }

  function setStatus(text, cls = "") {
    statusEl.textContent = text;
    statusEl.className = "status" + (cls ? " " + cls : "");
  }

  function refreshStatus() {
    if (statusEl.classList.contains("error")) return;
    setStatus(statusLine(), busy ? "think" : "");
  }

  function refreshButtons() {
    undoBtn.disabled = busy || !game || game.moveCount === 0;
    restartBtn.disabled = busy;
  }

  // ---------------- 绘制 ----------------
  const xy = (r, c) => [PAD + c * CELL + 6, PAD + r * CELL + 6];

  function draw() {
    ctx.clearRect(0, 0, SIZE, SIZE);

    // 木框与棋盘
    ctx.fillStyle = "#b98a4a";
    ctx.fillRect(0, 0, SIZE, SIZE);
    ctx.fillStyle = "#d3a15f";
    ctx.fillRect(6, 6, SIZE - 12, SIZE - 12);
    const g = ctx.createLinearGradient(0, 0, SIZE, SIZE);
    g.addColorStop(0, "#e9c088");
    g.addColorStop(1, "#dcac6d");
    ctx.fillStyle = g;
    ctx.fillRect(12, 12, SIZE - 24, SIZE - 24);

    // 网格
    ctx.strokeStyle = "#5c3a14";
    ctx.lineWidth = 1;
    for (let i = 0; i < N; i++) {
      const [x0, y0] = xy(i, 0);
      const [x1, y1] = xy(i, N - 1);
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(y0, x0); ctx.lineTo(y1, x1); ctx.stroke();
    }
    ctx.lineWidth = 2;
    ctx.strokeRect(PAD + 6, PAD + 6, CELL * (N - 1), CELL * (N - 1));

    // 星位
    ctx.fillStyle = "#5c3a14";
    for (const [r, c] of [[3, 3], [3, 11], [11, 3], [11, 11], [7, 7]]) {
      const [x, y] = xy(r, c);
      ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2); ctx.fill();
    }

    if (!game) return;

    // 悬停虚影
    if (hover && canPlay() && game.board[hover.r][hover.c] === EMPTY) {
      drawStone(hover.r, hover.c, game.turn === "black" ? BLACK : WHITE, false, 0.35, 1);
    }

    // 棋子（含落子动画）
    const now = performance.now();
    for (let r = 0; r < N; r++) {
      for (let c = 0; c < N; c++) {
        const v = game.board[r][c];
        if (v === EMPTY) continue;
        let scale = 1;
        const key = r + "," + c;
        if (anim[key] !== undefined) {
          const p = Math.min(1, (now - anim[key]) / 150);
          scale = 1.25 - 0.25 * p;
          if (p >= 1) delete anim[key];
        }
        drawStone(r, c, v, false, 1, scale);
      }
    }
    if (game.lastMove) {
      const [r, c] = game.lastMove;
      drawStone(r, c, game.board[r][c], true, 1, 1);
    }
    if (game.winCells) drawWinLine(game.winCells);
  }

  function drawStone(r, c, color, mark, alpha = 1, scale = 1) {
    const [x, y] = xy(r, c);
    const rad = CELL * 0.42 * scale;
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.beginPath();
    ctx.arc(x + 1, y + 3, rad, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(0,0,0,.28)";
    ctx.fill();
    const grad = ctx.createRadialGradient(x - rad * .35, y - rad * .4, rad * .15, x, y, rad);
    if (color === BLACK) {
      grad.addColorStop(0, "#6b7280");
      grad.addColorStop(.45, "#27303f");
      grad.addColorStop(1, "#080b12");
    } else {
      grad.addColorStop(0, "#ffffff");
      grad.addColorStop(.55, "#eef1f5");
      grad.addColorStop(1, "#b9c0cc");
    }
    ctx.beginPath();
    ctx.arc(x, y, rad, 0, Math.PI * 2);
    ctx.fillStyle = grad;
    ctx.fill();
    ctx.strokeStyle = color === BLACK ? "rgba(255,255,255,.12)" : "rgba(0,0,0,.28)";
    ctx.lineWidth = 1;
    ctx.stroke();
    if (mark) {
      ctx.beginPath();
      ctx.arc(x, y, rad * 0.34, 0, Math.PI * 2);
      ctx.fillStyle = "#fbbf24";
      ctx.fill();
    }
    ctx.restore();
  }

  function drawWinLine(cells) {
    if (!cells || cells.length < 2) return;
    const [x0, y0] = xy(cells[0][0], cells[0][1]);
    const [x1, y1] = xy(cells[cells.length - 1][0], cells[cells.length - 1][1]);
    ctx.save();
    ctx.globalAlpha = 0.45 + 0.55 * Math.abs(Math.sin(pulse));
    ctx.strokeStyle = "#fbbf24";
    ctx.lineWidth = pulse > Math.PI / 2 ? 6 : 3;
    ctx.lineCap = "round";
    ctx.shadowColor = "rgba(251,191,36,.85)";
    ctx.shadowBlur = 18;
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.lineTo(x1, y1);
    ctx.stroke();
    ctx.restore();
  }

  // 统一动画帧：金线脉冲 + 落子缩放
  function frame() {
    pulse += 0.18;
    if (game && (game.winCells || Object.keys(anim).length)) draw();
    requestAnimationFrame(frame);
  }

  // ---------------- 结果应用 ----------------
  function applyResult(res, opts = {}) {
    const prevCount = game ? game.moveCount : 0;
    game = res.state;
    gameId = game.id;

    const newMoves = game.moves.slice(prevCount);
    const now = performance.now();
    if (!opts.silent) {
      newMoves.forEach((mv, i) => {
        anim[mv[0] + "," + mv[1]] = now + i * 110;
        setTimeout(() => Sound.stone(), i * 110);
      });
    }
    if (game.status === "won") {
      const humanWon = game.mode === "pvp" || game.winner === game.humanColor;
      setTimeout(() => (humanWon ? Sound.win() : Sound.draw()), 60 + newMoves.length * 110);
    } else if (game.status === "draw") {
      setTimeout(() => Sound.draw(), 60);
    }

    if (res.aiMeta) {
      aiTip = `AI：${res.aiMeta.difficulty} · 深度 ${res.aiMeta.depth} · ${res.aiMeta.elapsedMs}ms`;
      engineEl.textContent = "· " + aiTip;
    }
    hover = null;
    refreshButtons();
    refreshStatus();
    draw();
  }

  async function request(fn, opts) {
    busy = true;
    refreshButtons();
    setStatus(statusLine(), "think");
    try {
      const res = await fn();
      applyResult(res, opts);
      return true;
    } catch (err) {
      if (err.code === "game-not-found") {
        setStatus("后台上的这局棋没了（服务可能重启过），正在新开一局…", "error");
        await newGame({ silent: true });
        return false;
      }
      setStatus(err.message, "error");
      return false;
    } finally {
      busy = false;
      refreshButtons();
      refreshStatus();
    }
  }

  const opts = () => ({
    mode: document.querySelector('input[name="mode"]:checked').value,
    difficulty: document.querySelector('input[name="difficulty"]:checked').value,
    humanColor: document.querySelector('input[name="humanColor"]:checked').value,
  });

  function syncControls() {
    if (!game) return;
    for (const [name, value] of [["mode", game.mode], ["difficulty", game.difficulty], ["humanColor", game.humanColor]]) {
      const el = document.querySelector(`input[name="${name}"][value="${value}"]`);
      if (el) el.checked = true;
    }
  }

  function newGame(extra = {}) {
    gameId = null;
    return request(async () => {
      const res = await api("POST", "/api/games", { ...opts(), ...extra });
      syncControls();
      return res;
    }, { silent: extra.silent });
  }

  // ---------------- 事件 ----------------
  document.querySelectorAll('.group input[type="radio"]').forEach((el) => {
    el.addEventListener("change", () => {
      if (busy) return;
      if (!gameId) { newGame(); return; }
      request(() => api("PATCH", `/api/games/${gameId}`, opts()));
    });
  });

  undoBtn.addEventListener("click", () => {
    if (busy || !game || game.moveCount === 0) return;
    request(async () => {
      Sound.undo();
      return api("POST", `/api/games/${gameId}/undo`, {});
    }, { silent: true });
  });

  restartBtn.addEventListener("click", () => {
    if (busy || !gameId) return;
    request(() => api("POST", `/api/games/${gameId}/reset`, {}), { silent: true });
  });

  soundBtn.addEventListener("click", () => {
    soundOn = !soundOn;
    soundBtn.textContent = soundOn ? "🔊 音效：开" : "🔇 音效：关";
  });

  function cellFromEvent(e) {
    const rect = cv.getBoundingClientRect();
    const scale = SIZE / rect.width;              // 响应式缩放换算
    const x = (e.clientX - rect.left) * scale;
    const y = (e.clientY - rect.top) * scale;
    const c = Math.round((x - PAD - 6) / CELL);
    const r = Math.round((y - PAD - 6) / CELL);
    return (r >= 0 && r < N && c >= 0 && c < N) ? { r, c } : null;
  }

  cv.addEventListener("click", (e) => {
    const cell = cellFromEvent(e);
    if (!cell) return;
    if (!canPlay() || game.board[cell.r][cell.c] !== EMPTY) return;
    request(() => api("POST", `/api/games/${gameId}/moves`, { r: cell.r, c: cell.c, ai: true }));
  });

  cv.addEventListener("mousemove", (e) => {
    const cell = cellFromEvent(e);
    const changed = (!cell && hover) || (cell && (!hover || cell.r !== hover.r || cell.c !== hover.c));
    if (changed) { hover = cell; draw(); }
  });

  cv.addEventListener("mouseleave", () => {
    if (hover) { hover = null; draw(); }
  });

  window.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
      e.preventDefault();
      undoBtn.click();
    }
  });

  // 后台重启等情况：切回页面时刷新一下
  document.addEventListener("visibilitychange", async () => {
    if (document.visibilityState !== "visible" || !gameId || busy) return;
    try {
      const res = await api("GET", `/api/games/${gameId}`);
      applyResult(res, { silent: true });
    } catch (err) {
      if (err.code === "game-not-found") await newGame({ silent: true });
    }
  });

  // ---------------- 启动 ----------------
  (async function init() {
    try {
      const health = await api("GET", "/api/health");
      engineEl.textContent = `· ${health.engine.algorithm}`;
      await newGame({ silent: true });
    } catch (err) {
      setStatus("连不上后台：" + err.message + "（请先运行 gomoku_server.py）", "error");
      draw();
    }
  })();

  frame();
})();
