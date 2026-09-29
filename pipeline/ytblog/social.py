"""유입용 SNS 게시: 글이 공개되면 스레드에 세로 카드 1장 + 짧은 글 + 블로그 링크를 올린다.

카드: 1080x1350(4:5). 썸네일과 같은 팔레트·무늬로 후킹 문구·숫자·핵심 3가지를 담는다.
글: 후킹 한 줄 + 핵심 3줄 + 전체 정리 링크 (500자 이내).

  python -m ytblog threads-post VIDEO_ID [--dry-run]   이미 공개된 글을 스레드에 올리기(수동·재시도용)
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .config import PIPELINE_DIR
from .images import _wrap, render_jobs
from .thumbs import PALETTES, PATTERNS, _e, _hl, _pick

TW, TH = 1080, 1350

V_CSS = """
.tb.v{padding:84px 80px 72px}
.v .tb-body{flex:1;display:flex;flex-direction:column;justify-content:center}
.v .tb-sv{font-size:150px;font-weight:700;color:var(--ac);line-height:1;letter-spacing:-5px;word-break:keep-all}
.v .tb-sv.sm{font-size:110px}
.v .tb-sl{font-size:34px;opacity:.85;margin-top:10px}
.v .tb-hook{margin-top:34px}
.v .tb-hook.xl{font-size:96px}.v .tb-hook.lg{font-size:82px}.v .tb-hook.md{font-size:68px}
.v .tb-pts{margin-top:56px;background:var(--soft);border-radius:30px;padding:38px 42px;display:flex;flex-direction:column;gap:24px}
.v .tb-pt{display:flex;gap:20px;font-size:35px;line-height:1.42;word-break:keep-all}
.v .tb-pt b{flex:0 0 48px;height:48px;border-radius:12px;background:var(--ac);color:var(--bg1);display:flex;align-items:center;justify-content:center;font-size:28px;margin-top:2px}
.v .tb-foot{font-size:32px;opacity:.8;font-weight:700}
"""


def _plain(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("**", "").replace("*", "")).strip()


def _points(summary, n: int = 3) -> list[str]:
    out = []
    for t in summary.synthesis.key_takeaways[:n]:
        s = _plain(t.short or t.text)
        out.append(s if len(s) <= 62 else s[:60].rstrip() + "…")
    return out


def _palette(summary) -> str:
    try:
        hist = json.loads((PIPELINE_DIR / "data" / "thumb_history.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        hist = {}
    pal = hist.get(summary.video_id) or getattr(summary.synthesis, "thumb_palette", "") or ""
    return pal if pal in PALETTES else _pick(summary.video_id, "pal", list(PALETTES))


def threads_card(summary, out_path: Path) -> Path:
    syn = summary.synthesis
    hook = (getattr(syn, "hook", "") or "").strip() or _plain(syn.seo.title)
    val = (getattr(syn, "hook_value", "") or "").strip()
    lab = (getattr(syn, "hook_label", "") or "").strip()
    cat = (getattr(syn, "category", "") or "").strip() or "핵심 정리"
    bg1, bg2, fg, ac, chipbg, chipfg, soft = PALETTES[_palette(summary)]
    style = f"--bg1:{bg1};--bg2:{bg2};--fg:{fg};--ac:{ac};--chipbg:{chipbg};--chipfg:{chipfg};--soft:{soft};"
    pattern = _pick(summary.video_id, "pattern", PATTERNS)
    n = len(hook.replace("*", ""))
    size = "xl" if n <= 16 else ("lg" if n <= 26 else "md")
    stat = (f"<div class='tb-sv{' sm' if len(val) > 6 else ''}'>{_e(val)}</div><div class='tb-sl'>{_e(lab)}</div>" if val else "")
    pts = "".join(f"<div class='tb-pt'><b>{i}</b><span>{_e(p)}</span></div>" for i, p in enumerate(_points(summary), 1))
    inner = (f"<style>{V_CSS}</style><div class='tb v p-{pattern}' style=\"{style}\">"
             f"<div class='tb-top'><span class='tb-cat'>{_e(cat)}</span><span class='tb-min'>5분 정리</span></div>"
             f"<div class='tb-body'>{stat}<div class='tb-hook {size}'>{_hl(hook)}</div>"
             f"<div class='tb-pts'>{pts}</div></div>"
             f"<div class='tb-foot'>전체 정리 · jisikfill.com</div></div>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    render_jobs([{"html": _wrap(inner, "tbcard"), "width": TW, "height": TH, "out": str(out_path)}])
    return out_path


def threads_text(summary, link: str) -> str:
    syn = summary.synthesis
    head = _plain(getattr(syn, "hook", "") or "") or _plain(syn.seo.title)
    if not head.endswith((".", "?", "!")):
        head += "."
    body = "\n".join(f"· {p}" for p in _points(summary))
    tail = f"\n\n30분 강연을 5분 글로 정리했어요.\n{link}"
    return (f"{head}\n\n{body}"[: 500 - len(tail)] + tail)


def post_to_threads(settings, summary, link: str, wp, state, out_dir: Path, dry_run: bool = False) -> str:
    """카드 렌더 → WP 미디어 업로드 → 스레드 게시. 게시한 스레드 id 를 state 에 남긴다(중복 방지)."""
    from . import threads as th
    vid = summary.video_id
    v = state.video(vid)
    if v.get("threads_id") and not dry_run:
        return f"이미 게시됨 ({v['threads_id']})"
    card = threads_card(summary, out_dir / "images" / "threads.png")
    text = threads_text(summary, link)
    if dry_run:
        return f"[dry-run] 카드 {card}\n{text}"
    if not os.environ.get("THREADS_ACCESS_TOKEN"):
        return "건너뜀: THREADS_ACCESS_TOKEN 없음"
    th.refresh_if_needed()
    safe = re.sub(r"[^A-Za-z0-9]", "", vid).lower() or "v"
    _mid, url = wp.upload_media(card, f"{_plain(summary.synthesis.seo.title)} 요약 카드", "스레드 카드",
                                filename=f"{safe}-threads-{time.strftime('%y%m%d%H%M')}.png")
    tid = th.post(text, url)
    v["threads_id"] = tid; v["threads_at"] = time.time()
    state.save()
    return f"스레드 게시 완료 (id {tid})"
