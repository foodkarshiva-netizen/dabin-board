"""내용 도식(다이어그램) HTML 생성. images.py 가 이 HTML 을 카드로 렌더링한다.

summary.json 의 diagrams[] 항목 하나가 도식 한 장이다.
  {"type": "flow", "title": "...", "section_index": 2, "caption": "...", "data": {...}}

지원 type 과 data 형식
- flow     : {"steps": ["A", "B", "C", "D"]}                       원인→결과 흐름 (4개까지 가로, 그 이상 세로)
- cycle    : {"steps": ["A", "B", "C"]}                            서로 맞물려 도는 순환 고리 (3~5개)
- compare  : {"unit": "%", "items": [{"label": "...", "value": 4.4}, ...], "highlight": 0}   가로 막대 비교
- trend    : {"unit": "원", "points": [{"label": "2023", "value": 1300}, ...], "note": "..."}   시간에 따른 변화(막대)
- factors  : {"items": [{"cause": "물가가 오른다", "effect": "돈 가치 하락", "dir": "down"}, ...]}   원인 → 결과 목록
- versus   : {"left": {"title": "흔한 생각", "items": [...]}, "right": {"title": "영상의 답", "items": [...]}}   두 생각 비교
- steps    : {"steps": [{"title": "...", "desc": "..."}, ...]}     번호 매긴 단계 (세로)
"""
from __future__ import annotations

import html
import math

BLUE = "#1f2937"
BLUE_DARK = "#1f2937"
BLUE_LIGHT = "#f5f3ee"
RED = "#e11d48"
RED_LIGHT = "#ffe4e6"
GREEN = "#059669"
GREEN_LIGHT = "#d1fae5"
GRAY = "#8a95a3"
INK = "#1b2430"

DIAGRAM_CSS = """
.dg{margin-top:34px}
.dg-flow{display:flex;align-items:stretch;gap:0;flex-wrap:nowrap}
.dg-flow .box{flex:1;min-width:0;background:#f5f3ee;border:3px solid #e7e5e4;border-radius:20px;padding:26px 22px;font-size:34px;font-weight:700;line-height:1.35;display:flex;align-items:center;justify-content:center;text-align:center;word-break:keep-all}
.dg-flow .box.last{background:#1f2937;border-color:#1f2937;color:#fff}
.dg-flow .arr{flex:0 0 56px;display:flex;align-items:center;justify-content:center;color:#1f2937;font-size:44px;font-weight:700}
.dg-vflow{display:flex;flex-direction:column;gap:0;align-items:stretch}
.dg-vflow .box{background:#f5f3ee;border:3px solid #e7e5e4;border-radius:20px;padding:22px 30px;font-size:36px;font-weight:700;line-height:1.35;text-align:center;word-break:keep-all}
.dg-vflow .box.last{background:#1f2937;border-color:#1f2937;color:#fff}
.dg-vflow .arr{height:54px;display:flex;align-items:center;justify-content:center;color:#1f2937;font-size:44px;font-weight:700}
.dg-cmp{display:flex;flex-direction:column;gap:26px}
.dg-cmp .row{display:grid;grid-template-columns:330px 1fr 150px;align-items:center;gap:20px}
.dg-cmp .lab{font-size:34px;font-weight:700;line-height:1.3;word-break:keep-all}
.dg-cmp .track{height:54px;background:#efece6;border-radius:14px;overflow:hidden}
.dg-cmp .bar{height:100%;background:#d6d3d1;border-radius:14px}
.dg-cmp .bar.hi{background:#1f2937}
.dg-cmp .val{font-size:38px;font-weight:700;color:#1f2937;text-align:right}
.dg-fac{display:flex;flex-direction:column;gap:22px}
.dg-fac .row{display:grid;grid-template-columns:1fr 90px 1fr;align-items:center;gap:16px}
.dg-fac .c{background:#f5f3ee;border-radius:18px;padding:22px 26px;font-size:34px;font-weight:700;text-align:center;line-height:1.35;word-break:keep-all}
.dg-fac .a{text-align:center;font-size:44px;font-weight:700;color:#b45309}
.dg-fac .e{border-radius:18px;padding:22px 26px;font-size:34px;font-weight:700;text-align:center;line-height:1.35;word-break:keep-all}
.dg-fac .e.up{background:#d1fae5;color:#065f46}
.dg-fac .e.down{background:#ffe4e6;color:#9f1239}
.dg-fac .e.neutral{background:#f5f3ee;color:#1f2937}
.dg-vs{display:grid;grid-template-columns:1fr 1fr;gap:26px}
.dg-vs .col{border-radius:22px;padding:30px 32px}
.dg-vs .col.l{background:#fbf1ef;border:3px solid #f3d5d0}
.dg-vs .col.r{background:#f5f3ee;border:3px solid #d6d3d1}
.dg-vs h4{font-size:34px;font-weight:700;margin-bottom:18px}
.dg-vs .col.l h4{color:#9f3b2f}
.dg-vs .col.r h4{color:#1f2937}
.dg-vs li{font-size:32px;line-height:1.45;margin-left:34px;word-break:keep-all;margin-bottom:10px}
.dg-steps{display:flex;flex-direction:column;gap:20px}
.dg-steps .st{display:flex;gap:22px;align-items:flex-start}
.dg-steps .n{flex:0 0 60px;height:60px;border-radius:50%;background:#1f2937;color:#fff;font-size:30px;font-weight:700;display:flex;align-items:center;justify-content:center}
.dg-steps .t{font-size:36px;font-weight:700;line-height:1.35;word-break:keep-all}
.dg-steps .d{font-size:30px;color:#3d4a5c;line-height:1.45;margin-top:6px;word-break:keep-all}
.dg-cap{margin-top:26px;font-size:28px;color:#52606d;line-height:1.45;word-break:keep-all}
"""


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _fmt(v, unit: str) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = f"{v:,}" if isinstance(v, (int, float)) else str(v)
    return f"{s}{unit}"


def flow(data: dict) -> str:
    steps = [str(s) for s in data.get("steps", []) if str(s).strip()]
    if not steps:
        return ""
    if len(steps) <= 4:
        parts = []
        for i, s in enumerate(steps):
            cls = "box last" if i == len(steps) - 1 else "box"
            parts.append(f"<div class='{cls}'>{_e(s)}</div>")
            if i < len(steps) - 1:
                parts.append("<div class='arr'>→</div>")
        return f"<div class='dg dg-flow'>{''.join(parts)}</div>"
    parts = []
    for i, s in enumerate(steps):
        cls = "box last" if i == len(steps) - 1 else "box"
        parts.append(f"<div class='{cls}'>{_e(s)}</div>")
        if i < len(steps) - 1:
            parts.append("<div class='arr'>↓</div>")
    return f"<div class='dg dg-vflow'>{''.join(parts)}</div>"


def cycle(data: dict) -> str:
    steps = [str(s) for s in data.get("steps", []) if str(s).strip()]
    n = len(steps)
    if n < 2:
        return ""
    W, H = 1130, 620
    cx, cy, r = W / 2, H / 2, 215
    bw, bh = 300, 110
    nodes = []
    for i in range(n):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        nodes.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    svg = [f"<svg class='dg' width='{W}' height='{H}' viewBox='0 0 {W} {H}' xmlns='http://www.w3.org/2000/svg'>",
           "<defs><marker id='ah' markerUnits='userSpaceOnUse' markerWidth='26' markerHeight='26' refX='20' refY='13' orient='auto'>"
           f"<path d='M0,0 L26,13 L0,26 z' fill='{BLUE}'/></marker></defs>",
           f"<circle cx='{cx}' cy='{cy}' r='{r}' fill='none' stroke='#e7e5e4' stroke-width='4' stroke-dasharray='10 12'/>"]
    # 노드 사이 호(arc) 화살표: 노드 박스 바깥에서 시작/끝나도록 각도를 줄인다
    trim = math.radians(360 / n * 0.34)
    for i in range(n):
        a0 = -math.pi / 2 + 2 * math.pi * i / n + trim
        a1 = -math.pi / 2 + 2 * math.pi * (i + 1) / n - trim
        x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
        x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        svg.append(f"<path d='M{x0:.1f},{y0:.1f} A{r},{r} 0 0 1 {x1:.1f},{y1:.1f}' fill='none' stroke='{BLUE}' stroke-width='6' marker-end='url(#ah)'/>")
    for i, (x, y) in enumerate(nodes):
        svg.append(f"<rect x='{x - bw / 2:.1f}' y='{y - bh / 2:.1f}' width='{bw}' height='{bh}' rx='22' fill='#fff' stroke='{BLUE_DARK}' stroke-width='4'/>")
        svg.append(f"<foreignObject x='{x - bw / 2:.1f}' y='{y - bh / 2:.1f}' width='{bw}' height='{bh}'>"
                   f"<div xmlns='http://www.w3.org/1999/xhtml' style='width:{bw}px;height:{bh}px;display:flex;align-items:center;justify-content:center;"
                   f"text-align:center;font-size:31px;font-weight:700;line-height:1.3;color:{INK};padding:0 14px;word-break:keep-all'>{_e(steps[i])}</div></foreignObject>")
    svg.append("</svg>")
    return "".join(svg)


def compare(data: dict) -> str:
    items = data.get("items", [])
    unit = data.get("unit", "")
    if not items:
        return ""
    vals = [float(it.get("value", 0)) for it in items]
    mx = float(data.get("max") or max(vals) or 1)
    hi = data.get("highlight", -1)
    rows = []
    for i, it in enumerate(items):
        pct = max(4, min(100, vals[i] / mx * 100))
        cls = "bar hi" if i == hi or (hi == -1 and vals[i] == max(vals)) else "bar"
        rows.append(f"<div class='row'><div class='lab'>{_e(it.get('label', ''))}</div>"
                    f"<div class='track'><div class='{cls}' style='width:{pct:.1f}%'></div></div>"
                    f"<div class='val'>{_e(_fmt(it.get('value', 0), unit))}</div></div>")
    return f"<div class='dg dg-cmp'>{''.join(rows)}</div>"


def trend(data: dict) -> str:
    pts = data.get("points", [])
    unit = data.get("unit", "")
    if len(pts) < 2:
        return ""
    W, H = 1130, 560
    padL, padR, padT, padB = 40, 40, 90, 90
    vals = [float(p.get("value", 0)) for p in pts]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    lo_axis = lo - span * 0.35
    n = len(pts)
    slot = (W - padL - padR) / n
    bw = min(150, slot * 0.6)
    svg = [f"<svg class='dg' width='{W}' height='{H}' viewBox='0 0 {W} {H}' xmlns='http://www.w3.org/2000/svg'>"]
    base_y = H - padB
    svg.append(f"<line x1='{padL}' y1='{base_y}' x2='{W - padR}' y2='{base_y}' stroke='#e7e5e4' stroke-width='3'/>")
    hl = data.get("highlight", n - 1)
    for i, p in enumerate(pts):
        v = vals[i]
        h = (v - lo_axis) / (hi - lo_axis) * (base_y - padT)
        x = padL + slot * i + (slot - bw) / 2
        y = base_y - h
        color = BLUE_DARK if i == hl else "#d6d3d1"
        svg.append(f"<rect x='{x:.1f}' y='{y:.1f}' width='{bw:.1f}' height='{h:.1f}' rx='14' fill='{color}'/>")
        svg.append(f"<text x='{x + bw / 2:.1f}' y='{y - 18:.1f}' text-anchor='middle' font-size='32' font-weight='700' fill='{INK}'>{_e(_fmt(p.get('value', 0), unit))}</text>")
        svg.append(f"<text x='{x + bw / 2:.1f}' y='{base_y + 46}' text-anchor='middle' font-size='28' fill='#52606d'>{_e(p.get('label', ''))}</text>")
    svg.append("</svg>")
    return "".join(svg)


def factors(data: dict) -> str:
    items = data.get("items", [])
    if not items:
        return ""
    rows = []
    for it in items:
        d = it.get("dir", "neutral")
        rows.append(f"<div class='row'><div class='c'>{_e(it.get('cause', ''))}</div><div class='a'>→</div>"
                    f"<div class='e {_e(d)}'>{_e(it.get('effect', ''))}</div></div>")
    return f"<div class='dg dg-fac'>{''.join(rows)}</div>"


def versus(data: dict) -> str:
    l, r = data.get("left", {}), data.get("right", {})
    def col(side: dict, cls: str) -> str:
        lis = "".join(f"<li>{_e(x)}</li>" for x in side.get("items", []))
        return f"<div class='col {cls}'><h4>{_e(side.get('title', ''))}</h4><ul>{lis}</ul></div>"
    return f"<div class='dg dg-vs'>{col(l, 'l')}{col(r, 'r')}</div>"


def steps(data: dict) -> str:
    items = data.get("steps", [])
    if not items:
        return ""
    rows = []
    for i, it in enumerate(items):
        if isinstance(it, str):
            it = {"title": it}
        desc = f"<div class='d'>{_e(it.get('desc', ''))}</div>" if it.get("desc") else ""
        rows.append(f"<div class='st'><div class='n'>{i + 1}</div><div><div class='t'>{_e(it.get('title', ''))}</div>{desc}</div></div>")
    return f"<div class='dg dg-steps'>{''.join(rows)}</div>"


RENDERERS = {"flow": flow, "cycle": cycle, "compare": compare, "trend": trend,
             "factors": factors, "versus": versus, "steps": steps}


def diagram_html(dtype: str, data: dict) -> str:
    fn = RENDERERS.get(dtype)
    if not fn:
        raise ValueError(f"지원하지 않는 도식 type: {dtype}")
    return fn(data or {})
