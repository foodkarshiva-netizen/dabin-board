"""스레드·인스타 성과 집계와 새 댓글 확인.

  python -m ytblog sns-stats                 게시물별 조회·좋아요·댓글 (다빈보드 블로그 탭은 stats 명령이 같이 갱신)
  python -m ytblog sns-comments [--peek]     아직 알리지 않은 새 댓글을 JSON 으로 출력 (--peek 이 아니면 '알림 완료'로 기록)
  python -m ytblog sns-reply threads|ig <댓글id> "답글"   댓글에 답글 달기
  python -m ytblog notify --file 메시지.txt   다빈보드 소통에 메시지 올리기
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone

import requests

TH = "https://graph.threads.net/v1.0"
IG = "https://graph.instagram.com/v21.0"
KST = timezone(timedelta(hours=9))


def _get(url: str, token: str, **params) -> dict:
    r = requests.get(url, params={**params, "access_token": token}, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"{url.split('/')[-1]} {r.status_code}: {r.text[:200]}")
    return r.json()


def _kst(ts: str) -> str:
    try:
        return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).astimezone(KST).strftime("%m/%d %H:%M")
    except Exception:  # noqa: BLE001
        return ts[:16]


def _first_line(text: str, n: int = 38) -> str:
    line = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
    return line if len(line) <= n else line[: n - 1] + "…"


# ---------------------------------------------------------------- 스레드
def _threads_posts(token: str, limit: int = 40) -> list[dict]:
    """내가 올린 원글만(답글 제외)."""
    rows = _get(f"{TH}/me/threads", token, fields="id,permalink,timestamp,text,is_reply,media_type", limit=limit).get("data", [])
    return [r for r in rows if not r.get("is_reply") and r.get("media_type") != "REPOST_FACADE"]


def threads_stats() -> dict:
    token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    if not token:
        return {}
    out = {"followers": 0, "posts": []}
    try:
        for m in _get(f"{TH}/me/threads_insights", token, metric="followers_count").get("data", []):
            out["followers"] = int((m.get("total_value") or {}).get("value") or 0)
    except Exception:  # noqa: BLE001
        pass
    for p in _threads_posts(token):
        row = {"id": p["id"], "url": p.get("permalink", ""), "title": _first_line(p.get("text", "")), "date": _kst(p.get("timestamp", "")),
               "views": 0, "likes": 0, "replies": 0, "reposts": 0}
        try:
            for m in _get(f"{TH}/{p['id']}/insights", token, metric="views,likes,replies,reposts,quotes").get("data", []):
                val = int(((m.get("values") or [{}])[0]).get("value") or 0)
                if m["name"] == "quotes":
                    row["reposts"] += val
                elif m["name"] in row:
                    row[m["name"]] += val
        except Exception:  # noqa: BLE001
            pass
        out["posts"].append(row)
    out["views"] = sum(p["views"] for p in out["posts"]); out["likes"] = sum(p["likes"] for p in out["posts"])
    return out


def threads_comments() -> list[dict]:
    token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    if not token:
        return []
    me = _get(f"{TH}/me", token, fields="username").get("username", "")
    out = []
    for p in _threads_posts(token, 25):
        try:
            rows = _get(f"{TH}/{p['id']}/conversation", token, fields="id,text,username,timestamp,permalink").get("data", [])
        except Exception:  # noqa: BLE001
            continue
        for c in rows:
            if c.get("username") and c["username"] != me:
                out.append({"platform": "threads", "id": c["id"], "user": c["username"], "text": c.get("text", ""),
                            "time": _kst(c.get("timestamp", "")), "post": _first_line(p.get("text", "")),
                            "url": c.get("permalink") or p.get("permalink", "")})
    return out


# ---------------------------------------------------------------- 인스타그램
def instagram_stats() -> dict:
    token = os.environ.get("IG_ACCESS_TOKEN", "")
    if not token:
        return {}
    out = {"followers": int(_get(f"{IG}/me", token, fields="followers_count").get("followers_count") or 0), "posts": []}
    for p in _get(f"{IG}/me/media", token, fields="id,permalink,timestamp,caption,like_count,comments_count", limit=40).get("data", []):
        row = {"id": p["id"], "url": p.get("permalink", ""), "title": _first_line(p.get("caption", "")), "date": _kst(p.get("timestamp", "")),
               "views": 0, "likes": int(p.get("like_count") or 0), "replies": int(p.get("comments_count") or 0), "saved": 0}
        try:
            for m in _get(f"{IG}/{p['id']}/insights", token, metric="reach,saved").get("data", []):
                val = int(((m.get("values") or [{}])[0]).get("value") or 0)
                row["views" if m["name"] == "reach" else "saved"] = val      # 인스타는 '도달한 계정 수'를 조회로 본다
        except Exception:  # noqa: BLE001
            pass
        out["posts"].append(row)
    out["views"] = sum(p["views"] for p in out["posts"]); out["likes"] = sum(p["likes"] for p in out["posts"])
    return out


def instagram_comments() -> list[dict]:
    token = os.environ.get("IG_ACCESS_TOKEN", "")
    if not token:
        return []
    me = _get(f"{IG}/me", token, fields="username").get("username", "")
    out = []
    for p in _get(f"{IG}/me/media", token, fields="id,permalink,caption,comments_count", limit=25).get("data", []):
        if not int(p.get("comments_count") or 0):
            continue
        try:
            rows = _get(f"{IG}/{p['id']}/comments", token, fields="id,text,username,timestamp").get("data", [])
        except Exception:  # noqa: BLE001
            continue
        for c in rows:
            if c.get("username") != me:
                out.append({"platform": "ig", "id": c["id"], "user": c.get("username", ""), "text": c.get("text", ""),
                            "time": _kst(c.get("timestamp", "")), "post": _first_line(p.get("caption", "")), "url": p.get("permalink", "")})
    return out


# ---------------------------------------------------------------- 공통
def sns_stats() -> dict:
    out = {"updated": int(time.time() * 1000)}
    for key, fn in (("threads", threads_stats), ("instagram", instagram_stats)):
        try:
            out[key] = fn()
        except Exception as e:  # noqa: BLE001
            out[key] = {"error": str(e)[:120]}
    return out


def new_comments(state, mark: bool = True) -> list[dict]:
    """아직 알리지 않은 댓글. mark=True 면 알린 것으로 기록한다."""
    seen = set(state.data.setdefault("sns_seen", []))
    rows = []
    for fn in (threads_comments, instagram_comments):
        try:
            rows += fn()
        except Exception:  # noqa: BLE001
            pass
    fresh = [c for c in rows if c["id"] not in seen]
    if mark and fresh:
        state.data["sns_seen"] = (state.data["sns_seen"] + [c["id"] for c in fresh])[-2000:]
        state.save()
    return fresh


def reply(platform: str, comment_id: str, text: str) -> str:
    if platform.startswith("th"):
        from .threads import post
        return post(text, reply_to=comment_id)
    token = os.environ.get("IG_ACCESS_TOKEN", "")
    r = requests.post(f"{IG}/{comment_id}/replies", data={"message": text, "access_token": token}, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"답글 실패 {r.status_code}: {r.text[:200]}")
    return r.json().get("id", "")


def notify(text: str, board_js) -> None:
    """다빈보드 소통에 메시지 한 건."""
    now = datetime.now(KST)
    doc = {"who": "cl", "text": text, "t": now.strftime("%H:%M"), "ts": int(time.time() * 1000)}
    subprocess.run(["node", str(board_js), "add", "chat", json.dumps(doc, ensure_ascii=False)], check=True,
                   capture_output=True, text=True, encoding="utf-8", timeout=120)


def report_lines(stats: dict) -> list[str]:
    lines = []
    for key, name in (("threads", "스레드"), ("instagram", "인스타")):
        s = stats.get(key) or {}
        if not s or s.get("error"):
            continue
        posts = s.get("posts", [])
        lines.append(f"· {name}: 팔로워 {s.get('followers', 0)}명 · 게시 {len(posts)}건 · 조회 {s.get('views', 0)} · 좋아요 {s.get('likes', 0)}")
        best = max(posts, key=lambda p: p["views"], default=None)
        if best and best["views"]:
            lines.append(f"  - 가장 많이 본 글: {best['title']} ({best['views']}회, 좋아요 {best['likes']}, 댓글 {best['replies']})")
    return lines
