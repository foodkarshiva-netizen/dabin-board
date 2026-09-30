# -*- coding: utf-8 -*-
"""댓글 폼 간소화: 댓글 + 이름(선택)만 남기고 이메일·웹사이트·쿠키 동의·안내문을 숨긴다(전역 스타일 CSS)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ytblog.config import Settings
from ytblog.wordpress import WordPressClient

CSS = ("/* yt-cmt */.comment-form-email,.comment-form-url,.comment-form-cookies-consent,.comment-notes{display:none!important}"
       ".comment-form-comment textarea{height:130px;min-height:110px}"
       ".comment-form-author label::after{content:' (안 써도 돼요)';opacity:.6;font-weight:400}"
       ".comment-form-author input{max-width:320px}")
s = Settings.load(); wp = WordPressClient(s.wp_url, s.wp_user, s.wp_app_password)
th = wp._get("themes", status="active")[0]
gid = th["_links"]["wp:user-global-styles"][0]["href"].rstrip("/").split("/")[-1]
g = wp._get(f"global-styles/{gid}", context="edit"); styles = g.get("styles") or {}
css = styles.get("css") or ""
if "/* yt-cmt */" in css:
    print("already")
else:
    styles["css"] = (css + "\n" + CSS).strip(); wp._post(f"global-styles/{gid}", styles=styles); print("added")
for st in ("hold", "approve"):
    print(st, len(wp._get("comments", status=st, per_page=50)))
