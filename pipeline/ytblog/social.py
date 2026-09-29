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


def _eum(s: str) -> str:
    """존댓말 문장 끝을 음슴체로 (threads_points 가 없을 때의 대체용). 했습니다→했음, 됩니다→됨, 해요→함."""
    def ending(m: re.Match) -> str:
        word = m.group(1)
        if word.endswith("습니다"):
            return word[:-3] + "음"
        syl = word[-3]                      # '~ㅂ니다' 의 앞 글자: 받침 ㅂ → ㅁ (됩→됨, 합→함, 입→임)
        code = ord(syl) - 0xAC00
        if 0 <= code < 11172 and code % 28 == 17:
            return word[:-3] + chr(ord(syl) - 1)  # 받침 ㅂ(17) → ㅁ(16)
        return word
    s = re.sub(r"(\S+니다)(?=[.!?]?(\s|$))", ending, s)
    s = re.sub(r"(았|었|였|했|됐|왔|웠)어요(?=[.!?]?(\s|$))", r"\1음", s)
    for a, b in (("이에요", "임"), ("예요", "임"), ("해요", "함"), ("돼요", "됨"), ("있어요", "있음"), ("없어요", "없음")):
        s = re.sub(a + r"(?=[.!?]?(\s|$))", b, s)
    return s


def _points(summary, n: int = 3) -> list[str]:
    syn = summary.synthesis
    src = [p for p in (getattr(syn, "threads_points", None) or []) if p.strip()]
    if not src:
        src = [_eum(_plain(t.short or t.text)) for t in syn.key_takeaways]
    out = []
    for s in src[:n]:
        s = _plain(s)
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


TOPICS = {"경제·투자": "재테크", "건강·의학": "건강", "역사·인문": "역사", "과학·기술": "과학",
          "심리·자기계발": "자기계발", "사회·문화": "사회"}


def threads_text(summary, link: str = "") -> str:
    """스레드 본문(음슴체, 링크 없음). 링크는 알고리즘이 외부 링크 글을 덜 퍼뜨려서 본인 답글로 따로 단다."""
    syn = summary.synthesis
    body = (getattr(syn, "threads_text", "") or "").strip()
    if not body:
        head = _plain(getattr(syn, "hook", "") or "") or _plain(syn.seo.title)
        body = f"{head}\n\n" + "\n".join(f"· {p}" for p in _points(summary))
    return body[:500].rstrip()


def threads_reply(link: str) -> str:
    return f"강연 30분짜리 5분 정리해 둔 거 여기 있음\n{link}"


def threads_topic(summary) -> str:
    return TOPICS.get((getattr(summary.synthesis, "category", "") or "").strip(), "공부")


def post_to_threads(settings, summary, link: str, wp, state, out_dir: Path, dry_run: bool = False) -> str:
    """카드 렌더 → WP 미디어 업로드 → 스레드 게시. 게시한 스레드 id 를 state 에 남긴다(중복 방지)."""
    from . import threads as th
    vid = summary.video_id
    v = state.video(vid)
    if v.get("threads_id") and not dry_run:
        return f"이미 게시됨 ({v['threads_id']})"
    card = threads_card(summary, out_dir / "images" / "threads.png")
    text = threads_text(summary)
    topic = threads_topic(summary)
    if dry_run:
        return f"[dry-run] 카드 {card} · 주제 {topic}\n{text}\n  └ 답글: {threads_reply(link)}"
    if not os.environ.get("THREADS_ACCESS_TOKEN"):
        return "건너뜀: THREADS_ACCESS_TOKEN 없음"
    th.refresh_if_needed()
    safe = re.sub(r"[^A-Za-z0-9]", "", vid).lower() or "v"
    _mid, url = wp.upload_media(card, f"{_plain(summary.synthesis.seo.title)} 요약 카드", "스레드 카드",
                                filename=f"{safe}-threads-{time.strftime('%y%m%d%H%M')}.png")
    tid = th.post(text, url, topic=topic)
    v["threads_id"] = tid; v["threads_at"] = time.time()
    state.save()
    try:
        time.sleep(5)
        v["threads_reply_id"] = th.post(threads_reply(link), reply_to=tid)
        state.save()
        return f"스레드 게시 완료 (id {tid}, 링크 답글 달림, 주제 {topic})"
    except Exception as e:  # noqa: BLE001
        return f"스레드 게시 완료 (id {tid}) · 링크 답글 실패: {str(e)[:100]}"


# ---------------------------------------------------------------- 인스타그램 캐러셀
IG_CSS = """
.ig{position:absolute;inset:0;padding:84px 84px 76px;display:flex;flex-direction:column;color:var(--fg);
    background:linear-gradient(135deg,var(--bg1) 0%,var(--bg2) 100%);overflow:hidden}
.ig::before{content:'';position:absolute;inset:0;z-index:0;opacity:.9}
.ig.p-dots::before{background-image:radial-gradient(var(--soft) 3px,transparent 3px);background-size:38px 38px}
.ig.p-grid::before{background-image:linear-gradient(var(--soft) 2px,transparent 2px),linear-gradient(90deg,var(--soft) 2px,transparent 2px);background-size:64px 64px}
.ig.p-stripes::before{background-image:repeating-linear-gradient(135deg,var(--soft) 0 18px,transparent 18px 60px)}
.ig.p-rings::before{background-image:radial-gradient(circle at 88% 18%,transparent 0 150px,var(--soft) 150px 170px,transparent 170px 260px,var(--soft) 260px 280px,transparent 280px)}
.ig>*{position:relative;z-index:1}
.ig-top{display:flex;justify-content:space-between;align-items:center;font-size:30px;font-weight:700}
.ig-top .c{background:var(--chipbg);color:var(--chipfg);padding:10px 24px;border-radius:999px}
.ig-top .n{opacity:.7}
.ig-body{flex:1;display:flex;flex-direction:column;justify-content:center}
.ig-no{font-size:150px;font-weight:700;color:var(--ac);line-height:1;letter-spacing:-4px}
.ig-h{font-size:72px;font-weight:700;line-height:1.28;letter-spacing:-2px;margin-top:40px;word-break:keep-all}
.ig-p{font-size:40px;line-height:1.6;margin-top:44px;opacity:.9;word-break:keep-all}
.ig-foot{font-size:30px;font-weight:700;opacity:.75;display:flex;justify-content:space-between}
.ig-cta .ig-h{font-size:84px}
.ig-cta .ig-url{margin-top:48px;display:inline-block;background:var(--ac);color:var(--bg1);font-size:52px;font-weight:700;padding:20px 40px;border-radius:24px;align-self:flex-start}
"""


def _ig_style(summary) -> tuple[str, str]:
    bg1, bg2, fg, ac, chipbg, chipfg, soft = PALETTES[_palette(summary)]
    return (f"--bg1:{bg1};--bg2:{bg2};--fg:{fg};--ac:{ac};--chipbg:{chipbg};--chipfg:{chipfg};--soft:{soft};",
            _pick(summary.video_id, "pattern", PATTERNS))


def ig_slides(summary, out_dir: Path) -> list[Path]:
    """표지(스레드 카드와 같은 디자인) + 핵심 한 장씩 + 마지막 안내 장. 1080x1350 JPEG."""
    syn = summary.synthesis
    style, pattern = _ig_style(summary)
    cat = (getattr(syn, "category", "") or "").strip() or "핵심 정리"
    tks = syn.key_takeaways[:5]
    total = len(tks) + 2
    out_dir.mkdir(parents=True, exist_ok=True)
    cover = out_dir / "ig-01.jpg"
    threads_card(summary, cover)          # 같은 HTML 을 JPEG 으로 한 번 더 렌더
    jobs, paths = [], [cover]
    for i, t in enumerate(tks, 1):
        head = _plain(t.short or t.text)
        body = _plain(t.text)
        sents = re.split(r"(?<=[.!?])\s+", body)
        hw = set(head.replace(".", "").split())
        if sents and hw and len(hw & set(sents[0].replace(".", "").split())) / len(hw) >= 0.6:
            sents = sents[1:]                 # 첫 문장이 제목과 거의 같으면 뺀다
        body = " ".join(sents).strip()
        inner = (f"<style>{IG_CSS}</style><div class='ig p-{pattern}' style=\"{style}\">"
                 f"<div class='ig-top'><span class='c'>{_e(cat)}</span><span class='n'>{i + 1} / {total}</span></div>"
                 f"<div class='ig-body'><div class='ig-no'>{i:02d}</div><div class='ig-h'>{_e(head)}</div>"
                 + (f"<div class='ig-p'>{_e(body)}</div>" if body else "") +
                 f"</div><div class='ig-foot'><span>지식채우기</span><span>밀어서 계속 →</span></div></div>")
        p = out_dir / f"ig-{i + 1:02d}.jpg"
        jobs.append({"html": _wrap(inner, "tbcard"), "width": TW, "height": TH, "out": str(p), "fixed": True}); paths.append(p)
    inner = (f"<style>{IG_CSS}</style><div class='ig ig-cta p-{pattern}' style=\"{style}\">"
             f"<div class='ig-top'><span class='c'>{_e(cat)}</span><span class='n'>{total} / {total}</span></div>"
             f"<div class='ig-body'><div class='ig-h'>30분 강연,<br>5분 정리 전문은<br>프로필 링크에서.</div>"
             f"<div class='ig-url'>jisikfill.com</div></div>"
             f"<div class='ig-foot'><span>저장해 두고 다시 보기</span><span>지식채우기</span></div></div>")
    p = out_dir / f"ig-{total:02d}.jpg"
    jobs.append({"html": _wrap(inner, "tbcard"), "width": TW, "height": TH, "out": str(p), "fixed": True}); paths.append(p)
    render_jobs(jobs)
    return paths


def ig_caption(summary) -> str:
    syn = summary.synthesis
    tags = ["#" + re.sub(r"[^0-9A-Za-z가-힣]", "", t) for t in (syn.seo.tags or [])[:5]]
    tags = [t for t in dict.fromkeys(tags + ["#지식채우기", "#공부기록"]) if len(t) > 1]
    return f"{threads_text(summary)}\n\n5분 정리 전문은 프로필 링크 → jisikfill.com\n\n{' '.join(tags)}"


def post_to_instagram(settings, summary, link: str, wp, state, out_dir: Path, dry_run: bool = False) -> str:
    """캐러셀 렌더 → WP 미디어 업로드 → 인스타 게시. state 의 ig_id 로 중복 방지."""
    from . import instagram as ig
    vid = summary.video_id
    v = state.video(vid)
    if v.get("ig_id") and not dry_run:
        return f"이미 게시됨 ({v['ig_id']})"
    slides = ig_slides(summary, out_dir / "images" / "ig")
    cap = ig_caption(summary)
    if dry_run:
        return f"[dry-run] 슬라이드 {len(slides)}장 {slides[0].parent}\n{cap}"
    if not os.environ.get("IG_ACCESS_TOKEN"):
        return "건너뜀: IG_ACCESS_TOKEN 없음"
    ig.refresh_if_needed()
    safe = re.sub(r"[^A-Za-z0-9]", "", vid).lower() or "v"
    stamp = time.strftime("%y%m%d%H%M")
    urls = []
    for s in slides:
        _mid, url = wp.upload_media(s, f"{_plain(summary.synthesis.seo.title)} {s.stem}", "인스타 카드",
                                    filename=f"{safe}-{s.stem}-{stamp}.jpg")
        urls.append(url)
    time.sleep(5)
    mid = ig.post_carousel(urls, cap)
    v["ig_id"] = mid; v["ig_at"] = time.time()
    state.save()
    return f"인스타 게시 완료 (id {mid}, {len(urls)}장)"
