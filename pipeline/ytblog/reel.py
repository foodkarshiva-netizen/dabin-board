"""릴스용 모션그래픽 영상: 숫자가 올라가고, 단어가 하나씩 튀어나오고, 막대가 자라고, 단계가 차례로 떨어지는 세로 영상.

장면: 후킹(숫자 카운트 + 문구) → 핵심 1 → 그래프(비교·추이) → 핵심 2 → 흐름도 → (핵심 3) → 마무리(팔로우·프로필 링크).
HTML/CSS 로 그린 뒤 render_frames.js 가 시간을 한 프레임씩 지정해 찍고, ffmpeg 로 배경음악(music.py)과 합친다.

  python -m ytblog reel <영상ID>     → out/<영상ID>/images/reel/reel.mp4 (게시 안 함)
"""
from __future__ import annotations

import html as _html
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import PIPELINE_DIR
from .images import _font_css
from .music import make_music
from .thumbs import ICONS, PALETTES, PATTERNS, _pick

W, H, FPS = 1080, 1920, 30
FRAMES_JS = PIPELINE_DIR / "render" / "render_frames.js"


def _e(s) -> str:
    return _html.escape(str(s or ""), quote=True)


def _plain(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("**", "").replace("*", "")).strip()


def _sents(s: str) -> list[str]:
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+", _plain(s)) if x.strip()]


# ---------------------------------------------------------------- 장면 조각
class _TL:
    """장면·요소의 시작 시각을 모아 두는 작은 타임라인."""

    def __init__(self):
        self.t = 0.0
        self.scenes: list[str] = []

    def scene(self, body: str, dur: float, cls: str = "") -> None:
        a, b = self.t, self.t + dur
        self.scenes.append(f"<section class='scene {cls}' data-a='{a:.2f}' data-b='{b:.2f}'>{body}</section>")
        self.t = b - 0.25          # 다음 장면과 살짝 겹치게


def _fx(kind: str, start: float, dur: float = 0.6, **attrs) -> str:
    extra = "".join(f" data-{k}='{_e(v)}'" for k, v in attrs.items())
    return f" data-fx='{kind}' data-in='{start:.2f}' data-dur='{dur:.2f}'{extra}"


def _count_attrs(value: str) -> tuple[str, dict] | None:
    m = re.match(r"^\s*([^\d]*?)([\d][\d,]*(?:\.\d+)?)(.*)$", value or "")
    if not m:
        return None
    pre, num, suf = m.groups()
    dec = len(num.split(".")[1]) if "." in num else 0
    return num, {"num": num.replace(",", ""), "dec": dec, "pre": pre, "suf": suf, "comma": 1 if "," in num else 0}


def _words(text: str, start: float, step: float = 0.11) -> tuple[str, float]:
    """**강조** 를 살려 단어 단위로 튀어나오게."""
    out, t, hl = [], start, False
    for tok in re.split(r"(\*\*)", text or ""):
        if tok == "**":
            hl = not hl
            continue
        for w in tok.split():
            out.append(f"<span class='w{' hl' if hl else ''}'{_fx('pop', t, 0.45)}>{_e(w)}</span>")
            t += step
    return " ".join(out), t


def _icon(name: str, start: float) -> str:
    svg = ICONS.get(name) or ICONS["bulb"]
    svg = re.sub(r"<(polyline|path|circle|ellipse|line|rect)(?![^>]*fill='currentColor')", r"<\1 pathLength='1' class='dr'", svg)
    return f"<svg class='ico' viewBox='0 0 300 300'{_fx('draw', start, 1.4)}>{svg}</svg>"


def _scene_hook(tl: _TL, syn, cat: str) -> None:
    t0 = tl.t
    val, lab = (getattr(syn, "hook_value", "") or "").strip(), (getattr(syn, "hook_label", "") or "").strip()
    num = ""
    if val:
        c = _count_attrs(val)
        if c:
            shown, a = c
            num = (f"<div class='big{' sm' if len(val) > 6 else ''}'{_fx('count', t0 + 0.25, 1.3, **a)}>{_e(val)}</div>"
                   f"<div class='lab'{_fx('fade', t0 + 0.6, 0.5)}>{_e(lab)}</div>")
        else:
            num = f"<div class='big'{_fx('pop', t0 + 0.25, 0.6)}>{_e(val)}</div><div class='lab'{_fx('fade', t0 + 0.6, 0.5)}>{_e(lab)}</div>"
    words, tend = _words((getattr(syn, "hook", "") or "").strip() or syn.seo.title, t0 + (1.1 if num else 0.3))
    body = f"<div class='hook-wrap'>{num}<div class='hook'>{words}</div></div>"
    tl.scene(body, max(3.6, tend - t0 + 1.4), "s-hook")


def _scene_point(tl: _TL, i: int, head: str, sub: str, icon: str) -> None:
    t0 = tl.t
    head_html, tend = _words(head, t0 + 0.45, 0.09)
    body = (f"<div class='idx'{_fx('pop', t0 + 0.1, 0.5)}>{i:02d}</div>"
            f"<div class='head'>{head_html}</div>"
            f"<div class='rule'{_fx('grow', t0 + 0.5, 0.5, w=100)}></div>"
            + (f"<div class='sub'{_fx('fade', tend + 0.1, 0.6)}>{_e(sub)}</div>" if sub else "")
            + _icon(icon, t0 + 0.3))
    tl.scene(body, max(3.4, tend - t0 + 2.0), "s-point")


def _scene_compare(tl: _TL, dg) -> None:
    t0 = tl.t
    d = dg.data or {}
    items = (d.get("items") or [])[:5]
    unit = d.get("unit", "")
    hi = d.get("highlight", 0)
    mx = max([float(x.get("value") or 0) for x in items] + [d.get("max") or 0]) or 1
    rows = []
    for k, it in enumerate(items):
        v = float(it.get("value") or 0)
        st = t0 + 0.6 + k * 0.35
        c = _count_attrs(f"{it.get('value')}{unit}") or ("", {})
        rows.append(f"<div class='row{' on' if k == hi else ''}'><div class='rl'><span>{_e(it.get('label'))}</span>"
                    f"<b{_fx('count', st, 1.0, **c[1]) if c[1] else ''}>{_e(it.get('value'))}{_e(unit)}</b></div>"
                    f"<div class='track'><div class='fill'{_fx('grow', st, 1.0, w=round(100 * v / mx, 1))}></div></div></div>")
    body = f"<div class='ttl'{_fx('fade', t0 + 0.1, 0.5)}>{_e(dg.title)}</div><div class='bars'>{''.join(rows)}</div>"
    tl.scene(body, 2.6 + 0.35 * len(items) + 0.8, "s-chart")


def _scene_trend(tl: _TL, dg) -> None:
    t0 = tl.t
    d = dg.data or {}
    pts = (d.get("points") or [])[:6]
    unit = d.get("unit", "")
    hi = d.get("highlight", len(pts) - 1)
    vals = [float(p.get("value") or 0) for p in pts]
    lo, mx = min(vals + [0]), max(vals) or 1
    base = lo * 0.85 if lo > 0 and (mx - lo) / mx < 0.5 else 0      # 차이가 작으면 바닥을 올려 변화가 보이게
    cols = []
    for k, p in enumerate(pts):
        st = t0 + 0.6 + k * 0.25
        hgt = 12 + 88 * (vals[k] - base) / ((mx - base) or 1)
        c = _count_attrs(f"{p.get('value')}") or ("", {})
        cols.append(f"<div class='col{' on' if k == hi else ''}'><b{_fx('count', st, 0.9, **c[1]) if c[1] else ''}>{_e(p.get('value'))}</b>"
                    f"<div class='ctrack'><div class='cfill'{_fx('growh', st, 0.9, h=round(hgt, 1))}></div></div>"
                    f"<span>{_e(p.get('label'))}</span></div>")
    body = (f"<div class='ttl'{_fx('fade', t0 + 0.1, 0.5)}>{_e(dg.title)}"
            f"{f' <small>({_e(unit)})</small>' if unit else ''}</div><div class='cols'>{''.join(cols)}</div>")
    tl.scene(body, 2.8 + 0.25 * len(pts) + 0.8, "s-chart")


def _scene_flow(tl: _TL, dg) -> None:
    t0 = tl.t
    d = dg.data or {}
    steps = d.get("steps") or []
    steps = [s.get("title") if isinstance(s, dict) else s for s in steps][:5]
    parts = []
    for k, s in enumerate(steps):
        st = t0 + 0.5 + k * 0.55
        if k:
            parts.append(f"<div class='arw'{_fx('fade', st - 0.15, 0.3)}>↓</div>")
        parts.append(f"<div class='step{' last' if k == len(steps) - 1 else ''}'{_fx('rise', st, 0.5)}>{_e(s)}</div>")
    body = f"<div class='ttl'{_fx('fade', t0 + 0.1, 0.5)}>{_e(dg.title)}</div><div class='flow'>{''.join(parts)}</div>"
    tl.scene(body, 1.4 + 0.55 * len(steps) + 1.3, "s-flow")


def _scene_outro(tl: _TL) -> None:
    t0 = tl.t
    body = (f"<div class='o1'><span{_fx('pop', t0 + 0.1, 0.5)}>30분 강연</span>"
            f"<span class='ar'{_fx('fade', t0 + 0.5, 0.4)}>→</span>"
            f"<span class='hl'{_fx('pop', t0 + 0.8, 0.5)}>5분 정리</span></div>"
            f"<div class='o2'{_fx('fade', t0 + 1.2, 0.5)}>전문은 프로필 링크에서</div>"
            f"<div class='o3'{_fx('pop', t0 + 1.6, 0.5)}>@jisikfill211204</div>"
            f"<div class='o4'{_fx('pulse', t0 + 2.0, 0.5)}>팔로우하고 매일 한 조각씩</div>")
    tl.scene(body, 3.6, "s-outro")


# ---------------------------------------------------------------- 페이지
CSS = """
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1080px;height:1920px;overflow:hidden;font-family:'NotoKR','Noto Sans KR','Malgun Gothic',sans-serif;word-break:keep-all}
.stage{position:absolute;inset:0;overflow:hidden;color:var(--fg);background:linear-gradient(160deg,var(--bg1) 0%,var(--bg2) 100%)}
.blob{position:absolute;border-radius:50%;filter:blur(90px);opacity:.55}
#b1{width:720px;height:720px;left:-200px;top:180px;background:var(--ac);opacity:.22}
#b2{width:620px;height:620px;right:-220px;top:900px;background:var(--fg);opacity:.08}
#b3{width:520px;height:520px;left:260px;bottom:-160px;background:var(--ac);opacity:.16}
.pat{position:absolute;inset:-200px;opacity:.55}
.p-dots{background-image:radial-gradient(var(--soft) 3px,transparent 3px);background-size:42px 42px}
.p-grid{background-image:linear-gradient(var(--soft) 2px,transparent 2px),linear-gradient(90deg,var(--soft) 2px,transparent 2px);background-size:70px 70px}
.p-stripes{background-image:repeating-linear-gradient(135deg,var(--soft) 0 16px,transparent 16px 64px)}
.p-rings,.p-plain{background:none}
.top{position:absolute;left:90px;right:150px;top:150px;display:flex;justify-content:space-between;align-items:center}
.brand{font-size:34px;font-weight:700;opacity:.9}
.chip{background:var(--chipbg);color:var(--chipfg);font-size:30px;font-weight:700;padding:10px 26px;border-radius:999px}
.prog{position:absolute;left:90px;right:150px;top:222px;height:8px;border-radius:4px;background:var(--soft)}
#prog{height:100%;width:0;border-radius:4px;background:var(--ac)}
.scene{position:absolute;left:90px;right:150px;top:320px;bottom:400px;display:flex;flex-direction:column;justify-content:center;opacity:0}
.w{display:inline-block;margin-right:.24em;opacity:0}
.hl{color:var(--ac)}
.big{font-size:200px;font-weight:900;line-height:1;letter-spacing:-6px;color:var(--ac);white-space:nowrap}.big.sm{font-size:150px;letter-spacing:-4px}
.lab{font-size:42px;opacity:.85;margin-top:14px}
.hook{font-size:96px;font-weight:800;line-height:1.24;letter-spacing:-2px;margin-top:60px}
.idx{font-size:160px;font-weight:900;color:var(--ac);line-height:1;letter-spacing:-5px}
.head{font-size:86px;font-weight:800;line-height:1.3;letter-spacing:-2px;margin-top:34px}
.rule{height:10px;width:0;max-width:140px;background:var(--ac);border-radius:5px;margin-top:44px}
.sub{font-size:46px;line-height:1.6;opacity:.9;margin-top:40px}
.ico{position:absolute;right:-40px;bottom:-30px;width:300px;height:300px;color:var(--ac);opacity:.35}
.ico .dr{stroke-dasharray:1;stroke-dashoffset:1}
.ttl{font-size:60px;font-weight:800;line-height:1.3;margin-bottom:60px}
.ttl small{font-size:38px;opacity:.7;font-weight:600}
.bars{display:flex;flex-direction:column;gap:48px}
.rl{display:flex;justify-content:space-between;align-items:baseline;font-size:40px;margin-bottom:16px;gap:20px}
.rl b{font-size:50px;font-weight:800;white-space:nowrap}
.track{height:64px;border-radius:32px;background:var(--soft);overflow:hidden}
.fill{height:100%;width:0;border-radius:32px;background:var(--fg);opacity:.55}
.row.on .fill{background:var(--ac);opacity:1}.row.on .rl b{color:var(--ac)}
.cols{display:flex;gap:26px;align-items:flex-end;height:900px}
.col{flex:1;display:flex;flex-direction:column;align-items:center;height:100%;justify-content:flex-end}
.col b{font-size:38px;font-weight:800;margin-bottom:14px;white-space:nowrap}
.ctrack{width:100%;height:640px;display:flex;align-items:flex-end}
.cfill{width:100%;height:0;border-radius:22px 22px 6px 6px;background:var(--fg);opacity:.5}
.col.on .cfill{background:var(--ac);opacity:1}.col.on b{color:var(--ac)}
.col span{font-size:30px;margin-top:16px;text-align:center;line-height:1.3;opacity:.85;min-height:80px}
.flow{display:flex;flex-direction:column;align-items:stretch}
.step{font-size:48px;font-weight:700;line-height:1.35;padding:30px 36px;border-radius:28px;background:var(--soft);opacity:0}
.step.last{background:var(--ac);color:var(--bg1)}
.arw{font-size:52px;color:var(--ac);text-align:center;line-height:1.1;margin:8px 0;opacity:0}
.o1{font-size:96px;font-weight:900;line-height:1.3;letter-spacing:-2px}
.o1 span{display:inline-block;opacity:0}.o1 .ar{margin:0 .2em}
.o2{font-size:58px;font-weight:700;margin-top:60px;opacity:0}
.o3{display:inline-block;align-self:flex-start;margin-top:40px;font-size:50px;font-weight:800;padding:18px 36px;border-radius:999px;background:var(--fg);color:var(--bg1);opacity:0}
.o4{display:inline-block;align-self:flex-start;margin-top:34px;font-size:46px;font-weight:800;padding:20px 40px;border-radius:24px;background:var(--ac);color:var(--bg1);opacity:0}
"""

JS = r"""
const T=__DUR__;
const oc=p=>1-Math.pow(1-p,3), ob=p=>{const c1=1.70158,c3=c1+1;return 1+c3*Math.pow(p-1,3)+c1*Math.pow(p-1,2)};
const cl=x=>Math.max(0,Math.min(1,x));
const scenes=[...document.querySelectorAll('.scene')], els=[...document.querySelectorAll('[data-fx]')];
const fmt=(v,d,comma)=>{let s=v.toFixed(d);if(comma){const [a,b]=s.split('.');s=a.replace(/\B(?=(\d{3})+(?!\d))/g,',')+(b?'.'+b:'')}return s};
window.__setT=function(t){
  document.getElementById('prog').style.width=(100*cl(t/T))+'%';
  [['b1',0,90],['b2',2,70],['b3',4,80]].forEach(([id,ph,a])=>{document.getElementById(id).style.transform=`translate(${Math.sin(t*0.45+ph)*a}px,${Math.cos(t*0.37+ph)*a}px)`});
  document.querySelector('.pat').style.transform=`translateY(${-(t*14)%70}px)`;
  for(const s of scenes){const a=+s.dataset.a,b=+s.dataset.b;const pi=cl((t-a)/0.35),po=cl((t-(b-0.3))/0.3);
    s.style.opacity=Math.min(pi,1-po);s.style.transform=`translateY(${(1-oc(pi))*60}px) scale(${1-0.05*po})`;s.style.visibility=(t<a-0.05||t>b+0.05)?'hidden':'visible'}
  for(const e of els){const d=e.dataset,p=cl((t-(+d.in))/(+d.dur));
    switch(d.fx){
      case 'pop':e.style.opacity=cl(p*2.5);e.style.transform=`scale(${0.5+0.5*ob(p)}) translateY(${(1-oc(p))*20}px)`;break;
      case 'fade':e.style.opacity=oc(p);e.style.transform=`translateY(${(1-oc(p))*30}px)`;break;
      case 'rise':e.style.opacity=oc(p);e.style.transform=`translateY(${(1-oc(p))*-60}px)`;break;
      case 'grow':e.style.width=(+d.w*oc(p))+'%';break;
      case 'growh':e.style.height=(+d.h*oc(p))+'%';break;
      case 'draw':e.querySelectorAll('.dr').forEach(x=>x.style.strokeDashoffset=1-oc(p));break;
      case 'count':e.textContent=(d.pre||'')+fmt(+d.num*oc(p),+d.dec,+d.comma)+(d.suf||'');e.style.opacity=cl(p*4);break;
      case 'pulse':e.style.opacity=oc(p);e.style.transform=`scale(${1+(t>+d.in+d.dur*1?0.035*Math.sin((t-(+d.in))*6):0)})`;break;
    }}
};
window.__setT(0);
"""


def build_html(summary, palette: str) -> tuple[str, float]:
    syn = summary.synthesis
    bg1, bg2, fg, ac, chipbg, chipfg, soft = PALETTES[palette]
    style = f"--bg1:{bg1};--bg2:{bg2};--fg:{fg};--ac:{ac};--chipbg:{chipbg};--chipfg:{chipfg};--soft:{soft};"
    pattern = _pick(summary.video_id, "pattern", PATTERNS)
    cat = (getattr(syn, "category", "") or "").strip() or "핵심 정리"
    from .social import _eum
    tks = syn.key_takeaways[:3]
    dgs = list(getattr(summary, "diagrams", []) or [])
    chart = next((d for d in dgs if d.type in ("compare", "trend") and 2 <= len((d.data or {}).get("items") or (d.data or {}).get("points") or []) <= 6), None)
    flow = next((d for d in dgs if d.type in ("flow", "steps") and 3 <= len((d.data or {}).get("steps") or []) <= 5), None)
    from .thumbs import CATEGORY_ICONS
    icons = [n for n in (CATEGORY_ICONS.get(cat) or ["bulb", "target", "book", "chat"]) if n in ICONS]

    def _similar(a: str, b: str) -> bool:
        wa, wb = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", a)), set(re.findall(r"[가-힣A-Za-z0-9]{2,}", b))
        return bool(wa) and len(wa & wb) / len(wa) >= 0.4

    def point(i, t):
        ss = _sents(t.text)
        head_src = (_sents(t.short) or ss or [""])[0]          # 제목은 첫 문장만
        head = _eum(head_src).rstrip(".")
        rest = [x for x in ss if not _similar(head_src, x) and not _similar(x, head_src)][:1]   # 제목과 겹치는 문장은 빼고
        sub = _eum(rest[0]) if rest else ""
        return head, sub if len(sub) <= 70 else sub[:68].rstrip() + "…"

    tl = _TL()
    _scene_hook(tl, syn, cat)
    seq = []
    if tks:
        seq.append(("p", 0))
    if chart:
        seq.append(("c", chart))
    if len(tks) > 1:
        seq.append(("p", 1))
    if flow:
        seq.append(("f", flow))
    if len(tks) > 2 and not (chart and flow):
        seq.append(("p", 2))
    n = 0
    for kind, x in seq:
        if kind == "p":
            n += 1
            h, s = point(n, tks[x])
            _scene_point(tl, n, h, s, _pick(summary.video_id + str(n), "ico", icons))
        elif kind == "c":
            (_scene_trend if x.type == "trend" else _scene_compare)(tl, x)
        else:
            _scene_flow(tl, x)
    _scene_outro(tl)
    dur = round(tl.t + 0.25, 2)
    page = (f"<!doctype html><html><head><meta charset='utf-8'><style>{_font_css()}{CSS}</style></head><body>"
            f"<div class='stage' style=\"{style}\"><div class='pat p-{pattern}'></div>"
            f"<div class='blob' id='b1'></div><div class='blob' id='b2'></div><div class='blob' id='b3'></div>"
            f"<div class='top'><span class='brand'>지식채우기</span><span class='chip'>{_e(cat)}</span></div>"
            f"<div class='prog'><div id='prog'></div></div>{''.join(tl.scenes)}</div>"
            f"<script>{JS.replace('__DUR__', str(dur))}</script></body></html>")
    return page, dur


def make_reel_video(summary, out_dir: Path) -> Path:
    import imageio_ffmpeg
    from .social import _palette
    out_dir.mkdir(parents=True, exist_ok=True)
    page, dur = build_html(summary, _palette(summary))
    (out_dir / "reel.html").write_text(page, encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="reel-"))
    try:
        job = tmp / "job.json"
        job.write_text(json.dumps({"html": page, "width": W, "height": H, "fps": FPS, "duration": dur,
                                   "outDir": str(tmp / "f"), "quality": 90}, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run(["node", str(FRAMES_JS), str(job)], capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"프레임 렌더 실패: {r.stderr[-1500:]}")
        music = make_music(summary.video_id, dur, tmp / "music.wav")
        out = out_dir / "reel.mp4"
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-framerate", str(FPS),
                        "-i", str(tmp / "f" / "f_%05d.jpg"), "-i", str(music),
                        "-vf", "scale=out_range=tv:out_color_matrix=bt709,format=yuv420p",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-profile:v", "high",
                        "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-shortest", "-movflags", "+faststart", str(out)],
                       check=True, capture_output=True, text=True, timeout=900)
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
