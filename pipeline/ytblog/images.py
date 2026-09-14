"""생성 이미지(카드) 제작. 영상 캡처는 쓰지 않는다.

카드는 블로그 본문 폭(Twenty Twenty-Five 기준 645px)의 2배인 1290px 로 그려서, 본문에 축소돼 들어가도
글자가 본문 글자(약 18px)와 비슷하거나 크게 보이도록 한다. 디자인은 흰 바탕·굵은 제목·짧은 항목으로 단순하게.

- hero: 대표 이미지(16:9, OG 겸용)
- takeaways: 핵심 포인트
- section: 구간마다 한 장(핵심 한 줄 + 짧은 항목 3개)
- numbers: 숫자로 기억하기
- glossary: 핵심 용어(한 장에 3개)
- questions: 자기 점검 질문
모든 카드 하단에 출처(채널명)와 블로그 이름을 표기한다.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import PIPELINE_DIR
from .discover import VideoMeta
from .schema import Summary

RENDER_JS = PIPELINE_DIR / "render" / "render_card.js"
FONT_DIR = PIPELINE_DIR / "render" / "fonts"
W = 1290                      # 본문 645px 의 2배
H_STD = 726                   # 16:9
TS_RE = re.compile(r"\s*\[(?:\d{1,2}:)?\d{1,2}:\d{2}\]")


@dataclass
class CardImage:
    kind: str
    path: Path
    alt: str
    caption: str
    section_index: int = -1


def _font_css() -> str:
    faces = []
    for name, weight in (("NotoSansKR-Regular.otf", 400), ("NotoSansKR-Bold.otf", 700)):
        p = FONT_DIR / name
        if p.exists():
            faces.append(f"@font-face{{font-family:'NotoKR';src:url('file://{p}');font-weight:{weight};}}")
    return "\n".join(faces)


BASE_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;width:100%}
body{font-family:'NotoKR','Noto Sans KR','Apple SD Gothic Neo','Malgun Gothic','Noto Sans CJK KR',sans-serif;
     color:#1b2430;background:#fff;-webkit-font-smoothing:antialiased}
.card{width:100%;height:100%;padding:72px 80px 120px;display:flex;flex-direction:column;position:relative;overflow:hidden;background:#fff}
.card.dark{background:#1d4ed8;color:#fff}
.label{font-size:30px;font-weight:700;color:#2563eb;letter-spacing:.3px}
.card.dark .label{color:rgba(255,255,255,.85)}
.title{font-size:62px;font-weight:700;line-height:1.28;margin-top:22px;word-break:keep-all;overflow-wrap:anywhere}
.title.sm{font-size:54px}
.sub{font-size:38px;line-height:1.5;margin-top:30px;color:#3d4a5c;word-break:keep-all}
.card.dark .sub{color:rgba(255,255,255,.9)}
.source{position:absolute;left:80px;right:80px;bottom:44px;font-size:26px;color:#8a95a3;display:flex;justify-content:space-between}
.card.dark .source{color:rgba(255,255,255,.75)}
ul.pts{list-style:none;margin-top:34px;display:flex;flex-direction:column;gap:22px}
ul.pts li{position:relative;padding-left:44px;font-size:40px;line-height:1.42;word-break:keep-all;overflow-wrap:anywhere}
ul.pts li::before{content:'';position:absolute;left:0;top:22px;width:18px;height:18px;border-radius:50%;background:#2563eb}
ol.tk{list-style:none;margin-top:30px;display:flex;flex-direction:column;gap:22px}
ol.tk li{display:flex;gap:22px;align-items:flex-start;font-size:38px;line-height:1.42;word-break:keep-all;overflow-wrap:anywhere}
ol.tk .n{flex:0 0 56px;height:56px;border-radius:14px;background:#2563eb;color:#fff;font-weight:700;display:flex;align-items:center;justify-content:center;font-size:30px;margin-top:2px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:22px;margin-top:34px}
.grid.two{grid-template-columns:repeat(2,1fr)}
.num{background:#f5f7fb;border-radius:20px;padding:30px 30px 26px}
.num .v{font-size:52px;font-weight:700;color:#1d4ed8;line-height:1.15;word-break:keep-all;overflow-wrap:anywhere}
.num .v.m{font-size:40px}
.num .v.s{font-size:32px;line-height:1.3}
.num .l{font-size:28px;color:#52606d;margin-top:14px;line-height:1.35;word-break:keep-all}
.gl{margin-top:34px;display:flex;flex-direction:column;gap:40px}
.gl .t{font-size:42px;font-weight:700;color:#1d4ed8;line-height:1.3}
.gl .d{font-size:34px;line-height:1.45;color:#3d4a5c;margin-top:6px;word-break:keep-all}
"""


def _wrap(inner: str, cls: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{_font_css()}{BASE_CSS}</style></head>"
            f"<body><div class='card {cls}'>{inner}</div></body></html>")


def _e(s: str) -> str:
    return html.escape(s or "")


def _source_line(meta: VideoMeta, blog_name: str) -> str:
    return f"<div class='source'><span>출처: {_e(meta.channel_title)} · 유튜브</span><span>{_e(blog_name)}</span></div>"


def _clip(s: str, n: int) -> str:
    s = TS_RE.sub("", s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _first_sentence(s: str, n: int) -> str:
    s = TS_RE.sub("", s or "").strip()
    m = re.match(r"(.+?[.!?다])(\s|$)", s)
    return _clip(m.group(1) if m else s, n)


def build_cards(summary: Summary, meta: VideoMeta, blog_name: str, out_dir: Path) -> list[CardImage]:
    out_dir.mkdir(parents=True, exist_ok=True)
    syn = summary.synthesis
    jobs: list[dict] = []
    cards: list[CardImage] = []
    src = _source_line(meta, blog_name)

    def add(kind: str, inner: str, cls: str, h: int, alt: str, caption: str, idx: int = -1, suffix: str = ""):
        name = kind + (f"-{idx + 1}" if idx >= 0 else "") + suffix
        path = out_dir / f"{name}.png"
        jobs.append({"html": _wrap(inner + src, cls), "width": W, "height": h, "out": str(path)})
        cards.append(CardImage(kind=kind, path=path, alt=alt, caption=caption, section_index=idx))

    # 1) 대표 이미지
    add("hero",
        f"<div class='label'>핵심 정리</div><div class='title'>{_e(_clip(syn.seo.title or meta.title, 52))}</div>"
        f"<div class='sub'>{_e(_clip(syn.one_liner, 105))}</div>",
        "dark", H_STD, alt=f"{meta.title} 핵심 정리 대표 이미지", caption="")

    # 2) 핵심 포인트
    tk = syn.key_takeaways[:5]
    if tk:
        items = "".join(f"<li><span class='n'>{i + 1}</span><span>{_e(_clip(t.text, 84))}</span></li>" for i, t in enumerate(tk))
        add("takeaways",
            f"<div class='label'>핵심 포인트 {len(tk)}가지</div><ol class='tk'>{items}</ol>",
            "light", max(H_STD, 230 + 150 * len(tk)), alt="핵심 포인트 정리 카드", caption="이 영상의 핵심 포인트")

    # 3) 구간 카드: 구간마다 한 장 (핵심 한 줄 + 짧은 항목 최대 3개)
    for i, s in enumerate(summary.sections):
        pts = [d for d in s.details if d.strip()][:3]
        if not pts:
            continue
        bullets = "".join(f"<li>{_e(_clip(d, 66))}</li>" for d in pts)
        head = _clip(s.image_caption or s.title, 40)
        add("section",
            f"<div class='label'>{_e(_clip(s.title, 34))}</div><div class='title sm'>{_e(head)}</div><ul class='pts'>{bullets}</ul>",
            "light", 780, alt=f"{s.title} 핵심 카드", caption=head, idx=i)

    # 4) 숫자로 기억하기: 구간을 돌아가며 최대 6개
    nums = []
    pools = [list(s.numbers) for s in summary.sections if s.numbers]
    while len(nums) < 6 and any(pools):
        for p in pools:
            if p and len(nums) < 6:
                nums.append(p.pop(0))
    if len(nums) >= 3:
        def vcls(v: str) -> str:
            return "" if len(v) <= 8 else ("m" if len(v) <= 14 else "s")
        cells = "".join(f"<div class='num'><div class='v {vcls(n.value)}'>{_e(_clip(n.value, 28))}</div>"
                        f"<div class='l'>{_e(_clip(n.label, 30))}</div></div>" for n in nums)
        rows = (len(nums) + 2) // 3
        add("numbers",
            f"<div class='label'>숫자로 기억하기</div><div class='grid'>{cells}</div>",
            "light", max(H_STD, 280 + 220 * rows), alt="영상의 주요 수치 카드", caption="숫자로 기억하는 핵심")

    # 5) 핵심 용어: 한 장에 3개
    gl = syn.glossary
    for k in range(0, len(gl), 2):
        chunk = gl[k:k + 2]
        rows_html = "".join(f"<div><div class='t'>{_e(_clip(g.term, 24))}</div><div class='d'>{_e(_clip(g.definition, 90))}</div></div>" for g in chunk)
        add("glossary",
            f"<div class='label'>핵심 용어</div><div class='gl'>{rows_html}</div>",
            "light", H_STD, alt="핵심 용어 카드", caption="알아두면 좋은 용어", suffix=f"-{k // 2 + 1}")

    # 6) 자기 점검 질문
    qs = getattr(syn, "study_questions", []) or []
    if qs:
        items = "".join(f"<li><span class='n'>Q{i + 1}</span><span>{_e(_clip(q, 60))}</span></li>" for i, q in enumerate(qs[:5]))
        add("questions",
            f"<div class='label'>스스로 점검해 보기</div><ol class='tk'>{items}</ol>",
            "light", max(H_STD, 230 + 150 * min(5, len(qs))), alt="자기 점검 질문 카드", caption="영상을 이해했는지 확인하는 질문")

    render_jobs(jobs)
    return cards


def render_jobs(jobs: list[dict]) -> None:
    if not jobs:
        return
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False)
        job_path = f.name
    try:
        subprocess.run(["node", str(RENDER_JS), job_path], check=True, capture_output=True, text=True, timeout=300)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"카드 렌더링 실패: {e.stderr[-2000:]}") from e
    finally:
        Path(job_path).unlink(missing_ok=True)
