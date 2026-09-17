"""Threads(스레드) API 연동: 토큰 발급·갱신, 이미지+글 게시.

.env 항목
  THREADS_APP_ID, THREADS_APP_SECRET   Meta 개발자 앱의 Threads 앱 ID/시크릿
  THREADS_REDIRECT_URI                 앱에 등록한 리디렉션 URI (기본 https://jisikfill.com/)
  THREADS_AUTH_CODE                    인증 URL 허용 후 주소창에 붙는 code=... 값 (1회용, 발급 뒤 비워도 됨)
  THREADS_ACCESS_TOKEN, THREADS_USER_ID  threads-auth 가 채운다 (장기 토큰, 60일)
  THREADS_TOKEN_EXPIRES                토큰 만료 시각(epoch). threads-refresh 가 갱신

명령
  python -m ytblog threads-auth      code → 단기 토큰 → 장기 토큰 → .env 저장
  python -m ytblog threads-refresh   장기 토큰 갱신(만료 7일 전이면 자동 갱신)
  python -m ytblog threads-test      "연결 테스트" 글 게시 (이미지 없이)
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import requests

from .config import PIPELINE_DIR

API = "https://graph.threads.net"
SCOPES = "threads_basic,threads_content_publish"


def _env(k: str, default: str = "") -> str:
    return os.environ.get(k, default)


def _save_env(**kv: str) -> None:
    """.env 의 해당 키를 갱신(없으면 추가)."""
    p = PIPELINE_DIR / ".env"
    txt = p.read_text(encoding="utf-8") if p.exists() else ""
    for k, v in kv.items():
        line = f"{k}={v}"
        if re.search(rf"^{k}=.*$", txt, flags=re.M):
            txt = re.sub(rf"^{k}=.*$", line, txt, flags=re.M)
        else:
            txt = txt.rstrip("\n") + "\n" + line + "\n"
        os.environ[k] = v
    p.write_text(txt, encoding="utf-8")


def auth_url() -> str:
    return (f"https://threads.net/oauth/authorize?client_id={_env('THREADS_APP_ID')}"
            f"&redirect_uri={_env('THREADS_REDIRECT_URI', 'https://jisikfill.com/')}&scope={SCOPES}&response_type=code")


def exchange_code() -> dict:
    """인증 code → 단기 토큰 → 장기 토큰. 결과를 .env 에 저장."""
    app_id, secret = _env("THREADS_APP_ID"), _env("THREADS_APP_SECRET")
    code = _env("THREADS_AUTH_CODE").split("#")[0].strip()
    redirect = _env("THREADS_REDIRECT_URI", "https://jisikfill.com/")
    if not (app_id and secret and code):
        raise RuntimeError("THREADS_APP_ID / THREADS_APP_SECRET / THREADS_AUTH_CODE 를 .env 에 넣어 주세요")
    r = requests.post(f"{API}/oauth/access_token", data={
        "client_id": app_id, "client_secret": secret, "grant_type": "authorization_code",
        "redirect_uri": redirect, "code": code}, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"단기 토큰 실패 {r.status_code}: {r.text[:300]}")
    short = r.json()
    r2 = requests.get(f"{API}/access_token", params={
        "grant_type": "th_exchange_token", "client_secret": secret, "access_token": short["access_token"]}, timeout=60)
    if r2.status_code >= 400:
        raise RuntimeError(f"장기 토큰 실패 {r2.status_code}: {r2.text[:300]}")
    long = r2.json()
    token = long["access_token"]; expires = int(time.time()) + int(long.get("expires_in", 5184000))
    me = requests.get(f"{API}/v1.0/me", params={"fields": "id,username", "access_token": token}, timeout=60).json()
    _save_env(THREADS_ACCESS_TOKEN=token, THREADS_USER_ID=str(me.get("id", short.get("user_id", ""))),
              THREADS_TOKEN_EXPIRES=str(expires), THREADS_AUTH_CODE="")
    return {"username": me.get("username"), "user_id": me.get("id"), "expires_days": round((expires - time.time()) / 86400)}


def refresh_if_needed(force: bool = False) -> str:
    token = _env("THREADS_ACCESS_TOKEN")
    if not token:
        return "토큰 없음 (threads-auth 먼저)"
    exp = int(_env("THREADS_TOKEN_EXPIRES", "0") or 0)
    if not force and exp and exp - time.time() > 7 * 86400:
        return f"갱신 불필요 (만료까지 {round((exp - time.time()) / 86400)}일)"
    r = requests.get(f"{API}/refresh_access_token", params={"grant_type": "th_refresh_token", "access_token": token}, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"토큰 갱신 실패 {r.status_code}: {r.text[:300]}")
    j = r.json(); new_exp = int(time.time()) + int(j.get("expires_in", 5184000))
    _save_env(THREADS_ACCESS_TOKEN=j["access_token"], THREADS_TOKEN_EXPIRES=str(new_exp))
    return f"갱신 완료 (만료까지 {round((new_exp - time.time()) / 86400)}일)"


def post(text: str, image_url: str = "") -> str:
    """이미지(+글) 또는 글만 게시. 게시된 스레드 id 반환."""
    token, uid = _env("THREADS_ACCESS_TOKEN"), _env("THREADS_USER_ID")
    if not (token and uid):
        raise RuntimeError("THREADS_ACCESS_TOKEN / THREADS_USER_ID 가 없습니다 (threads-auth 먼저)")
    data = {"access_token": token, "text": text[:500]}
    if image_url:
        data.update({"media_type": "IMAGE", "image_url": image_url})
    else:
        data["media_type"] = "TEXT"
    r = requests.post(f"{API}/v1.0/{uid}/threads", data=data, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"컨테이너 생성 실패 {r.status_code}: {r.text[:300]}")
    cid = r.json()["id"]
    for _ in range(10):  # 이미지 처리 대기
        st = requests.get(f"{API}/v1.0/{cid}", params={"fields": "status,error_message", "access_token": token}, timeout=60).json()
        if st.get("status") in ("FINISHED", None):
            break
        if st.get("status") == "ERROR":
            raise RuntimeError(f"미디어 처리 오류: {st.get('error_message')}")
        time.sleep(3)
    r2 = requests.post(f"{API}/v1.0/{uid}/threads_publish", data={"creation_id": cid, "access_token": token}, timeout=60)
    if r2.status_code >= 400:
        raise RuntimeError(f"게시 실패 {r2.status_code}: {r2.text[:300]}")
    return r2.json()["id"]
