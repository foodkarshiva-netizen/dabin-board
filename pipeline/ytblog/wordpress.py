"""WordPress REST API 발행(애플리케이션 비밀번호 인증)."""
from __future__ import annotations

import mimetypes
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


class WordPressClient:
    def __init__(self, base_url: str, user: str, app_password: str):
        if not (base_url and user and app_password):
            raise ValueError("WP_URL / WP_USER / WP_APP_PASSWORD 가 필요합니다")
        self.api = base_url.rstrip("/") + "/wp-json/wp/v2"
        self.s = requests.Session()
        self.s.auth = (user, app_password)
        self.s.headers.update({"User-Agent": "ytblog/0.1"})

    def _get(self, path: str, **params):
        r = self.s.get(f"{self.api}/{path}", params=params, timeout=60)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, **json):
        r = self.s.post(f"{self.api}/{path}", json=json, timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(f"WordPress {path} 실패 {r.status_code}: {r.text[:500]}")
        return r.json()

    def check(self) -> dict:
        return self._get("users/me", context="edit")

    def find_post_by_video(self, video_id: str) -> dict | None:
        """본문(임베드)에 video_id 가 들어 있는 글이 있으면 반환. 중복 발행 방지용."""
        for status in ("publish", "draft", "pending", "future", "private"):
            hits = self._get("posts", search=video_id, status=status, per_page=5, context="edit")
            for p in hits:
                if video_id in p.get("content", {}).get("raw", "") or video_id in p.get("content", {}).get("rendered", ""):
                    return p
        return None

    def get_post(self, post_id: int) -> dict:
        return self._get(f"posts/{post_id}", context="edit")

    def update_post(self, post_id: int, **fields) -> dict:
        return self._post(f"posts/{post_id}", **fields)

    def count_recent_posts(self, days: float, category_id: int = 0, marker: str = "ytblog") -> int:
        """최근 days 일 안에 만들어진 글(초안·공개 등) 수. 발행 상한 판단용.

        marker 가 본문에 들어 있는 글만 세므로 손으로 쓴 글은 제외된다.
        """
        after = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()
        params = dict(after=after, status="publish,draft,pending,future,private",
                      per_page=100, context="edit", orderby="date", order="desc")
        if category_id:
            params["categories"] = category_id
        n = 0
        for p in self._get("posts", **params):
            raw = p.get("content", {}).get("raw", "") or p.get("content", {}).get("rendered", "")
            if marker in raw:
                n += 1
        return n

    def upload_media(self, path: Path, alt: str, title: str = "") -> tuple[int, str]:
        mime = mimetypes.guess_type(str(path))[0] or "image/png"
        with open(path, "rb") as f:
            r = self.s.post(
                f"{self.api}/media", data=f.read(), timeout=180,
                headers={"Content-Type": mime,
                         "Content-Disposition": f'attachment; filename="{path.name}"'},
            )
        if r.status_code >= 400:
            raise RuntimeError(f"미디어 업로드 실패 {r.status_code}: {r.text[:500]}")
        m = r.json()
        self._post(f"media/{m['id']}", alt_text=alt, title=title or alt)
        return m["id"], m["source_url"]

    def delete_media_in(self, html_text: str, keep: set[str] | None = None) -> int:
        """본문에 들어 있던 업로드 이미지(이 사이트 uploads URL)를 미디어 라이브러리에서 삭제. keep 에 있는 URL 은 남긴다."""
        import re
        keep = keep or set()
        urls = set(re.findall(r"https?://[^'\" ]+/wp-content/uploads/[^'\" ]+", html_text)) - keep
        n = 0
        for u in urls:
            base = u.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            for m in self._get("media", search=base, per_page=10):
                if m.get("source_url") == u:
                    r = self.s.delete(f"{self.api}/media/{m['id']}", params={"force": "true"}, timeout=60)
                    if r.status_code < 400:
                        n += 1
        return n

    def _term_id(self, taxonomy: str, name: str) -> int:
        hits = self._get(taxonomy, search=name, per_page=20)
        for h in hits:
            if h["name"].lower() == name.lower():
                return h["id"]
        return self._post(taxonomy, name=name)["id"]

    def category_ids(self, names: list[str]) -> list[int]:
        return [self._term_id("categories", n) for n in names if n]

    def tag_ids(self, names: list[str]) -> list[int]:
        return [self._term_id("tags", n) for n in names if n]

    def create_post(self, *, title: str, content: str, status: str, slug: str = "",
                    excerpt: str = "", categories: list[int] | None = None,
                    tags: list[int] | None = None, featured_media: int = 0) -> dict:
        payload = {"title": title, "content": content, "status": status, "excerpt": excerpt,
                   "categories": categories or [], "tags": tags or []}
        if slug:
            payload["slug"] = slug
        if featured_media:
            payload["featured_media"] = featured_media
        return self._post("posts", **payload)
