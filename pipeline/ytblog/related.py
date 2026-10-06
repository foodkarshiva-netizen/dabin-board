"""'같이 읽으면 좋은 글' 자동 연결.

공개된 글마다 같은 카테고리·태그가 겹치는 글 3편을 골라 글 끝(출처 고지 앞)에 썸네일 카드로 붙인다.
새 글이 나오면 기존 글의 추천도 바뀔 수 있으므로 전체를 다시 계산하되, 내용이 달라진 글만 갱신한다.

  python -m ytblog related [--dry-run]
"""
from __future__ import annotations

import html
import re

from .render import H2
from .wordpress import WordPressClient

START, END = "<!-- ytblog:related:start -->", "<!-- ytblog:related:end -->"
BLOCK_RE = re.compile(r"\n*" + re.escape(START) + r".*?" + re.escape(END) + r"\n*", re.S)
SEP = "<!-- wp:separator -->"
REL_CSS = ("/* yt-rel */.yt-rel{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:8px}"
           ".yt-rel a{display:block;text-decoration:none;color:inherit;font-weight:700;font-size:.95em;line-height:1.45}"
           ".yt-rel img{width:100%;height:auto;border-radius:12px;display:block;margin-bottom:10px}"
           ".yt-rel a:hover span{text-decoration:underline}"
           "@media (max-width:781px){.yt-rel{grid-template-columns:1fr;gap:14px}"
           ".yt-rel a{display:grid;grid-template-columns:42% 1fr;gap:14px;align-items:center}.yt-rel img{margin-bottom:0}}")


def ensure_css(wp: WordPressClient) -> None:
    themes = wp._get("themes", status="active")
    href = themes[0].get("_links", {}).get("wp:user-global-styles", [{}])[0].get("href", "")
    gid = href.rstrip("/").split("/")[-1]
    if not gid:
        return
    g = wp._get(f"global-styles/{gid}", context="edit"); styles = g.get("styles") or {}
    css = styles.get("css") or ""
    if "/* yt-rel */" not in css:
        styles["css"] = (css + "\n" + REL_CSS).strip()
        wp._post(f"global-styles/{gid}", styles=styles)


def _thumbs(wp: WordPressClient, ids: list[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    ids = [i for i in dict.fromkeys(ids) if i]
    for k in range(0, len(ids), 50):
        for m in wp._get("media", include=",".join(map(str, ids[k:k + 50])), per_page=50):
            sizes = (m.get("media_details") or {}).get("sizes") or {}
            out[m["id"]] = (sizes.get("medium_large") or sizes.get("large") or {}).get("source_url") or m.get("source_url", "")
    return out


def _pick(me: dict, posts: list[dict], n: int = 3) -> list[dict]:
    def score(p: dict) -> tuple:
        s = 3 * len(set(me["categories"]) & set(p["categories"])) + 2 * len(set(me["tags"]) & set(p["tags"]))
        return (s, p["date"])
    return sorted((p for p in posts if p["id"] != me["id"]), key=score, reverse=True)[:n]


def build_block(picks: list[dict], thumbs: dict[int, str]) -> str:
    cards = []
    for p in picks:
        title = html.escape(html.unescape(p["title"]["rendered"]), quote=True)
        img = thumbs.get(p.get("featured_media") or 0, "")
        pic = f'<img src="{img}" alt="{title}" loading="lazy"/>' if img else ""
        cards.append(f'<a href="{p["link"]}">{pic}<span>{title}</span></a>')
    return (f"{START}\n{H2('같이 읽으면 좋은 글')}\n<!-- wp:html -->\n<div class=\"yt-rel\">{''.join(cards)}</div>\n"
            f"<!-- /wp:html -->\n{END}")


def apply_block(raw: str, block: str) -> str:
    """기존 추천 블록을 빼고, 마지막 구분선(출처 고지) 앞에 새 블록을 넣는다."""
    raw = BLOCK_RE.sub("\n\n", raw)
    i = raw.rfind(SEP)
    if i < 0:
        return raw.rstrip() + "\n\n" + block
    return raw[:i].rstrip() + "\n\n" + block + "\n\n" + raw[i:]


def refresh(settings, wp: WordPressClient | None = None, dry_run: bool = False) -> str:
    wp = wp or WordPressClient(settings.wp_url, settings.wp_user, settings.wp_app_password)
    posts = wp._get("posts", per_page=100, status="publish", context="edit",
                    _fields="id,date,link,title,categories,tags,featured_media,content")
    try:      # 청약 분석 글은 자동 공고 페이지라 '같이 읽으면 좋은 글' 대상에서 뺀다
        cy = {c["id"] for c in wp._get("categories", search="청약 분석") if c["name"] == "청약 분석"}
        posts = [p for p in posts if not (set(p.get("categories", [])) & cy)]
    except Exception:  # noqa: BLE001
        pass
    if len(posts) < 2:
        return "글이 2편 미만이라 건너뜀"
    if not dry_run:
        ensure_css(wp)
    thumbs = _thumbs(wp, [p.get("featured_media") or 0 for p in posts])
    changed = 0
    for p in posts:
        raw = p["content"]["raw"]
        new = apply_block(raw, build_block(_pick(p, posts), thumbs))
        if new.strip() != raw.strip():
            changed += 1
            if not dry_run:
                wp.update_post(p["id"], content=new)
    return f"추천 글 갱신 {changed}편 / 전체 {len(posts)}편" + (" (dry-run)" if dry_run else "")
