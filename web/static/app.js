/* ETF 轮动 · 前端逻辑 (原生 JS, 用户态存 localStorage) */

const $ = (sel) => document.querySelector(sel);

const state = {
  instruments: [],   // 备选池 (固定清单 + 用户拉取的)
  strategy: null,
};

/* ───────── 工具 ───────── */

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 2600);
}

const pct = (x) => (x * 100).toFixed(1) + "%";
const pctTrim = (x) => (x * 100).toFixed(1).replace(/\.0$/, "") + "%";  // 0.0% → 0%
const price = (x) => x >= 100 ? x.toFixed(2) : x.toFixed(3);
const momCls = (x) => x > 0 ? "up" : x < 0 ? "down" : "";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function requestJson(url, options = {}) {
  let res;
  try {
    res = await fetch(url, options);
  } catch (err) {
    throw new Error("网络连接失败，请稍后重试");
  }
  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    const text = await res.text().catch(() => "");
    const message = text.trim().startsWith("{")
      ? (JSON.parse(text).detail || text)
      : `接口返回异常 (${res.status})`;
    throw new Error(message || `请求失败 (${res.status})`);
  }
  let data;
  try {
    data = await res.json();
  } catch {
    throw new Error("接口返回的数据不是有效 JSON");
  }
  if (!res.ok) throw new Error(data.detail || `请求失败 (${res.status})`);
  return data;
}

function fmtAmount(x) {
  if (!isFinite(x) || x <= 0) return "—";
  if (x >= 1e8) return (x / 1e8).toFixed(2) + "亿";
  if (x >= 1e4) return Math.round(x / 1e4).toLocaleString("zh-CN") + "万";
  return Math.round(x).toLocaleString("zh-CN");
}

function chgText(x) {
  return x == null || !isFinite(x) ? "—" : (x >= 0 ? "+" : "") + pct(x);
}

/* ───────── 当日分时 sparkline (三层封装: 纯绘图 → 单元格组件 → 数据渲染) ───────── */

// 第一层: 纯绘图 — 只负责把分时价画到 canvas, 不关心 DOM、不关心数据来源
function drawSparkline(canvas, prices, prevClose) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const w = rect.width, h = rect.height;
  if (w === 0 || h === 0) return;

  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  if (!prices || prices.length < 2) return;

  // canvas 不能直接用 CSS 变量, 从主题取色 (跟随系统明暗)
  const cs = getComputedStyle(document.documentElement);
  const cUp = cs.getPropertyValue("--up").trim();
  const cDown = cs.getPropertyValue("--down").trim();
  const cAxis = cs.getPropertyValue("--line").trim();
  const cAvg = cs.getPropertyValue("--c2").trim();

  const base = prevClose > 0 ? prevClose : prices[0];
  const rel = prices.map((p) => ((p - base) / base) * 100);
  const minRel = Math.min(...rel, 0);
  const maxRel = Math.max(...rel, 0);
  const range = (maxRel - minRel) || 0.01;

  const pad = 5;
  const plotH = h - pad * 2;
  const plotW = w - pad * 2;
  const yOf = (v) => pad + ((maxRel - v) / range) * plotH;
  const zeroY = yOf(0);
  const xOf = (i) => pad + (i / (prices.length - 1)) * plotW;
  const points = rel.map((v, i) => ({ x: xOf(i), y: yOf(v), rel: v }));

  // 零轴虚线
  ctx.beginPath();
  ctx.setLineDash([3, 3]);
  ctx.strokeStyle = cAxis;
  ctx.lineWidth = 0.8;
  ctx.moveTo(pad, zeroY);
  ctx.lineTo(w - pad, zeroY);
  ctx.stroke();
  ctx.setLineDash([]);

  // 零轴以上填充 (涨) / 以下填充 (跌)
  ctx.globalAlpha = 0.18;
  ctx.beginPath();
  ctx.moveTo(points[0].x, zeroY);
  points.forEach((pt) => ctx.lineTo(pt.x, Math.min(pt.y, zeroY)));
  ctx.lineTo(points[points.length - 1].x, zeroY);
  ctx.closePath();
  ctx.fillStyle = cUp;
  ctx.fill();

  ctx.beginPath();
  ctx.moveTo(points[0].x, zeroY);
  points.forEach((pt) => ctx.lineTo(pt.x, Math.max(pt.y, zeroY)));
  ctx.lineTo(points[points.length - 1].x, zeroY);
  ctx.closePath();
  ctx.fillStyle = cDown;
  ctx.fill();
  ctx.globalAlpha = 1;

  // 折线分段染色 (涨红跌绿, 跨零轴处交于零轴)
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i], b = points[i + 1];
    const aboveA = a.rel >= 0, aboveB = b.rel >= 0;
    if (aboveA === aboveB) {
      ctx.beginPath();
      ctx.strokeStyle = aboveA ? cUp : cDown;
      ctx.lineWidth = 1.5;
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    } else {
      const t = -a.rel / (b.rel - a.rel);
      const crossX = a.x + t * (b.x - a.x);
      ctx.beginPath();
      ctx.strokeStyle = aboveA ? cUp : cDown;
      ctx.lineWidth = 1.5;
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(crossX, zeroY);
      ctx.stroke();
      ctx.beginPath();
      ctx.strokeStyle = aboveB ? cUp : cDown;
      ctx.lineWidth = 1.5;
      ctx.moveTo(crossX, zeroY);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }
  }

  // 均价线 (累计均价, 虚线)
  let sum = 0;
  const avgPoints = [];
  for (let i = 0; i < prices.length; i++) {
    sum += prices[i];
    const avgPct = ((sum / (i + 1) - base) / base) * 100;
    avgPoints.push({ x: xOf(i), y: yOf(avgPct) });
  }
  ctx.beginPath();
  ctx.setLineDash([4, 3]);
  ctx.strokeStyle = cAvg;
  ctx.lineWidth = 1.2;
  avgPoints.forEach((pt, i) => (i === 0 ? ctx.moveTo(pt.x, pt.y) : ctx.lineTo(pt.x, pt.y)));
  ctx.stroke();
  ctx.setLineDash([]);

  // 末点
  const last = points[points.length - 1];
  ctx.beginPath();
  ctx.arc(last.x, last.y, 2.5, 0, Math.PI * 2);
  ctx.fillStyle = last.rel >= 0 ? cUp : cDown;
  ctx.fill();
}

// 第二层: 组件 — 接收数据, 返回一个已经画好图的 <td>, 调用方不需要知道 canvas/坐标系细节
function createSparklineCell(prices, prevClose) {
  const td = document.createElement("td");
  td.className = "spark";
  const canvas = document.createElement("canvas");
  canvas.className = "sparkline";
  canvas._prices = prices;
  canvas._prevClose = prevClose;
  td.appendChild(canvas);
  // canvas 此刻尺寸可能为 0, 挂载进文档后下一帧再画
  requestAnimationFrame(() => drawSparkline(canvas, prices, prevClose));
  return td;
}

// 主题切换 → 全部分时图按新配色重绘
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  document.querySelectorAll("canvas.sparkline").forEach((c) =>
    drawSparkline(c, c._prices, c._prevClose));
});

/* ───────── 标签页 (轻量 hash 路由: #pool / #strategy) ───────── */

const TABS = ["pool", "strategy"];

function showTab(name, { writeHash = true } = {}) {
  if (!TABS.includes(name)) name = "pool";
  document.querySelectorAll(".tab").forEach((b) =>
    b.classList.toggle("is-active", b.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) =>
    p.classList.toggle("is-active", p.id === "panel-" + name));
  // 地址栏同步: 刷新 / 分享 / 前进后退都停在当前页签 (hash 不触发整页请求)
  if (writeHash && location.hash !== "#" + name) location.hash = name;
  if (name === "strategy" && !state.strategy && state.instruments.length) runStrategy();
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => showTab(btn.dataset.tab));
});

window.addEventListener("hashchange", () =>
  showTab(location.hash.slice(1), { writeHash: false }));

/* ───────── 备选池 ───────── */

async function fetchInstruments() {
  const data = await requestJson("/api/instruments");
  if (!Array.isArray(data)) throw new Error("接口返回的数据格式不正确");
  state.instruments = data;
  renderPool();
  // 直接以 #strategy 打开时, 等池数据到位再测算一次
  if ($("#panel-strategy").classList.contains("is-active") && !state.strategy) runStrategy();
}

// 第三层: 数据 + 渲染 — 一行调用即得分时单元格, 以后加行/加页面直接复用 createSparklineCell
function poolRow(x) {
  const tr = document.createElement("tr");
  tr.insertAdjacentHTML("beforeend", `
      <td><a class="link code" href="${x.link}" target="_blank" rel="noopener">${x.code}</a></td>
      <td>${esc(x.name)}</td>`);
  const spark = createSparklineCell(x.spark, x.pre);   // 复用点
  // 非交易时间 (周末/节假日/盘前) 行情回退到最近交易日, 提示里标明所属交易日
  const todayStr = new Date().toLocaleDateString("sv-SE");
  spark.title = `${x.day && x.day !== todayStr ? `最近交易日 ${x.day}` : "今日"} ${chgText(x.change_pct)}`;
  tr.appendChild(spark);
  tr.insertAdjacentHTML("beforeend", `
      <td class="num">${price(x.price)}</td>
      <td class="num ${momCls(x.change_pct)}">${chgText(x.change_pct)}</td>
      <td class="num">${fmtAmount(x.amount)}</td>
      <td class="num">${x.high52 != null ? pct(x.high52) : "—"}</td>
      <td class="num ${momCls(x.mom120)}">${x.mom120 != null ? pct(x.mom120) : "—"}</td>
      <td class="num ${momCls(x.ram30)}">${x.ram30 != null ? x.ram30.toFixed(2) : "—"}</td>`);
  return tr;
}

function renderPool() {
  const body = $("#pool-body");
  body.replaceChildren(...state.instruments.map(poolRow));
  const lasts = state.instruments.map((x) => x.last).filter(Boolean).sort();
  $("#pool-summary").textContent =
    `共 ${state.instruments.length} 只 · 更新至 ${lasts[0] || "—"} · 点击代码跳转富途`;
}

/* ───────── 回测净值曲线 ───────── */

const SERIES_COLOR = { high52: "var(--c1)", ram: "var(--c2)" };

function drawCurves(series) {
  // series: [{name, color, data: [{d, v}]}] — 双策略净值对比 (颜色跟策略固定)
  const el = $("#curve");
  const base = (series[0] && series[0].data) || [];
  if (base.length < 2) { el.innerHTML = ""; return; }
  const W = 820, H = 250, padL = 48, padR = 14, padT = 16, padB = 28;
  const iw = W - padL - padR, ih = H - padT - padB;
  const maps = series.map((s) => Object.fromEntries(s.data.map((p) => [p.d, p.v])));
  const dates = base.map((p) => p.d).filter((d) => maps.every((m) => m[d] != null));
  const n = dates.length;
  if (n < 2) { el.innerHTML = ""; return; }
  const all = dates.flatMap((d) => maps.map((m) => m[d]));
  let vMin = Math.min(...all), vMax = Math.max(...all);
  const span = (vMax - vMin) || 1;
  vMin -= span * 0.1; vMax += span * 0.1;
  const xOf = (i) => padL + (iw * i) / (n - 1);
  const yOf = (v) => padT + ih * (1 - (v - vMin) / (vMax - vMin));

  let grid = "";
  for (let k = 0; k <= 3; k++) {
    const t = vMin + ((vMax - vMin) * k) / 3;
    const y = yOf(t);
    grid += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}"` +
            ` stroke="var(--line-soft)" stroke-width="1"/>` +
            `<text x="${padL - 8}" y="${(y + 3.5).toFixed(1)}" text-anchor="end" class="axis-t">${t.toFixed(2)}</text>`;
  }
  let xl = "";
  dates.forEach((d, i) => {
    const yr = d.slice(0, 4);
    if (i === 0 || yr !== dates[i - 1].slice(0, 4)) {
      xl += `<text x="${xOf(i).toFixed(1)}" y="${H - 8}" text-anchor="middle" class="axis-t">${yr}</text>`;
    }
  });

  const paths = series.map((s, si) => {
    const line = dates.map((d, i) =>
      `${i ? "L" : "M"}${xOf(i).toFixed(1)} ${yOf(maps[si][d]).toFixed(1)}`).join("");
    return `<path d="${line}" fill="none" stroke="${s.color}" stroke-width="2"` +
           (s.dash ? ` stroke-dasharray="6 4"` : "") +
           ` stroke-linejoin="round" stroke-linecap="round"/>`;
  }).join("");
  const dots = series.map((s, si) =>
    `<circle id="curve-dot-${si}" r="3.5" fill="${s.color}" stroke="var(--card)"` +
    ` stroke-width="1.5" style="display:none"/>`).join("");

  el.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="回测净值曲线对比">
      ${grid}${xl}${paths}
      <line id="curve-cross" y1="${padT}" y2="${padT + ih}" stroke="var(--ink-3)"
            stroke-width="1" stroke-dasharray="3 3" style="display:none"/>
      ${dots}
      <rect x="${padL}" y="${padT}" width="${iw}" height="${ih}" fill="transparent"/>
    </svg>
    <div class="chart-tip" id="curve-tip" hidden></div>`;

  const svg = el.querySelector("svg");
  const cross = el.querySelector("#curve-cross");
  const tip = el.querySelector("#curve-tip");
  const dotEls = series.map((_, si) => el.querySelector(`#curve-dot-${si}`));

  svg.addEventListener("mousemove", (ev) => {
    const r = svg.getBoundingClientRect();
    const mx = ((ev.clientX - r.left) / r.width) * W;
    let i = Math.round(((mx - padL) / iw) * (n - 1));
    i = Math.max(0, Math.min(n - 1, i));
    const d = dates[i];
    cross.setAttribute("x1", xOf(i).toFixed(1));
    cross.setAttribute("x2", xOf(i).toFixed(1));
    cross.style.display = "";
    series.forEach((s, si) => {
      dotEls[si].setAttribute("cx", xOf(i).toFixed(1));
      dotEls[si].setAttribute("cy", yOf(maps[si][d]).toFixed(1));
      dotEls[si].style.display = "";
    });
    tip.hidden = false;
    tip.innerHTML = `<b>${d}</b>` + series.map((s, si) =>
      `<br>${esc(s.name)} ${maps[si][d].toFixed(3)}`).join("");
    const cr = el.getBoundingClientRect();
    let left = ev.clientX - cr.left + 12;
    if (left > cr.width - 190) left = ev.clientX - cr.left - 190;
    tip.style.left = left + "px";
    tip.style.top = (ev.clientY - cr.top - 12) + "px";
  });
  svg.addEventListener("mouseleave", () => {
    tip.hidden = true;
    cross.style.display = "none";
    dotEls.forEach((dot) => (dot.style.display = "none"));
  });
}

/* ───────── 策略持仓 ───────── */

async function runStrategy() {
  const box = $("#target-list");
  box.innerHTML = `<p class="muted">测算中…</p>`;
  try {
    const data = await requestJson("/api/strategy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pool: state.instruments.map((x) => ({ code: x.code })) }),
    });
    const keep = state.strategy && state.strategy.active;
    if (keep && data.strategies[keep]) data.active = keep;   // 保留 seg 切换
    state.strategy = data;
    renderStrategy();
    if (data.source === "live") toast(`实时测算完成 · ${data.as_of}`);
  } catch (err) {
    box.innerHTML = `<p class="muted">${esc(err.message || err)}</p>`;
    toast(String(err.message || err));
  }
}

function activeS() {
  const st = state.strategy;
  return st ? st.strategies[st.active] : null;
}

function renderStrategy() {
  const st = state.strategy;
  const s = activeS();
  if (!s) return;

  document.querySelectorAll("#strategy-seg .seg-btn").forEach((btn) =>
    btn.classList.toggle("is-on", btn.dataset.sid === st.active));

  // 信号时间 + 换手 + 决策合并成一行
  const asofTxt = st.source === "live" ? `实时 · 数据 ${st.data_as_of}` : `信号 ${st.as_of}`;
  $("#strategy-asof").innerHTML =
    `${asofTxt} · 换手 ${pctTrim(s.turnover)} · ` +
    (s.trade ? `<b class="warn">建议调仓</b>` : "维持现仓");

  const d = s.defense;
  // 数据一行 (点位取整) + 防守标记 + 距 60 日线偏离条
  // 偏离条: 全程 ±10% 量程, 中央刻度 = 60 日线, 向左右延伸表示领先/落后幅度
  const gapPct = d.ma60 ? (d.hs300 / d.ma60 - 1) * 100 : 0;
  const clamped = Math.max(-10, Math.min(10, gapPct));
  const halfW = Math.abs(clamped) / 10 * 50;
  const leftPct = clamped < 0 ? 50 - halfW : 50;
  const gapColor = clamped < 0 ? "var(--down)" : "var(--up)";
  $("#defense-text").innerHTML =
    `沪深300 当前/MA60 - <b>${Math.round(d.hs300)}</b>/<b>${Math.round(d.ma60)}</b> ` +
    `<b class="ma-gap" style="color:${gapColor}">${gapPct < 0 ? "-" : "+"}${Math.abs(gapPct).toFixed(1)}%</b>` +
    (d.state === "defense" ? ` / <b class="def-on">防守中</b>` : "") + `
    <div class="ma-track"><i class="ma-fill" style="left:${leftPct}%;width:${halfW}%;background:${gapColor}"></i></div>`;

  $("#target-list").innerHTML = (s.rank || []).map((t, i) => {
    const scoreTxt = t.score == null ? "防守仓"
      : s.id === "high52" ? (t.score >= 1 ? "创新高" : `距新高 ${pct(1 - t.score)}`)
      : `信号 ${t.score.toFixed(2)}`;
    return `
    <div class="target-row${t.picked ? " is-picked" : ""}">
      <div class="rank-num">${i + 1}</div>
      <div class="target-info">
        <div class="title">
          <a class="link code" href="${t.link}" target="_blank" rel="noopener">${t.code}</a>
          <span class="name" title="${esc(t.name)}">${esc(t.name)}</span>
        </div>
        <div class="desc">现价 ${price(t.price)} · ${scoreTxt}</div>
      </div>
      <div class="target-weight">${t.picked ? pct(t.weight) : "—"}</div>
    </div>`;
  }).join("") || `<p class="muted">当前无持仓信号。</p>`;

  // 操作指引为常驻静态文案 (index.html), 这里只补充临时的池变动提示
  $("#pool-note").textContent = st.pool_note || "";

  const b = s.backtest;
  const all = Object.values(st.strategies);
  const bm = st.benchmark;
  const series = all.map((x) => ({ name: x.label, color: SERIES_COLOR[x.id], data: x.curve }));
  if (bm && bm.curve && bm.curve.length) {
    series.push({ name: bm.name, color: "var(--ink-2)", data: bm.curve, dash: true });
  }
  drawCurves(series);
  $("#curve-legend").innerHTML = all.map((x) =>
    `<span class="lg"><i style="background:${SERIES_COLOR[x.id]}"></i>${esc(x.label)}</span>`).join("") +
    (bm ? `<span class="lg"><i class="dash"></i>${esc(bm.name)}（基准）</span>` : "");
  $("#backtest-stats").innerHTML = `
    <div class="stat"><div class="stat-v">${pct(b.cagr)}</div><div class="stat-k">年化</div></div>
    <div class="stat"><div class="stat-v">${b.sharpe.toFixed(2)}</div><div class="stat-k">夏普</div></div>
    <div class="stat"><div class="stat-v">${pct(b.mdd)}</div><div class="stat-k">最大回撤</div></div>
    <div class="stat"><div class="stat-v">${b.trades_yr}</div><div class="stat-k">年均交易</div></div>`;

  $("#compare-body").innerHTML = all.map((x) => `
    <tr class="${x.id === st.active ? "is-on" : ""}">
      <td>${esc(x.label)}</td>
      <td class="num">${pct(x.backtest.total)}</td>
      <td class="num">${pct(x.backtest.cagr)}</td>
      <td class="num">${x.backtest.sharpe.toFixed(2)}</td>
      <td class="num">${pct(x.backtest.mdd)}</td>
    </tr>`).join("") + (bm ? `
    <tr class="is-bench">
      <td>${esc(bm.name)}（基准）</td>
      <td class="num">${pct(bm.total)}</td>
      <td class="num">${pct(bm.cagr)}</td>
      <td class="num">${bm.sharpe.toFixed(2)}</td>
      <td class="num">${pct(bm.mdd)}</td>
    </tr>` : "");

  $("#history-summary").textContent = `最近 ${s.history.length} 次换仓，自新到旧`;
  $("#history-list").innerHTML = s.history.map((h) => `
    <tr>
      <td class="code">${h.date}</td>
      <td>${h.items.map((i) => `<span class="hold-chip">${esc(i.name)} ${pct(i.weight)}</span>`).join("") || "—"}</td>
    </tr>`).join("") || `<tr><td colspan="2" class="muted">暂无记录。</td></tr>`;
}

$("#btn-run").addEventListener("click", async () => {
  const btn = $("#btn-run");
  btn.disabled = true;
  btn.classList.add("is-loading");   // 图标旋转表示测算中
  try {
    await runStrategy();
  } finally {
    btn.disabled = false;
    btn.classList.remove("is-loading");
  }
});
$("#strategy-seg").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-sid]");
  if (!btn || !state.strategy) return;
  state.strategy.active = btn.dataset.sid;   // 双策略数据都在, 即时切换
  renderStrategy();
});

/* ───────── 启动 ───────── */

showTab(location.hash.slice(1) || "pool", { writeHash: false });  // 首屏按地址栏还原页签
fetchInstruments().catch((err) => toast("载入失败: " + err));
