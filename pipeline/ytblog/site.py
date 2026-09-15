"""사이트 운영 도구: 홈 배너 갱신, 주간 리포트(다빈보드 소통 게시).

  python -m ytblog banner ["헤드라인"] ["부제"]   최근 글을 반영한 배너 이미지 생성 → 홈 템플릿 교체
  python -m ytblog report                          지난 7일 발행·댓글·대기열 요약을 다빈보드 소통에 게시(+배너 갱신)
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import PIPELINE_DIR, Settings
from .images import _font_css
from .wordpress import WordPressClient

KST = timezone(timedelta(hours=9))
BANNER_HEAD = "30분짜리 강연,<br/><em>5분</em>이면 다 읽어요"
BANNER_SUB_DEFAULT = "유튜브 강연의 핵심만 쉬운 말과 도식으로 정리한 공부 노트예요."


def _e(s) -> str:
    return html.escape(str(s or ""))


def recent_posts(wp: WordPressClient, days: int = 7) -> list[dict]:
    after = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()
    return wp._get("posts", after=after, status="publish", per_page=50, orderby="date", order="desc", context="edit")


def category_names(wp: WordPressClient, posts: list[dict]) -> list[str]:
    ids = []
    for p in posts:
        for c in p.get("categories", []):
            if c not in ids:
                ids.append(c)
    names = []
    for cid in ids[:4]:
        try:
            names.append(wp._get(f"categories/{cid}")["name"])
        except Exception:  # noqa: BLE001
            pass
    return names


ILLUS = """
<svg width="820" height="520" viewBox="0 0 820 520" xmlns="http://www.w3.org/2000/svg" style="position:absolute;right:90px;top:60px">
  <defs><filter id="sh" x="-10%" y="-10%" width="130%" height="130%"><feDropShadow dx="0" dy="18" stdDeviation="18" flood-color="#1f2937" flood-opacity=".16"/></filter></defs>
  <g filter="url(#sh)"><rect x="20" y="90" width="330" height="210" rx="22" fill="#1f2937"/>
    <polygon points="165,160 165,230 230,195" fill="#fbbf24"/>
    <rect x="44" y="262" width="282" height="8" rx="4" fill="#ffffff" opacity=".25"/><rect x="44" y="262" width="110" height="8" rx="4" fill="#fbbf24"/></g>
  <path d="M372 195 C 420 195, 420 300, 470 300" stroke="#b45309" stroke-width="10" fill="none" stroke-linecap="round"/>
  <polygon points="462,282 492,300 462,318" fill="#b45309"/>
  <g filter="url(#sh)"><rect x="500" y="120" width="300" height="360" rx="26" fill="#ffffff" stroke="#e7e5e4" stroke-width="3"/>
    <rect x="500" y="120" width="300" height="70" rx="26" fill="#fbbf24"/><rect x="500" y="160" width="300" height="30" fill="#fbbf24"/>
    <rect x="530" y="220" width="180" height="16" rx="8" fill="#1f2937"/>
    <rect x="530" y="262" width="240" height="12" rx="6" fill="#d6d3d1"/><rect x="530" y="292" width="210" height="12" rx="6" fill="#d6d3d1"/><rect x="530" y="322" width="230" height="12" rx="6" fill="#d6d3d1"/>
    <rect x="530" y="366" width="240" height="70" rx="14" fill="#f5f3ee"/><rect x="546" y="382" width="60" height="38" rx="8" fill="#1f2937"/><rect x="616" y="382" width="60" height="38" rx="8" fill="#b45309"/><rect x="686" y="382" width="60" height="38" rx="8" fill="#d6d3d1"/></g>
</svg>"""


def render_banner(head: str, sub: str, out: Path, mobile: bool = False) -> Path:
    """밝은 크림 배경 + 일러스트(영상 → 노트). head 는 HTML 허용(<br/>, <em>). mobile=True 면 1080x1080 세로 배치."""
    css = _font_css() + """
*{margin:0;padding:0;box-sizing:border-box}
body{width:1920px;height:640px;font-family:'NotoKR','Noto Sans KR','Malgun Gothic',sans-serif;position:relative;overflow:hidden;background:#f5f3ee}
.dots{position:absolute;inset:0;background-image:radial-gradient(#d6d3d1 2px, transparent 2px);background-size:34px 34px;opacity:.55}
.t{position:absolute;left:140px;top:150px;color:#1f2937;max-width:900px}
.k{display:inline-block;background:#1f2937;color:#fbbf24;font-size:26px;font-weight:700;padding:8px 20px;border-radius:999px;letter-spacing:.5px}
.h{font-size:82px;font-weight:700;line-height:1.18;margin-top:26px;letter-spacing:-1.5px;word-break:keep-all}
.h em{font-style:normal;color:#b45309}
.p{font-size:31px;line-height:1.5;margin-top:26px;color:#52606d;word-break:keep-all}
"""
    W, H = (1080, 1080) if mobile else (1920, 640)
    if mobile:
        css += """
body{width:1080px;height:1080px}
.t{left:80px;top:90px;max-width:920px}
.k{font-size:30px;padding:10px 24px}
.h{font-size:96px;line-height:1.16;margin-top:30px}
.p{font-size:38px;margin-top:28px}
svg{right:auto !important;left:130px !important;top:560px !important;transform:scale(1.0)}
"""
    page = (f"<html><head><meta charset='utf-8'><style>{css}</style></head><body><div class='dots'></div>{ILLUS}"
            f"<div class='t'><span class='k'>지식채우기</span><div class='h'>{head}</div><div class='p'>{_e(sub)}</div></div></body></html>")
    jp = out.parent / f"banner-jobs-{'m' if mobile else 'd'}.json"
    jp.write_text(json.dumps([{"html": page, "width": W, "height": H, "out": str(out)}], ensure_ascii=False), encoding="utf-8")
    subprocess.run(["node", str(PIPELINE_DIR / "render" / "render_card.js"), str(jp)], check=True, capture_output=True, text=True, timeout=300)
    return out


BANNER_CSS = ".yt-banner .yt-banner-d img{width:100%;height:auto;border-radius:18px}.yt-banner .yt-banner-m{display:none}@media (max-width:781px){.yt-banner .yt-banner-d{display:none}.yt-banner .yt-banner-m{display:block}}.yt-banner figure{margin:0}"


def _banner_group(d_id: int, d_url: str, m_id: int, m_url: str) -> str:
    def img(mid, url, cls):
        return (f'<!-- wp:image {{"id":{mid},"sizeSlug":"full","linkDestination":"none","align":"wide","className":"{cls}"}} -->\n'
                f'<figure class="wp-block-image alignwide size-full {cls}"><img src="{url}" alt="지식채우기 배너" class="wp-image-{mid}"/></figure>\n<!-- /wp:image -->')
    return ('<!-- wp:group {"align":"full","className":"yt-banner","style":{"spacing":{"margin":{"top":"0"}}},"layout":{"type":"constrained"}} -->\n'
            '<div class="wp-block-group alignfull yt-banner" style="margin-top:0">\n' + img(d_id, d_url, "yt-banner-d") + "\n" + img(m_id, m_url, "yt-banner-m") +
            "\n</div>\n<!-- /wp:group -->")


def ensure_banner_css(wp: WordPressClient) -> None:
    th = wp._get("themes", status="active")[0]
    href = th.get("_links", {}).get("wp:user-global-styles", [{}])[0].get("href", "")
    gid = href.rstrip("/").rsplit("/", 1)[-1]
    if not gid:
        return
    g = wp._get(f"global-styles/{gid}", context="edit"); styles = g.get("styles") or {}
    css = styles.get("css") or ""
    if ".yt-banner" not in css:
        styles["css"] = (css + "\n" + BANNER_CSS).strip()
        wp._post(f"global-styles/{gid}", styles=styles)


def update_banner(settings: Settings, head: str = "", sub: str = "") -> str:
    wp = WordPressClient(settings.wp_url, settings.wp_user, settings.wp_app_password)
    if not sub:
        posts = recent_posts(wp, 7)
        cats = category_names(wp, posts)
        n = len(posts)
        sub = (f"이번 주 새 글 {n}편 · 최근 주제: {', '.join(cats)}" if n and cats else BANNER_SUB_DEFAULT)
    head = head or BANNER_HEAD
    tmp = Path(tempfile.mkdtemp()); stamp = int(time.time())
    d_path = render_banner(head, sub, tmp / f"home-banner-{stamp}.png")
    m_path = render_banner(head, sub, tmp / f"home-banner-m-{stamp}.png", mobile=True)
    d_id, d_url = wp.upload_media(d_path, "지식채우기 배너", "홈 배너")
    m_id, m_url = wp.upload_media(m_path, "지식채우기 배너(모바일)", "홈 배너 모바일")
    t = wp._get("templates/twentytwentyfive//home", context="edit"); c = t["content"]["raw"]
    group = _banner_group(d_id, d_url, m_id, m_url)
    old_ids = [int(x) for x in re.findall(r'wp-image-(\d+)', c)]
    if 'className":"yt-banner"' in c:
        c2 = re.sub(r'<!-- wp:group \{"align":"full","className":"yt-banner".*?<!-- /wp:group -->', lambda _m: group, c, count=1, flags=re.S)
    else:
        c2 = re.sub(r'<!-- wp:cover .*?<!-- /wp:cover -->', lambda _m: group, c, count=1, flags=re.S)
    if c2 == c:
        raise RuntimeError("홈 템플릿에서 배너 블록을 찾지 못했습니다")
    wp._post("templates/twentytwentyfive//home", content=c2)
    ensure_banner_css(wp)
    for mid in set(old_ids) - {d_id, m_id}:
        try:
            wp.s.delete(f"{wp.api}/media/{mid}", params={"force": "true"}, timeout=60)
        except Exception:  # noqa: BLE001
            pass
    return sub


def weekly_report(settings: Settings, board_js: Path, post_to_board: bool = True) -> str:
    wp = WordPressClient(settings.wp_url, settings.wp_user, settings.wp_app_password)
    posts = recent_posts(wp, 7)
    comments = wp._get("comments", after=(datetime.now(timezone.utc) - timedelta(days=7)).replace(microsecond=0).isoformat(),
                       per_page=100, status="approve")
    pending = 0
    try:
        from . import queue as q
        pending = len(q.pending())
    except Exception:  # noqa: BLE001
        pass
    now = datetime.now(KST)
    lines = [f"📝 지식채우기 주간 리포트 ({(now - timedelta(days=7)).strftime('%m/%d')}~{now.strftime('%m/%d')})",
             f"· 발행 {len(posts)}편 · 댓글 {len(comments)}개 · 대기열 {pending}편"]
    for p in posts[:7]:
        lines.append(f"  - {html.unescape(p['title']['rendered'])}  {p['link']}")
    if not posts:
        lines.append("  - 지난주 발행 없음. 대기열에 링크를 올려 주세요.")
    try:
        sub = update_banner(settings)
        lines.append(f"· 홈 배너 갱신: {sub}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"· 홈 배너 갱신 실패: {str(e)[:80]}")
    text = "\n".join(lines)
    if post_to_board and board_js.exists():
        doc = {"who": "cl", "text": text, "t": now.strftime("%H:%M"), "ts": int(time.time() * 1000)}
        subprocess.run(["node", str(board_js), "add", "chat", json.dumps(doc, ensure_ascii=False)], check=True,
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    return text
