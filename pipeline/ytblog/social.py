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
.tb.v.reel{padding:200px 140px 430px 84px}
.reel .tb-hook.xl{font-size:104px}.reel .tb-hook.lg{font-size:88px}.reel .tb-hook.md{font-size:74px}
"""


def _cdn(url: str) -> str:
    """Meta 가 카페24 에서 이미지를 가끔 못 가져온다(해외 요청 거름). 워드프레스 공식 이미지 CDN(i0.wp.com)을 거치면 안정적."""
    return re.sub(r"^https?://", "https://i0.wp.com/", url) if url.startswith("http") else url


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
    for a, b in (("이에요", "임"), ("예요", "임"), ("해요", "함"), ("돼요", "됨"), ("있어요", "있음"), ("없어요", "없음"),
                 ("하세요", "하기"), ("세요", "기"),
                 ("려요", "림"), ("겨요", "김"), ("와요", "옴"), ("줘요", "줌"), ("봐요", "봄"), ("워요", "움"),
                 ("져요", "짐"), ("쳐요", "침"), ("여요", "임"), ("녀요", "님"), ("펴요", "핌")):
        s = re.sub(a + r"(?=[.!?]?(\s|$))", b, s)

    def _yo(m: re.Match) -> str:      # 받침 있는 말 + 아요/어요 → 음 (갔어요→갔음, 살아남아요→살아남음)
        syl = m.group(1)
        c = ord(syl) - 0xAC00
        return syl + "음" if 0 <= c < 11172 and c % 28 else m.group(0)
    s = re.sub(r"([가-힣])[아어]요(?=[.!?]?(\s|$))", _yo, s)
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


def threads_card(summary, out_path: Path, h: int = TH) -> Path:
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
    inner = (f"<style>{V_CSS}</style><div class='tb v{' reel' if h > TH else ''} p-{pattern}' style=\"{style}\">"
             f"<div class='tb-top'><span class='tb-cat'>{_e(cat)}</span><span class='tb-min'>5분 정리</span></div>"
             f"<div class='tb-body'>{stat}<div class='tb-hook {size}'>{_hl(hook)}</div>"
             f"<div class='tb-pts'>{pts}</div></div>"
             f"<div class='tb-foot'>전체 정리 · jisikfill.com</div></div>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    render_jobs([{"html": _wrap(inner, "tbcard"), "width": TW, "height": h, "out": str(out_path), "fixed": h > TH}])
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


def threads_reply(link: str, summary=None) -> str:
    """이어지는 답글(2/2): 핵심 몇 줄 + 블로그 링크. 링크는 본문이 아니라 여기에만 둔다."""
    more = ""
    if summary is not None:
        more = (getattr(summary.synthesis, "threads_more", "") or "").strip()
        if not more:
            more = "\n".join(f"· {p.rstrip('.')}" for p in _points(summary))
    head = f"{more}\n\n" if more else ""
    return f"{head}30분 강연 5분 정리해 둔 거 여기 있음\n{link}"[:500]


def threads_topic(summary) -> str:
    return TOPICS.get((getattr(summary.synthesis, "category", "") or "").strip(), "공부")


def post_to_threads(settings, summary, link: str, wp, state, out_dir: Path, dry_run: bool = False,
                    min_gap_h: float = 0) -> str:
    """스레드 게시: 글 위주(카드 이미지 없음, THREADS_IMAGE=1 이면 카드) + 끝 질문에 맞춘 투표 + 이어지는 답글(2/2)에 핵심·링크.
    min_gap_h 를 주면 직전 게시 후 그 시간이 안 지났을 때 올리지 않는다(연달아 올리면 뒷글이 노출을 못 받음)."""
    from . import threads as th
    vid = summary.video_id
    v = state.video(vid)
    if v.get("threads_id") and not dry_run:
        if not v.get("threads_reply_id") and v.get("threads_at", 0) > 1790780000:   # 링크 답글만 빠진 글(답글 방식 도입 뒤 게시분)
            return f"이미 게시됨 ({v['threads_id']}) · {_link_reply(th, v, state, link, wait=0, summary=summary)}"
        return f"이미 게시됨 ({v['threads_id']})"
    text = threads_text(summary)
    topic = threads_topic(summary)
    poll = [o for o in (getattr(summary.synthesis, "threads_poll", None) or []) if o.strip()]
    with_image = os.environ.get("THREADS_IMAGE", "0") == "1"
    if dry_run:
        return (f"[dry-run] 주제 {topic} · 투표 {poll or '-'} · 이미지 {'있음' if with_image else '없음'}\n{text}\n"
                f"  └ 2/2: {threads_reply(link, summary)}")
    if not os.environ.get("THREADS_ACCESS_TOKEN"):
        return "건너뜀: THREADS_ACCESS_TOKEN 없음"
    th.refresh_if_needed()
    if min_gap_h:
        gap = th.hours_since_last_post()
        if gap < min_gap_h:
            return f"보류: 직전 게시가 {gap:.1f}시간 전이라 {min_gap_h:.0f}시간 간격을 지키려고 다음으로 미룸"
    url = ""
    if with_image:
        card = threads_card(summary, out_dir / "images" / "threads.png")
        safe = re.sub(r"[^A-Za-z0-9]", "", vid).lower() or "v"
        _mid, up = wp.upload_media(card, f"{_plain(summary.synthesis.seo.title)} 요약 카드", "스레드 카드",
                                   filename=f"{safe}-threads-{time.strftime('%y%m%d%H%M')}.png")
        url = _cdn(up)
    tid = th.post(text, url, topic=topic, poll=None if url else poll)
    v["threads_id"] = tid; v["threads_at"] = time.time()
    state.save()
    return f"스레드 게시 완료 (id {tid}, 주제 {topic}{', 투표' if poll and not url else ''}) · {_link_reply(th, v, state, link, summary=summary)}"


def _link_reply(th, v: dict, state, link: str, wait: int = 8, summary=None) -> str:
    """내 글에 블로그 링크 답글. 게시 직후에는 원글이 아직 준비 안 돼 400 이 날 수 있어 간격을 두고 다시 시도한다."""
    err = ""
    for attempt in range(4):
        time.sleep(wait if attempt == 0 else 15 * attempt)
        try:
            v["threads_reply_id"] = th.post(threads_reply(link, summary), reply_to=v["threads_id"])
            state.save()
            return "이어지는 답글(핵심·링크) 달림"
        except Exception as e:  # noqa: BLE001
            err = str(e)[:100]
    return f"링크 답글 실패: {err}"


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
.ig.reel{padding:200px 140px 430px 84px}
.reel .ig-h{font-size:78px}.reel .ig-p{font-size:44px}
"""


def _ig_style(summary) -> tuple[str, str]:
    bg1, bg2, fg, ac, chipbg, chipfg, soft = PALETTES[_palette(summary)]
    return (f"--bg1:{bg1};--bg2:{bg2};--fg:{fg};--ac:{ac};--chipbg:{chipbg};--chipfg:{chipfg};--soft:{soft};",
            _pick(summary.video_id, "pattern", PATTERNS))


def ig_slides(summary, out_dir: Path, h: int = TH, prefix: str = "ig") -> list[Path]:
    """표지(스레드 카드와 같은 디자인) + 핵심 한 장씩 + 마지막 안내 장. 1080x1350 JPEG."""
    syn = summary.synthesis
    style, pattern = _ig_style(summary)
    cat = (getattr(syn, "category", "") or "").strip() or "핵심 정리"
    tks = syn.key_takeaways[:5]
    total = len(tks) + 2
    out_dir.mkdir(parents=True, exist_ok=True)
    reel = " reel" if h > TH else ""
    cover = out_dir / f"{prefix}-01.jpg"
    threads_card(summary, cover, h)       # 같은 HTML 을 JPEG 으로 한 번 더 렌더
    jobs, paths = [], [cover]
    for i, t in enumerate(tks, 1):
        head = _plain(t.short or t.text)
        body = _plain(t.text)
        sents = re.split(r"(?<=[.!?])\s+", body)
        hw = set(head.replace(".", "").split())
        if sents and hw and len(hw & set(sents[0].replace(".", "").split())) / len(hw) >= 0.6:
            sents = sents[1:]                 # 첫 문장이 제목과 거의 같으면 뺀다
        body = " ".join(sents).strip()
        inner = (f"<style>{IG_CSS}</style><div class='ig{reel} p-{pattern}' style=\"{style}\">"
                 f"<div class='ig-top'><span class='c'>{_e(cat)}</span><span class='n'>{i + 1} / {total}</span></div>"
                 f"<div class='ig-body'><div class='ig-no'>{i:02d}</div><div class='ig-h'>{_e(head)}</div>"
                 + (f"<div class='ig-p'>{_e(body)}</div>" if body else "") +
                 f"</div><div class='ig-foot'><span>지식채우기</span><span>{'' if reel else '밀어서 계속 →'}</span></div></div>")
        p = out_dir / f"{prefix}-{i + 1:02d}.jpg"
        jobs.append({"html": _wrap(inner, "tbcard"), "width": TW, "height": h, "out": str(p), "fixed": True}); paths.append(p)
    inner = (f"<style>{IG_CSS}</style><div class='ig ig-cta{reel} p-{pattern}' style=\"{style}\">"
             f"<div class='ig-top'><span class='c'>{_e(cat)}</span><span class='n'>{total} / {total}</span></div>"
             f"<div class='ig-body'><div class='ig-h'>30분 강연,<br>5분 정리 전문은<br>프로필 링크에서.</div>"
             f"<div class='ig-url'>jisikfill.com</div></div>"
             f"<div class='ig-foot'><span>저장해 두고 다시 보기</span><span>지식채우기</span></div></div>")
    p = out_dir / f"{prefix}-{total:02d}.jpg"
    jobs.append({"html": _wrap(inner, "tbcard"), "width": TW, "height": h, "out": str(p), "fixed": True}); paths.append(p)
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
        urls.append(_cdn(url))
    time.sleep(5)
    mid = ig.post_carousel(urls, cap)
    v["ig_id"] = mid; v["ig_at"] = time.time()
    state.save()
    return f"인스타 게시 완료 (id {mid}, {len(urls)}장)"


# ---------------------------------------------------------------- 인스타그램 릴스
RH = 1920   # 릴스 9:16


def make_reel(summary, out_dir: Path) -> Path:
    """9:16 슬라이드(표지·핵심 5장·안내)를 천천히 확대되며 넘어가는 17초 안팎 세로 영상으로. 무음 오디오 트랙 포함."""
    import subprocess
    import imageio_ffmpeg
    slides = ig_slides(summary, out_dir, h=RH, prefix="rl")
    durs = [2.2] + [3.0] * (len(slides) - 2) + [2.4]
    fade, fps = 0.4, 30
    args = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error"]
    for img, d in zip(slides, durs):
        args += ["-loop", "1", "-framerate", str(fps), "-t", f"{d}", "-i", str(img)]
    total = sum(durs) - fade * (len(durs) - 1)
    args += ["-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=44100:cl=stereo"]
    parts = []
    for i, d in enumerate(durs):   # 아주 느린 확대(최대 4%)로 움직임을 준다
        frames = int(d * fps)
        parts.append(f"[{i}:v]scale=1188:2112,zoompan=z='min(1+on*0.04/{frames},1.04)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                     f":d=1:s=1080x1920:fps={fps},setsar=1,format=yuv420p[s{i}]")
    prev, off = "[s0]", 0.0
    for i in range(1, len(durs)):
        off += durs[i - 1] - fade
        parts.append(f"{prev}[s{i}]xfade=transition=fade:duration={fade}:offset={off:.2f}[x{i}]")
        prev = f"[x{i}]"
    parts.append(f"{prev}scale=out_range=tv:out_color_matrix=bt709,format=yuv420p[vout]")   # JPEG 전범위 → 영상 표준 범위
    prev = "[vout]"
    out = out_dir / "reel.mp4"
    args += ["-filter_complex", ";".join(parts), "-map", prev, "-map", f"{len(durs)}:a",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(fps),
             "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart", str(out)]
    subprocess.run(args, check=True, capture_output=True, text=True, timeout=600)
    return out


def post_reel_to_instagram(settings, summary, link: str, wp, state, out_dir: Path, dry_run: bool = False) -> str:
    """릴스(팔로워가 아닌 사람에게도 추천되는 유일한 형식) 게시. state 의 ig_reel_id 로 중복 방지."""
    from . import instagram as ig
    vid = summary.video_id
    v = state.video(vid)
    if v.get("ig_reel_id") and not dry_run:
        return f"이미 게시됨 ({v['ig_reel_id']})"
    try:   # 모션그래픽 영상 + 배경음악(reel.py). 실패하면 예전 슬라이드 영상으로
        from .reel import make_reel_video
        video = make_reel_video(summary, out_dir / "images" / "reel")
    except Exception as e:  # noqa: BLE001
        print(f"  모션 영상 실패 → 슬라이드 영상으로 대체: {str(e)[:160]}")
        video = make_reel(summary, out_dir / "images" / "reel")
    cap = ig_caption(summary)
    if dry_run:
        return f"[dry-run] 릴스 {video} ({video.stat().st_size // 1024}KB)\n{cap}"
    if not os.environ.get("IG_ACCESS_TOKEN"):
        return "건너뜀: IG_ACCESS_TOKEN 없음"
    ig.refresh_if_needed()
    mid = ig.post_reel(video, cap)
    v["ig_reel_id"] = mid; v["ig_reel_at"] = time.time()
    state.save()
    return f"인스타 릴스 게시 완료 (id {mid})"


def post_instagram_auto(settings, summary, link: str, wp, state, out_dir: Path, dry_run: bool = False) -> str:
    """IG_FORMAT(.env) 에 따라 릴스(기본) 또는 캐러셀."""
    if os.environ.get("IG_FORMAT", "reels").lower().startswith("car"):
        return post_to_instagram(settings, summary, link, wp, state, out_dir, dry_run)
    return post_reel_to_instagram(settings, summary, link, wp, state, out_dir, dry_run)


# ---------------------------------------------------------------- 지식 한 조각(스레드)
def nugget_candidates(settings, state, n: int = 8) -> list[dict]:
    """아직 단독 글로 안 쓴 핵심 포인트 후보. 최근에 조각을 낸 글은 뒤로, 글마다 돌아가며."""
    import json as _json
    used = state.data.setdefault("nuggets", [])           # [{"vid","idx","id","at"}]
    used_keys = {(u["vid"], u["idx"]) for u in used}
    recent = [u["vid"] for u in sorted(used, key=lambda u: u.get("at", 0))[-4:]]
    rows = []
    for vid, v in state.data["videos"].items():
        if v.get("status") != "published" or not v.get("wp_link"):
            continue
        sp = settings.out_dir / vid / "summary.json"
        if not sp.exists():
            continue
        d = _json.loads(sp.read_text(encoding="utf-8"))
        syn = d["synthesis"]
        for i, t in enumerate(syn.get("key_takeaways", [])):
            if (vid, i) in used_keys:
                continue
            rows.append({"vid": vid, "idx": i, "title": syn["seo"]["title"], "category": syn.get("category", ""),
                         "point": _plain(t.get("text", "")), "link": v["wp_link"], "recent": vid in recent})
    rows.sort(key=lambda r: (r["recent"], sum(1 for u in used if u["vid"] == r["vid"]), r["idx"]))
    return rows[:n]


def post_nugget(settings, state, vid: str, idx: int, text: str, poll: list[str] | None = None, min_gap_h: float = 3,
                dry_run: bool = False) -> str:
    """조각 글 게시(글만 + 선택 투표) → 이어지는 답글에 블로그 링크. 같은 포인트 중복 방지."""
    from . import threads as th
    v = state.video(vid)
    used = state.data.setdefault("nuggets", [])
    if any(u["vid"] == vid and u["idx"] == idx for u in used):
        return "이미 쓴 포인트"
    cat = ""
    sp = settings.out_dir / vid / "summary.json"
    if sp.exists():
        import json as _json
        cat = _json.loads(sp.read_text(encoding="utf-8"))["synthesis"].get("category", "")
    topic = TOPICS.get(cat, "공부")
    if dry_run:
        return f"[dry-run] 주제 {topic} · 투표 {poll or '-'}\n{text}\n  └ {v.get('wp_link')}"
    th.refresh_if_needed()
    gap = th.hours_since_last_post()
    if gap < min_gap_h:
        return f"보류: 직전 게시가 {gap:.1f}시간 전"
    tid = th.post(text.strip()[:500], topic=topic, poll=poll)
    used.append({"vid": vid, "idx": idx, "id": tid, "at": time.time()})
    state.save()
    reply = ""
    for attempt in range(4):
        time.sleep(8 if attempt == 0 else 15 * attempt)
        try:
            th.post(f"이 내용 나온 강연 정리\n{v.get('wp_link')}", reply_to=tid)
            reply = "링크 답글 달림"; break
        except Exception as e:  # noqa: BLE001
            reply = f"링크 답글 실패: {str(e)[:80]}"
    return f"조각 게시 완료 (id {tid}, 주제 {topic}{', 투표' if poll else ''}) · {reply}"

