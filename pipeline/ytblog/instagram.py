"""인스타그램(Instagram API with Instagram Login) 연동: 토큰 채택·갱신, 캐러셀 게시.

페이스북 페이지 없이 프로페셔널(비즈니스/크리에이터) 계정만 있으면 된다.

.env 항목
  IG_ACCESS_TOKEN     개발자 콘솔 → 이용 사례 → Instagram API → 'Instagram 로그인이 포함된 API 설정' → 계정 추가 → 토큰 생성
  IG_USER_ID          ig-auth 가 채운다
  IG_TOKEN_EXPIRES    토큰 만료 시각(epoch). ig-refresh 가 갱신 (60일)

명령
  python -m ytblog ig-auth       토큰 확인 → 사용자 ID·만료 저장
  python -m ytblog ig-refresh    장기 토큰 갱신(만료 7일 전이면 자동)
"""
from __future__ import annotations

import os
import time

import requests

from .threads import _save_env

API = "https://graph.instagram.com/v21.0"


def _env(k: str, default: str = "") -> str:
    return os.environ.get(k, default)


def adopt_token() -> dict:
    token = _env("IG_ACCESS_TOKEN").strip()
    if not token:
        raise RuntimeError("IG_ACCESS_TOKEN 이 비어 있습니다")
    r = requests.get(f"{API}/me", params={"fields": "user_id,username,account_type", "access_token": token}, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"토큰 확인 실패 {r.status_code}: {r.text[:300]}")
    me = r.json()
    expires = int(_env("IG_TOKEN_EXPIRES", "0") or 0) or int(time.time()) + 5184000
    _save_env(IG_ACCESS_TOKEN=token, IG_USER_ID=str(me.get("user_id") or me["id"]), IG_TOKEN_EXPIRES=str(expires))
    return {"username": me.get("username"), "user_id": me.get("user_id") or me["id"], "account_type": me.get("account_type"),
            "expires_days": round((expires - time.time()) / 86400)}


def refresh_if_needed(force: bool = False) -> str:
    token = _env("IG_ACCESS_TOKEN")
    if not token:
        return "토큰 없음 (ig-auth 먼저)"
    exp = int(_env("IG_TOKEN_EXPIRES", "0") or 0)
    if not force and exp and exp - time.time() > 7 * 86400:
        return f"갱신 불필요 (만료까지 {round((exp - time.time()) / 86400)}일)"
    r = requests.get("https://graph.instagram.com/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"토큰 갱신 실패 {r.status_code}: {r.text[:300]}")
    j = r.json(); new_exp = int(time.time()) + int(j.get("expires_in", 5184000))
    _save_env(IG_ACCESS_TOKEN=j["access_token"], IG_TOKEN_EXPIRES=str(new_exp))
    return f"갱신 완료 (만료까지 {round((new_exp - time.time()) / 86400)}일)"


def _create(uid: str, token: str, data: dict) -> str:
    """컨테이너 생성. 방금 올린 이미지를 못 가져오는 일시 오류는 잠시 뒤 재시도."""
    for attempt in range(10):
        r = requests.post(f"{API}/{uid}/media", data={**data, "access_token": token}, timeout=90)
        err = r.json().get("error", {}) if r.status_code >= 400 else {}
        # 2207052 = 미디어 다운로드 실패(방금 올린 이미지를 아직 못 가져감). is_transient=false 로 와도 잠시 뒤엔 된다
        if r.status_code < 400 or attempt == 9 or not (err.get("is_transient") or err.get("error_subcode") == 2207052):
            break
        time.sleep(8)   # 카페24 가 Meta 요청에 가끔 응답을 못 함 → 짧게 여러 번
    if r.status_code >= 400:
        raise RuntimeError(f"컨테이너 생성 실패 {r.status_code}: {r.text[:300]}")
    return r.json()["id"]


def _wait(cid: str, token: str) -> None:
    for _ in range(20):
        st = requests.get(f"{API}/{cid}", params={"fields": "status_code,status", "access_token": token}, timeout=60).json()
        code = st.get("status_code")
        if code in ("FINISHED", "PUBLISHED", None):
            return
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"미디어 처리 오류: {st.get('status')}")
        time.sleep(3)


def post_carousel(image_urls: list[str], caption: str) -> str:
    """JPEG 이미지 2~10장 캐러셀 게시. 게시물 id 반환."""
    token, uid = _env("IG_ACCESS_TOKEN"), _env("IG_USER_ID")
    if not (token and uid):
        raise RuntimeError("IG_ACCESS_TOKEN / IG_USER_ID 가 없습니다 (ig-auth 먼저)")
    kids = []
    for u in image_urls[:10]:
        # media_type=IMAGE 를 빼면 "Only photo or video can be accepted"(2207052)로 거절된다
        kids.append(_create(uid, token, {"image_url": u, "media_type": "IMAGE", "is_carousel_item": "true"}))
    for k in kids:
        _wait(k, token)
    cid = _create(uid, token, {"media_type": "CAROUSEL", "children": ",".join(kids), "caption": caption[:2200]})
    _wait(cid, token)
    r = requests.post(f"{API}/{uid}/media_publish", data={"creation_id": cid, "access_token": token}, timeout=90)
    if r.status_code >= 400:
        raise RuntimeError(f"게시 실패 {r.status_code}: {r.text[:300]}")
    return r.json()["id"]


def permalink(media_id: str) -> str:
    r = requests.get(f"{API}/{media_id}", params={"fields": "permalink", "access_token": _env("IG_ACCESS_TOKEN")}, timeout=60)
    return r.json().get("permalink", "") if r.ok else ""


REEL_REPO = "https://github.com/foodkarshiva-netizen/dabin-board.git"   # 공개 저장소(GitHub Pages 용). 릴스 영상은 임시 브랜치로만 잠깐 올린다


def _host_video(video_path) -> tuple[str, callable]:
    """영상을 공개 URL 로. 카페24(jisikfill.com)는 Meta 의 영상 다운로드를 막아서(같은 파일이 다른 호스트에서는 됨, 10/5 확인)
    공개 GitHub 저장소의 임시 브랜치에 올리고 jsDelivr CDN 주소를 쓴다. 게시가 끝나면 cleanup() 으로 브랜치를 지운다."""
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path
    repo = _env("REEL_HOST_REPO", REEL_REPO)
    owner_repo = repo.split("github.com/")[1].removesuffix(".git")
    branch = f"reel-{int(time.time())}"
    tmp = Path(tempfile.mkdtemp())
    git = lambda *a: subprocess.run(["git", *a], cwd=tmp, check=True, capture_output=True, text=True, timeout=300)  # noqa: E731
    git("init", "-q"); git("checkout", "-q", "-b", branch)
    shutil.copy(video_path, tmp / "reel.mp4")
    git("add", "reel.mp4"); git("-c", "user.name=ytblog", "-c", "user.email=ytblog@local", "commit", "-qm", "reel")
    git("push", "-q", repo, branch)
    sha = git("rev-parse", "HEAD").stdout.strip()

    def cleanup() -> None:
        try:
            git("push", "-q", repo, "--delete", branch)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    url = f"https://cdn.jsdelivr.net/gh/{owner_repo}@{sha}/reel.mp4"
    for _ in range(12):    # CDN 이 처음 가져오는 데 몇 초 걸릴 수 있다
        try:
            if requests.head(url, timeout=30).status_code == 200:
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(5)
    return url, cleanup


def post_reel(video_path, caption: str) -> str:
    """릴스 게시. 인스타 로그인 방식 API 는 직접 업로드(resumable)를 지원하지 않아 video_url 이 필요하다."""
    token, uid = _env("IG_ACCESS_TOKEN"), _env("IG_USER_ID")
    if not (token and uid):
        raise RuntimeError("IG_ACCESS_TOKEN / IG_USER_ID 가 없습니다 (ig-auth 먼저)")
    url, cleanup = _host_video(video_path)
    try:
        cid, last = "", ""
        for attempt in range(3):
            r = requests.post(f"{API}/{uid}/media", data={"media_type": "REELS", "video_url": url, "caption": caption[:2200],
                                                          "share_to_feed": "true", "access_token": token}, timeout=90)
            if r.status_code >= 400:
                raise RuntimeError(f"릴스 컨테이너 실패 {r.status_code}: {r.text[:300]}")
            cid = r.json()["id"]
            for _ in range(60):   # 영상 처리: 보통 30초~2분
                st = requests.get(f"{API}/{cid}", params={"fields": "status_code,status", "access_token": token}, timeout=60).json()
                last = st.get("status_code") or ""
                if last in ("FINISHED", "ERROR", "EXPIRED"):
                    break
                time.sleep(5)
            if last == "FINISHED":
                break
            time.sleep(20)
        if last != "FINISHED":
            raise RuntimeError(f"영상 처리 실패({last})")
        r = requests.post(f"{API}/{uid}/media_publish", data={"creation_id": cid, "access_token": token}, timeout=90)
        if r.status_code >= 400:
            raise RuntimeError(f"릴스 게시 실패 {r.status_code}: {r.text[:300]}")
        return r.json()["id"]
    finally:
        try:
            cleanup()
        except Exception:  # noqa: BLE001
            pass
