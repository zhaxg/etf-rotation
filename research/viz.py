"""SVG 图表生成 (dataviz 规范: 默认已验证色板, 2px 线, 悬停提示, 图例+直接标注)。"""
from __future__ import annotations

import math

LIGHT = {
    "surface": "#fcfcfb", "page": "#f9f9f7", "ink": "#0b0b0b", "ink2": "#52514e",
    "muted": "#898781", "grid": "#e1e0d9", "axis": "#c3c2b7",
    "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
}
DARK = {
    "surface": "#1a1a19", "page": "#0d0d0d", "ink": "#f0efec", "ink2": "#c3c2b7",
    "muted": "#898781", "grid": "#2c2c2a", "axis": "#383835",
    "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
}


def css_vars() -> str:
    def block(p: dict, sel: str) -> str:
        s = [f"{sel} {{", "  color-scheme: " + ("dark" if p is DARK else "light") + ";"]
        for k, v in {"surface-1": p["surface"], "page": p["page"], "text-primary": p["ink"],
                     "text-secondary": p["ink2"], "text-muted": p["muted"],
                     "grid": p["grid"], "axis": p["axis"]}.items():
            s.append(f"  --{k}: {v};")
        for i, c in enumerate(p["series"], 1):
            s.append(f"  --series-{i}: {c};")
        s.append("}")
        return "\n".join(s)
    return "\n".join([
        block(LIGHT, ".viz-root"),
        "@media (prefers-color-scheme: dark) { :root:where(:not([data-theme=\"light\"])) " + block(DARK, ".viz-root").replace("\n", "\n  ").replace(".viz-root {", ".viz-root {") + " }",
        block(DARK, ":root[data-theme=\"dark\"] .viz-root"),
    ])


def _fmt(v: float) -> str:
    return f"{v:,.2f}"


class Chart:
    """极简 SVG 折线/面积图, 支持对数轴与悬停十字线。"""

    def __init__(self, title: str, x_labels: list[str], *, width=860, height=340,
                 y_fmt=lambda v: f"{v:.1f}", log=False, y_label="", suffix=""):
        self.title = title
        self.x = x_labels
        self.w, self.h = width, height
        self.pad_l, self.pad_r, self.pad_t, self.pad_b = 56, 118, 28, 34
        self.series: list[tuple[str, list[float]]] = []
        self.y_fmt = y_fmt
        self.log = log
        self.y_label = y_label
        self.sfx = suffix

    def add(self, name: str, values: list[float]) -> None:
        self.series.append((name, values))

    def _transform(self, vals: list[float]) -> list[float]:
        return [math.log10(max(v, 1e-9)) for v in vals] if self.log else list(vals)

    def render(self, idx: int) -> str:
        n = len(self.x)
        xs = [self.pad_l + i * (self.w - self.pad_l - self.pad_r) / max(n - 1, 1) for i in range(n)]
        all_v = [v for _, vs in self.series for v in vs if v is not None and (not self.log or v > 0)]
        lo, hi = min(all_v), max(all_v)
        if self.log:
            tlo, thi = math.log10(lo), math.log10(hi)
        else:
            tlo, thi = lo, hi
        span = (thi - tlo) or 1.0
        tlo -= span * 0.06
        thi += span * 0.06

        def ypx(v: float) -> float:
            t = math.log10(max(v, 1e-9)) if self.log else v
            return self.pad_t + (thi - t) / (thi - tlo) * (self.h - self.pad_t - self.pad_b)

        out = []
        # 网格与 y 轴刻度: 对数轴用 1-2-5 系列, 线性轴 5 档
        if self.log:
            ticks = []
            for e in range(math.floor(math.log10(lo)) - 1, math.ceil(math.log10(hi)) + 1):
                for m in (1, 2, 5):
                    v = m * 10 ** e
                    if tlo <= math.log10(v) <= thi:
                        ticks.append(v)
        else:
            ticks = [tlo + (thi - tlo) * k / 4 for k in range(5)]
        for v in ticks:
            y = ypx(v)
            out.append(f'<line x1="{self.pad_l}" y1="{y:.1f}" x2="{self.w - self.pad_r}" y2="{y:.1f}" '
                       f'stroke="var(--grid)" stroke-width="1"/>')
            out.append(f'<text x="{self.pad_l - 8}" y="{y + 4:.1f}" text-anchor="end" '
                       f'font-size="11" fill="var(--text-muted)" style="font-variant-numeric:tabular-nums">'
                       f'{self.y_fmt(v)}</text>')
        # x 轴刻度: 每年一个
        step = max(1, n // 7)
        for i in range(0, n, step):
            out.append(f'<text x="{xs[i]:.1f}" y="{self.h - 12}" text-anchor="middle" '
                       f'font-size="11" fill="var(--text-muted)">{self.x[i]}</text>')
        out.append(f'<line x1="{self.pad_l}" y1="{self.h - self.pad_b}" x2="{self.w - self.pad_r}" '
                   f'y2="{self.h - self.pad_b}" stroke="var(--axis)" stroke-width="1"/>')
        # 折线
        colors = []
        for si, (name, vs) in enumerate(self.series):
            pts = []
            for i, v in enumerate(vs):
                if v is None:
                    continue
                pts.append((xs[i], ypx(v)))
            d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
            out.append(f'<path d="{d}" fill="none" stroke="var(--series-{si + 1})" stroke-width="2" '
                       f'stroke-linejoin="round" stroke-linecap="round"/>')
            # 末端直接标注
            lx, ly = pts[-1]
            out.append(f'<text x="{lx + 6:.1f}" y="{ly + 4:.1f}" font-size="11" '
                       f'fill="var(--text-secondary)">{name}</text>')
            colors.append(f"var(--series-{si + 1})")
        # 悬停层
        out.append(f'<rect x="{self.pad_l}" y="{self.pad_t}" width="{self.w - self.pad_l - self.pad_r}" '
                   f'height="{self.h - self.pad_t - self.pad_b}" fill="transparent" class="hover-area" '
                   f'data-chart="{idx}"/>')
        out.append(f'<line class="xhair" x1="0" y1="{self.pad_t}" x2="0" y2="{self.h - self.pad_b}" '
                   f'stroke="var(--axis)" stroke-width="1" visibility="hidden"/>')
        js_data = {"xs": [round(x, 1) for x in xs], "labels": self.x,
                   "series": [[None if v is None else round(v, 4) for v in vs] for _, vs in self.series],
                   "names": [name for name, _ in self.series], "sfx": self.sfx}
        legend = " · ".join(
            f'<span class="lg"><i style="background:{colors[i]}"></i>{name}</span>'
            for i, (name, _) in enumerate(self.series))
        return (f'<div class="chart" data-idx="{idx}">\n'
                f'<div class="chart-title">{self.title}</div>\n'
                f'<div class="legend">{legend}</div>\n'
                f'<svg viewBox="0 0 {self.w} {self.h}" class="svg">\n' + "\n".join(out) + '\n</svg>\n'
                f'<script type="application/json" class="cd">{_json(js_data, pretty=True)}</script>\n</div>')


def _json(obj, pretty: bool = False) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, indent=1 if pretty else None)


def grouped_bars(title: str, years: list[str], data: dict[str, dict[str, float]],
                 *, width=860, height=320) -> str:
    """data: 策略名 -> {年份: 收益率}。分组柱状, 2px 间隔, 顶端 4px 圆角。"""
    names = list(data)
    n_y, n_s = len(years), len(names)
    pad_l, pad_r, pad_t, pad_b = 52, 16, 26, 40
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    vals = [v for d in data.values() for v in d.values() if v is not None]
    lo, hi = min(0.0, min(vals)), max(vals)
    span = (hi - lo) or 1.0
    hi += span * 0.12
    lo -= span * 0.05

    def ypx(v):
        return pad_t + (hi - v) / (hi - lo) * plot_h

    out = []
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = ypx(v)
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" '
                   f'stroke="var(--grid)" stroke-width="1"/>')
        out.append(f'<text x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="11" '
                   f'fill="var(--text-muted)" style="font-variant-numeric:tabular-nums">{v * 100:.0f}%</text>')
    group_w = plot_w / n_y
    bar_w = min(26.0, (group_w - 10) / n_s - 2)
    for yi, yr in enumerate(years):
        gx = pad_l + yi * group_w
        out.append(f'<text x="{gx + group_w / 2:.1f}" y="{height - 14}" text-anchor="middle" '
                   f'font-size="11" fill="var(--text-muted)">{yr}</text>')
        for si, name in enumerate(names):
            v = data[name].get(yr)
            if v is None:
                continue
            x = gx + (group_w - (bar_w + 2) * n_s) / 2 + si * (bar_w + 2)
            y0, y1 = ypx(0.0), ypx(v)
            top, bot = min(y0, y1), max(y0, y1)
            h = max(bot - top, 1.5)
            r = min(4.0, h)
            # 圆角只在数据端
            if v >= 0:
                d = (f"M{x:.1f},{bot:.1f} L{x:.1f},{top + r:.1f} Q{x:.1f},{top:.1f} {x + r:.1f},{top:.1f} "
                     f"L{x + bar_w - r:.1f},{top:.1f} Q{x + bar_w:.1f},{top:.1f} {x + bar_w:.1f},{top + r:.1f} "
                     f"L{x + bar_w:.1f},{bot:.1f} Z")
            else:
                d = (f"M{x:.1f},{top:.1f} L{x:.1f},{bot - r:.1f} Q{x:.1f},{bot:.1f} {x + r:.1f},{bot:.1f} "
                     f"L{x + bar_w - r:.1f},{bot:.1f} Q{x + bar_w:.1f},{bot:.1f} {x + bar_w:.1f},{bot - r:.1f} "
                     f"L{x + bar_w:.1f},{top:.1f} Z")
            out.append(f'<path d="{d}" fill="var(--series-{si + 1})" class="bar" '
                       f'data-tip="{name} {yr}: {v * 100:.1f}%"/>')
        out.append(f'<line x1="{gx + group_w:.1f}" y1="{pad_t}" x2="{gx + group_w:.1f}" '
                   f'y2="{height - pad_b}" stroke="var(--grid)" stroke-width="0"/>')
    out.append(f'<line x1="{pad_l}" y1="{ypx(0):.1f}" x2="{width - pad_r}" y2="{ypx(0):.1f}" '
               f'stroke="var(--axis)" stroke-width="1"/>')
    legend = " · ".join(f'<span class="lg"><i style="background:var(--series-{i + 1})"></i>{n}</span>'
                        for i, n in enumerate(names))
    return (f'<div class="chart"><div class="chart-title">{title}</div>'
            f'<div class="legend">{legend}</div>'
            f'<svg viewBox="0 0 {width} {height}" class="svg">{"\n".join(out)}</svg></div>')


def scatter(title: str, points: list[tuple[float, float, str]], highlights: dict[str, tuple[float, float, str]],
            x_label: str, y_label: str, *, width=860, height=380) -> str:
    """points: (x, y, 名称) 灰点; highlights: 名称 -> (x, y, css色变量)。"""
    pad_l, pad_r, pad_t, pad_b = 58, 130, 26, 44
    xs = [p[0] for p in points] + [v[0] for v in highlights.values()]
    ys = [p[1] for p in points] + [v[1] for v in highlights.values()]
    xlo, xhi = min(xs), max(xs)
    ylo, yhi = min(ys), max(ys)
    xspan, yspan = (xhi - xlo) or 1, (yhi - ylo) or 1
    xlo -= xspan * 0.06; xhi += xspan * 0.06
    ylo -= yspan * 0.08; yhi += yspan * 0.08

    def px(v): return pad_l + (v - xlo) / (xhi - xlo) * (width - pad_l - pad_r)
    def py(v): return pad_t + (yhi - v) / (yhi - ylo) * (height - pad_t - pad_b)

    out = []
    for k in range(5):
        yv = ylo + (yhi - ylo) * k / 4
        y = py(yv)
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" '
                   f'stroke="var(--grid)" stroke-width="1"/>')
        out.append(f'<text x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="11" '
                   f'fill="var(--text-muted)">{yv * 100:.0f}%</text>')
        xv = xlo + (xhi - xlo) * k / 4
        x = px(xv)
        out.append(f'<text x="{x:.1f}" y="{height - 18}" text-anchor="middle" font-size="11" '
                   f'fill="var(--text-muted)">{xv * 100:.0f}%</text>')
    dots = [[round(px(xv), 1), round(py(yv), 1)] for xv, yv, _ in points]
    hi_payload = {}
    for name, (xv, yv, color) in highlights.items():
        hi_payload[name] = [round(px(xv), 1), round(py(yv), 1), color,
                            round(xv, 4), round(yv, 4)]
        out.append(f'<text x="{px(xv) + 10:.1f}" y="{py(yv) + 4:.1f}" font-size="11" '
                   f'fill="var(--text-secondary)">{name}</text>')
    out.append('<g class="scatter-g"></g>')
    out.append('<script type="application/json" class="sd">\n'
               + _json({"dots": dots, "hi": hi_payload}, pretty=True) + '\n</script>')
    out.append(f'<text x="{(pad_l + width - pad_r) / 2:.0f}" y="{height - 2}" text-anchor="middle" '
               f'font-size="11" fill="var(--text-muted)">{x_label}</text>')
    out.append(f'<text x="14" y="{(pad_t + height - pad_b) / 2:.0f}" text-anchor="middle" font-size="11" '
               f'fill="var(--text-muted)" transform="rotate(-90 14 {(pad_t + height - pad_b) / 2:.0f})">'
               f'{y_label}</text>')
    return (f'<div class="chart"><div class="chart-title">{title}</div>'
            f'<svg viewBox="0 0 {width} {height}" class="svg">{"\n".join(out)}</svg></div>')


TOOLTIP_JS = r"""
<script>
(function(){
  const tip = document.createElement('div');
  tip.className = 'viz-tip'; document.body.appendChild(tip);
  function show(html, x, y){ tip.innerHTML = html; tip.style.display = 'block';
    const r = tip.getBoundingClientRect();
    tip.style.left = Math.min(x + 14, window.innerWidth - r.width - 8) + 'px';
    tip.style.top = Math.max(y - r.height - 12, 8) + 'px'; }
  function hide(){ tip.style.display = 'none'; }
  document.querySelectorAll('.sd').forEach(sd=>{
    const d = JSON.parse(sd.textContent);
    const g = sd.previousElementSibling;
    const NS = 'http://www.w3.org/2000/svg';
    d.dots.forEach(([x,y])=>{
      const c = document.createElementNS(NS,'circle');
      c.setAttribute('cx',x); c.setAttribute('cy',y); c.setAttribute('r','3.2');
      c.setAttribute('fill','var(--text-muted)'); c.setAttribute('opacity','0.45');
      g.appendChild(c);
    });
    Object.entries(d.hi).forEach(([name,[x,y,color,xv,yv]])=>{
      const c = document.createElementNS(NS,'circle');
      c.setAttribute('cx',x); c.setAttribute('cy',y); c.setAttribute('r','7');
      c.setAttribute('fill',color); c.setAttribute('stroke','var(--surface-1)');
      c.setAttribute('stroke-width','2'); c.setAttribute('class','dot');
      c.setAttribute('data-tip', `${name}: 年化 ${(yv*100).toFixed(1)}%, 回撤 ${(xv*100).toFixed(1)}%`);
      g.appendChild(c);
    });
  });
  document.querySelectorAll('.bar,.dot').forEach(el=>{
    el.addEventListener('mousemove', e=>show(el.dataset.tip, e.clientX, e.clientY));
    el.addEventListener('mouseleave', hide);
  });
  document.querySelectorAll('.chart').forEach(ch=>{
    const area = ch.querySelector('.hover-area'); if(!area) return;
    const data = JSON.parse(ch.querySelector('.cd').textContent);
    const svg = ch.querySelector('svg'); const hair = ch.querySelector('.xhair');
    area.addEventListener('mousemove', e=>{
      const r = svg.getBoundingClientRect();
      const sx = (e.clientX - r.left) / r.width * 860;
      let best = 0, bd = 1e9;
      data.xs.forEach((x,i)=>{ const d = Math.abs(x - sx); if(d < bd){ bd = d; best = i; } });
      hair.setAttribute('x1', data.xs[best]); hair.setAttribute('x2', data.xs[best]);
      hair.setAttribute('visibility', 'visible');
      const rows = data.series.map((s,i)=>{
        const v = s[best];
        const txt = v==null ? '—' : (data.sfx ? v.toFixed(1)+data.sfx : (v>=10?v.toFixed(2):v.toFixed(3)));
        return `<div class="tr"><i style="background:var(--series-${i+1})"></i>${data.names[i]} <b>${txt}</b></div>`;
      }).join('');
      show(`<div class="th">${data.labels[best]}</div>${rows}`, e.clientX, e.clientY);
    });
    area.addEventListener('mouseleave', ()=>{ hair.setAttribute('visibility','hidden'); hide(); });
  });
})();
</script>
"""
