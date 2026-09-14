"""생성 이미지(카드) 제작. 영상 캡처는 쓰지 않는다.

- hero: 대표 이미지(1200x630, OG 겸용)
- takeaways: 핵심 포인트 카드
- flow: 구간 흐름도(목차 다이어그램)
- section: 구간별 카드(내용이 충분한 구간만)
- numbers: 수치 카드(수치가 3개 이상일 때)
모든 카드에 출처(채널명)를 표기한다.
"""
from __future__ import annotations

import html
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import PIPELINE_DIR
from .discover import VideoMeta
from .schema import Summary

RENDER_JS = PIPELINE_DIR / "render" / "render_card.js"
FONT_DIR = PIPELINE_DIR / "render" / "fonts"


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
     color:#1f2933;background:#fff;-webkit-font-smoothing:antialiased}
.card{width:100%;height:100%;padding:56px 64px 110px;display:flex;flex-direction:column;position:relative;overflow:hidden}
.card.dark{background:linear-gradient(135deg,#1e3a8a 0%,#2563eb 60%,#3b82f6 100%);color:#fff}
.card.light{background:#f4f6fb}
.kicker{font-size:22px;font-weight:700;letter-spacing:.5px;opacity:.85}
.title{font-size:52px;font-weight:700;line-height:1.25;margin-top:18px;word-break:keep-all;overflow-wrap:anywhere}
.sub{font-size:28px;line-height:1.45;margin-top:22px;opacity:.92;word-break:keep-all}
.source{position:absolute;left:64px;right:64px;bottom:36px;font-size:20px;opacity:.8;display:flex;justify-content:space-between}
.pill{display:inline-block;background:rgba(255,255,255,.18);border-radius:999px;padding:6px 16px;font-size:20px;font-weight:700}
.card.light .pill{background:#e8f0fe;color:#1d4ed8}
ol.tk{list-style:none;margin-top:28px;display:flex;flex-direction:column;gap:18px}
ol.tk li{display:flex;gap:18px;align-items:flex-start;font-size:30px;line-height:1.4;word-break:keep-all}
ol.tk .n{flex:0 0 48px;height:48px;border-radius:12px;background:#2563eb;color:#fff;font-weight:700;display:flex;align-items:center;justify-content:center;font-size:24px}
ol.tk .ts{font-size:20px;color:#2563eb;font-weight:700;white-space:nowrap;margin-left:auto;padding-left:12px}
.flow{display:flex;gap:16px;margin-top:34px;align-items:stretch}
.step{flex:1;background:#fff;border:2px solid #dbe3ee;border-radius:16px;padding:20px 18px;position:relative;min-width:0}
.step .n{font-size:18px;font-weight:700;color:#2563eb}
.step .t{font-size:22px;font-weight:700;margin-top:6px;line-height:1.3;word-break:keep-all}
.step .r{font-size:17px;color:#7a8794;margin-top:10px}
.step:not(:last-child)::after{content:'›';position:absolute;right:-15px;top:40%;font-size:34px;color:#9aa6ae}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:30px}
.num{background:#fff;border-radius:16px;padding:24px;border:2px solid #dbe3ee}
.num .v{font-size:44px;font-weight:700;color:#1d4ed8;line-height:1.1;word-break:break-all}
.num .l{font-size:20px;color:#52606d;margin-top:10px;line-height:1.35;word-break:keep-all}
.num .ts{font-size:16px;color:#9aa6ae;margin-top:6px}
.sec .body{font-size:26px;line-height:1.5;margin-top:22px;word-break:keep-all}
.sec ul{margin-top:18px;padding-left:28px;font-size:23px;line-height:1.5;word-break:keep-all}
"""


def _wrap(inner: str, cls: str) -> str:
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{_font_css()}{BASE_CSS}</style></head><body><div class='card {cls}'>{inner}</div></body></html>"


def _e(s: str) -> str:
    return html.escape(s or "")


def _source_line(meta: VideoMeta, blog_name: str) -> str:
    return f"<div class='source'><span>출처: {_e(meta.channel_title)} · 유튜브</span><span>{_e(blog_name)}</span></div>"


def _clip(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def build_cards(summary: Summary, meta: VideoMeta, blog_name: str, out_dir: Path) -> list[CardImage]:
    out_dir.mkdir(parents=True, exist_ok=True)
    syn = summary.synthesis
    jobs: list[dict] = []
    cards: list[CardImage] = []

    def add(kind: str, inner: str, cls: str, w: int, h: int, alt: str, caption: str, idx: int = -1):
        path = out_dir / f"{kind}{'' if idx < 0 else f'-{idx+1}'}.png"
        jobs.append({"html": _wrap(inner, cls), "width": w, "height": h, "out": str(path)})
        cards.append(CardImage(kind=kind, path=path, alt=alt, caption=caption, section_index=idx))

    # 1) 대표 이미지
    add("hero",
        f"<div class='kicker'>영상 요약</div><div class='title'>{_e(_clip(syn.seo.title or meta.title, 60))}</div>"
        f"<div class='sub'>{_e(_clip(syn.one_liner, 90))}</div>" + _source_line(meta, blog_name),
        "dark", 1200, 630, alt=f"{meta.title} 요약 대표 이미지", caption="")

    # 2) 핵심 포인트
    if syn.key_takeaways:
        items = "".join(
            f"<li><span class='n'>{i+1}</span><span>{_e(_clip(t.text, 70))}</span><span class='ts'>{_e(t.ts)}</span></li>"
            for i, t in enumerate(syn.key_takeaways[:5]))
        h = 360 + 100 * min(5, len(syn.key_takeaways))
        add("takeaways",
            f"<span class='pill'>핵심 포인트 {len(syn.key_takeaways[:5])}가지</span><div class='title' style='font-size:40px'>{_e(_clip(meta.title, 50))}</div>"
            f"<ol class='tk'>{items}</ol>" + _source_line(meta, blog_name),
            "light", 1200, h, alt="핵심 포인트 카드", caption="영상의 핵심 포인트를 한눈에 정리한 카드입니다.")

    # 3) 구간 흐름도
    secs = summary.sections[:6]
    if len(secs) >= 3:
        steps = "".join(
            f"<div class='step'><div class='n'>{i+1}</div><div class='t'>{_e(_clip(s.title, 26))}</div><div class='r'>{_e(s.start)} ~ {_e(s.end)}</div></div>"
            for i, s in enumerate(secs))
        add("flow",
            f"<span class='pill'>영상 흐름</span><div class='title' style='font-size:36px'>이 영상은 이렇게 진행됩니다</div><div class='flow'>{steps}</div>"
            + _source_line(meta, blog_name),
            "light", 1200, 480, alt="영상 구간 흐름도", caption="영상이 다루는 주제의 순서를 도식화했습니다.")

    # 4) 구간 카드: details 가 3개 이상인 구간만, 최대 4장
    made = 0
    for i, s in enumerate(summary.sections):
        if made >= 4 or len(s.details) < 3:
            continue
        bullets = "".join(f"<li>{_e(_clip(d, 80))}</li>" for d in s.details[:3])
        add("section",
            f"<span class='pill'>{_e(s.start)} ~ {_e(s.end)}</span><div class='title' style='font-size:40px'>{_e(_clip(s.title, 40))}</div>"
            f"<div class='sec'><div class='body'>{_e(_clip(s.image_caption or s.summary, 60))}</div><ul>{bullets}</ul></div>"
            + _source_line(meta, blog_name),
            "light", 1200, 640, alt=f"{s.title} 요약 카드", caption=s.image_caption or s.title, idx=i)
        made += 1

    # 5) 수치 카드
    nums = [n for s in summary.sections for n in s.numbers][:6]
    if len(nums) >= 3:
        cells = "".join(
            f"<div class='num'><div class='v'>{_e(_clip(n.value, 12))}</div><div class='l'>{_e(_clip(n.label, 40))}</div><div class='ts'>{_e(n.ts)}</div></div>"
            for n in nums)
        rows = (len(nums) + 2) // 3
        add("numbers",
            f"<span class='pill'>영상 속 숫자</span><div class='title' style='font-size:36px'>숫자로 보는 핵심</div><div class='grid'>{cells}</div>"
            + _source_line(meta, blog_name),
            "light", 1200, 300 + 190 * rows, alt="영상에 언급된 수치 카드", caption="영상에 언급된 주요 수치입니다.")

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
