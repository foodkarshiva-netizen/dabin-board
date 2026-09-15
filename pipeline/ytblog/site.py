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
BANNER_HEAD = "30분짜리 강연을<br/>5분 글로 채워 드려요"
BANNER_SUB_DEFAULT = "환율, 건강, 역사, 과학. 유튜브 강연의 핵심만 쉽게 정리한 공부 노트예요."


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


def render_banner(head: str, sub: str, out: Path) -> Path:
    css = _font_css() + """
*{margin:0;padding:0;box-sizing:border-box}
body{width:1920px;height:640px;font-family:'NotoKR','Noto Sans KR','Malgun Gothic',sans-serif;background:#1f2937;position:relative;overflow:hidden}
.c1{position:absolute;right:-120px;top:-160px;width:620px;height:620px;border-radius:50%;background:#fbbf24;opacity:.9}
.c2{position:absolute;right:380px;top:300px;width:420px;height:420px;border-radius:50%;background:#f5f3ee;opacity:.12}
.c3{position:absolute;right:60px;top:430px;width:260px;height:260px;border-radius:50%;background:#b45309;opacity:.85}
.t{position:absolute;left:140px;top:170px;color:#fff;max-width:1200px}
.k{font-size:30px;font-weight:700;color:#fbbf24;letter-spacing:.5px}
.h{font-size:78px;font-weight:700;line-height:1.2;margin-top:18px;letter-spacing:-1px}
.p{font-size:32px;line-height:1.5;margin-top:26px;color:rgba(255,255,255,.85);word-break:keep-all}
"""
    page = (f"<html><head><meta charset='utf-8'><style>{css}</style></head><body>"
            f"<div class='c1'></div><div class='c2'></div><div class='c3'></div>"
            f"<div class='t'><div class='k'>지식채우기</div><div class='h'>{head}</div><div class='p'>{_e(sub)}</div></div></body></html>")
    jp = out.parent / "banner-jobs.json"
    jp.write_text(json.dumps([{"html": page, "width": 1920, "height": 640, "out": str(out)}], ensure_ascii=False), encoding="utf-8")
    subprocess.run(["node", str(PIPELINE_DIR / "render" / "render_card.js"), str(jp)], check=True, capture_output=True, text=True, timeout=300)
    return out


def update_banner(settings: Settings, head: str = "", sub: str = "") -> str:
    wp = WordPressClient(settings.wp_url, settings.wp_user, settings.wp_app_password)
    if not sub:
        posts = recent_posts(wp, 7)
        cats = category_names(wp, posts)
        n = len(posts)
        sub = (f"이번 주 새 글 {n}편. 최근 주제는 {', '.join(cats)}이에요." if n and cats else BANNER_SUB_DEFAULT)
    head = head or BANNER_HEAD
    out = Path(tempfile.mkdtemp()) / f"home-banner-{int(time.time())}.png"
    render_banner(head, sub, out)
    mid, url = wp.upload_media(out, "지식채우기 배너", "홈 배너")
    t = wp._get("templates/twentytwentyfive//home", context="edit"); c = t["content"]["raw"]
    old = re.search(r'<!-- wp:cover \{"url":"([^"]+)","id":(\d+)', c)
    c2 = re.sub(r'<!-- wp:cover \{"url":"[^"]+","id":\d+', f'<!-- wp:cover {{"url":"{url}","id":{mid}', c, count=1)
    c2 = re.sub(r'class="wp-block-cover__image-background wp-image-\d+" alt="[^"]*" src="[^"]+"',
                f'class="wp-block-cover__image-background wp-image-{mid}" alt="지식채우기 배너" src="{url}"', c2, count=1)
    if c2 == c:
        raise RuntimeError("홈 템플릿에서 배너(cover) 블록을 찾지 못했습니다")
    wp._post("templates/twentytwentyfive//home", content=c2)
    if old:
        try:
            wp.s.delete(f"{wp.api}/media/{old.group(2)}", params={"force": "true"}, timeout=60)
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
