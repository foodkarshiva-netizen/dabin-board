"""채널 핸들 → channel_id 해석, RSS로 새 영상 감지, 영상 메타데이터 수집.

YouTube Data API 키 없이 동작한다(RSS + 공개 페이지 파싱). 키가 있으면 더 안정적이지만 필수는 아니다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import feedparser
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
CHAPTER_RE = re.compile(r"^\s*\(?((?:\d{1,2}:)?\d{1,2}:\d{2})\)?\s*[-–—:.]?\s*(.+?)\s*$")


@dataclass
class VideoMeta:
    video_id: str
    title: str
    channel_id: str
    channel_title: str
    published: str = ""
    description: str = ""
    duration_sec: int = 0
    keywords: list[str] = field(default_factory=list)
    thumbnail_url: str = ""
    chapters: list[dict] = field(default_factory=list)  # [{"start": sec, "title": str}]

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def _session(proxy_url: str = "") -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"})
    if proxy_url:
        s.proxies.update({"http": proxy_url, "https": proxy_url})
    return s


def _consent_cookies(s: requests.Session) -> None:
    # EU 동의 페이지 우회용 쿠키
    s.cookies.set("CONSENT", "YES+cb", domain=".youtube.com")
    s.cookies.set("SOCS", "CAI", domain=".youtube.com")


def resolve_channel(handle_or_url: str, proxy_url: str = "") -> tuple[str, str]:
    """'@지식인사이드' / 채널 URL / 'UC...' → (channel_id, channel_title)."""
    if handle_or_url.startswith("UC") and len(handle_or_url) == 24:
        return handle_or_url, ""
    url = handle_or_url
    if not url.startswith("http"):
        url = "https://www.youtube.com/" + url.lstrip("/")
    s = _session(proxy_url)
    _consent_cookies(s)
    r = s.get(url, timeout=30)
    r.raise_for_status()
    html = r.text
    m = re.search(r'"channelId":"(UC[\w-]{22})"', html) or \
        re.search(r'youtube\.com/channel/(UC[\w-]{22})', html)
    if not m:
        raise RuntimeError(f"channel_id를 찾지 못했습니다: {handle_or_url}")
    channel_id = m.group(1)
    t = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    title = t.group(1) if t else ""
    return channel_id, title


def _walk(obj, key):
    """중첩 dict/list 에서 key 를 가진 값을 모두 찾는다(등장 순서 유지)."""
    if isinstance(obj, dict):
        if key in obj:
            yield obj[key]
        for v in obj.values():
            yield from _walk(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, key)


def _extract_initial_data(html: str) -> dict:
    idx = html.find("ytInitialData")
    if idx < 0:
        return {}
    start = html.find("{", idx)
    try:
        obj, _ = json.JSONDecoder().raw_decode(html[start:])
        return obj
    except json.JSONDecodeError:
        return {}


def list_videos_from_page(channel_id: str, proxy_url: str = "") -> list[VideoMeta]:
    """채널 '동영상' 탭(ytInitialData)에서 최근 영상을 최신순으로 파싱. RSS 가 404 일 때의 대체 경로.

    쇼츠는 별도 탭이라 여기에 안 나온다. 길이는 썸네일 배지(mm:ss)에서 읽고, 게시일은 '1일 전' 같은 상대 표기다.
    """
    s = _session(proxy_url)
    _consent_cookies(s)
    r = s.get(f"https://www.youtube.com/channel/{channel_id}/videos", timeout=30)
    r.raise_for_status()
    data = _extract_initial_data(r.text)
    if not data:
        raise RuntimeError("채널 페이지에서 ytInitialData 를 찾지 못했습니다")
    channel_title = ""
    for md in _walk(data, "channelMetadataRenderer"):
        channel_title = md.get("title", "") or channel_title
        break
    out: list[VideoMeta] = []
    seen: set[str] = set()
    for lk in _walk(data, "lockupViewModel"):
        vid = lk.get("contentId", "")
        if lk.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO" or not vid or vid in seen:
            continue
        seen.add(vid)
        md = lk.get("metadata", {}).get("lockupMetadataViewModel", {})
        title = md.get("title", {}).get("content", "")
        published = ""
        for row in md.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", []):
            parts = [p.get("text", {}).get("content", "") for p in row.get("metadataParts", [])]
            for p in parts:
                if p.endswith("전") or "스트리밍" in p or "예정" in p:
                    published = p
        duration = 0
        for badge in _walk(lk.get("contentImage", {}), "thumbnailBadgeViewModel"):
            txt = badge.get("text", "")
            if re.fullmatch(r"(?:\d{1,2}:)?\d{1,2}:\d{2}", txt):
                duration = parse_timestamp(txt)
                break
        thumb = ""
        srcs = lk.get("contentImage", {}).get("thumbnailViewModel", {}).get("image", {}).get("sources", [])
        if srcs:
            thumb = srcs[-1].get("url", "")
        out.append(VideoMeta(video_id=vid, title=title, channel_id=channel_id, channel_title=channel_title,
                             published=published, duration_sec=duration, thumbnail_url=thumb))
    return out


def list_recent_videos(channel_id: str, proxy_url: str = "") -> list[VideoMeta]:
    """최근 영상을 최신순으로 반환. RSS(최대 15개) → 실패하면 채널 페이지 파싱(약 30개)."""
    s = _session(proxy_url)
    r = s.get(RSS_URL.format(channel_id=channel_id), timeout=30)
    if r.status_code != 200 or b"<feed" not in r.content[:2000]:
        # 2026-05 부터 유튜브 RSS 가 광범위하게 404 를 낸다 → 페이지 파싱으로 대체
        return list_videos_from_page(channel_id, proxy_url)
    feed = feedparser.parse(r.content)
    out: list[VideoMeta] = []
    for e in feed.entries:
        vid = getattr(e, "yt_videoid", "") or e.get("id", "").split(":")[-1]
        if not vid:
            continue
        thumb = ""
        media = e.get("media_thumbnail") or []
        if media:
            thumb = media[0].get("url", "")
        out.append(VideoMeta(
            video_id=vid,
            title=e.get("title", ""),
            channel_id=channel_id,
            channel_title=e.get("author", ""),
            published=e.get("published", ""),
            description=(e.get("summary") or e.get("media_description") or ""),
            thumbnail_url=thumb,
        ))
    return out


def _extract_player_response(html: str) -> dict:
    idx = html.find("ytInitialPlayerResponse")
    if idx < 0:
        return {}
    start = html.find("{", idx)
    try:
        obj, _ = json.JSONDecoder().raw_decode(html[start:])
        return obj
    except json.JSONDecodeError:
        return {}


def enrich_video(meta: VideoMeta, proxy_url: str = "") -> VideoMeta:
    """시청 페이지에서 길이·설명·키워드를 보강한다. 실패해도 예외 없이 원본 반환."""
    try:
        s = _session(proxy_url)
        _consent_cookies(s)
        r = s.get(meta.url, timeout=30)
        r.raise_for_status()
        pr = _extract_player_response(r.text)
        vd = pr.get("videoDetails", {})
        if vd:
            meta.duration_sec = int(vd.get("lengthSeconds") or 0)
            meta.description = vd.get("shortDescription") or meta.description
            meta.keywords = list(vd.get("keywords") or [])
            meta.channel_title = vd.get("author") or meta.channel_title
            meta.title = vd.get("title") or meta.title
    except Exception:  # noqa: BLE001 - 보강 실패는 치명적이지 않음
        pass
    meta.chapters = parse_chapters(meta.description)
    return meta


def parse_timestamp(ts: str) -> int:
    parts = [int(p) for p in ts.split(":")]
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    return sec


def parse_chapters(description: str) -> list[dict]:
    """설명란의 '00:00 제목' 줄들을 챕터로 파싱. 00:00으로 시작하는 목록이 있을 때만 인정."""
    chapters = []
    for line in description.splitlines():
        m = CHAPTER_RE.match(line)
        if m:
            chapters.append({"start": parse_timestamp(m.group(1)), "title": m.group(2)})
    if len(chapters) >= 2 and chapters[0]["start"] == 0:
        starts = [c["start"] for c in chapters]
        if starts == sorted(starts):
            return chapters
    return []
