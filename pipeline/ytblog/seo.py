"""유입용 SEO 도구.

- IndexNow: 글이 공개되면 네이버·IndexNow(빙 등) 에 즉시 알린다. 키는 IndexNow 플러그인이 사이트 루트에
  https://jisikfill.com/<키>.txt 로 서빙한다. .env INDEXNOW_KEY 가 없으면 플러그인 REST 에서 읽는다.
- 검색 수요(demand): 네이버·구글 자동완성으로 키워드별 검색 수요 신호를 본다(키 불필요).

  python -m ytblog indexnow [URL ...]      URL 을 알린다(없으면 공개된 글 전부 + 홈)
  python -m ytblog demand 키워드 [키워드 ...]  자동완성 기반 수요 점수·추천 검색어
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}
ENDPOINTS = ["https://searchadvisor.naver.com/indexnow", "https://api.indexnow.org/indexnow"]


def indexnow_key(settings, wp=None) -> str:
    key = os.environ.get("INDEXNOW_KEY", "")
    if key:
        return key
    try:
        from .wordpress import WordPressClient
        wp = wp or WordPressClient(settings.wp_url, settings.wp_user, settings.wp_app_password)
        r = wp.s.get(settings.wp_url + "/wp-json/indexnow/v_1.0.4/apiKey", timeout=30)
        return (r.json() or {}).get("APIKey", "") if r.ok else ""
    except Exception:  # noqa: BLE001
        return ""


def submit(settings, urls: list[str], wp=None) -> dict:
    """URL 목록을 네이버·IndexNow 에 제출. {엔드포인트: 상태코드}."""
    urls = [u for u in dict.fromkeys(urls) if u]
    key = indexnow_key(settings, wp)
    if not (urls and key):
        return {"skipped": "키 또는 URL 없음"}
    host = urlparse(settings.wp_url).netloc
    body = {"host": host, "key": key, "keyLocation": f"{settings.wp_url}/{key}.txt", "urlList": urls[:10000]}
    out = {}
    for ep in ENDPOINTS:
        try:
            r = requests.post(ep, json=body, headers={"Content-Type": "application/json; charset=utf-8"}, timeout=30)
            out[ep.split("/")[2]] = r.status_code
        except Exception as e:  # noqa: BLE001
            out[ep.split("/")[2]] = f"오류 {str(e)[:60]}"
    return out


def naver_suggest(q: str) -> list[str]:
    try:
        r = requests.get("https://ac.search.naver.com/nx/ac", headers=UA, timeout=15, params={
            "q": q, "con": 1, "frm": "nv", "ans": 2, "r_format": "json", "r_enc": "UTF-8", "r_unicode": 0,
            "t_koreng": 1, "run": 2, "rev": 4, "q_enc": "UTF-8", "st": 100})
        return [i[0] for i in (r.json().get("items") or [[]])[0]]
    except Exception:  # noqa: BLE001
        return []


def google_suggest(q: str) -> list[str]:
    try:
        r = requests.get("https://suggestqueries.google.com/complete/search", headers=UA, timeout=15,
                         params={"client": "firefox", "hl": "ko", "q": q})
        import json
        return list(json.loads(r.content.decode("utf-8", "ignore"))[1])
    except Exception:  # noqa: BLE001
        return []


def demand(keyword: str) -> dict:
    """자동완성 신호로 본 검색 수요. score 0~20: 네이버·구글 제안 수 + 정확히 같은 검색어가 제안되면 가산."""
    kw = keyword.strip()
    nv, gg = naver_suggest(kw), google_suggest(kw)
    norm = kw.replace(" ", "").lower()
    exact = any(s.replace(" ", "").lower() == norm for s in nv + gg)
    score = min(len(nv), 8) + min(len(gg), 8) + (4 if exact else 0)
    phrases = list(dict.fromkeys([s for s in nv + gg if s.replace(" ", "").lower() != norm]))
    return {"keyword": kw, "score": score, "exact": exact, "naver": nv[:8], "google": gg[:8], "phrases": phrases[:10]}
