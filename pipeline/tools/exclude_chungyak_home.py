# -*- coding: utf-8 -*-
"""홈·글 하단 '최근 글'·사이드바 목록에서 '청약 분석' 글을 뺀다(지식 글이 묻히지 않게).

블록 테마 쿼리 루프에 카테고리 제한(taxQuery)을 건다. 홈은 기본 쿼리를 물려받는(inherit) 방식이라
inherit 를 끄고 같은 조건(최신순·페이지당 수 유지)에 지식 카테고리만 넣는다.
카테고리를 새로 만들면 이 스크립트를 다시 돌리면 된다(청약 분석 외 전부 포함).
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ytblog.config import Settings  # noqa: E402
from ytblog.wordpress import WordPressClient  # noqa: E402

s = Settings.load(); wp = WordPressClient(s.wp_url, s.wp_user, s.wp_app_password)
cats = wp._get("categories", per_page=100)
keep = sorted(c["id"] for c in cats if c["name"] != "청약 분석")
print("포함 카테고리:", keep)


def fix(raw: str, force_no_inherit: bool) -> tuple[str, int]:
    n = 0

    def rep(m):
        nonlocal n
        attrs = json.loads(m.group(1))
        q = attrs.get("query", {})
        if q.get("inherit") and not force_no_inherit:
            return m.group(0)
        q["inherit"] = False
        q["taxQuery"] = {"category": keep}
        attrs["query"] = q
        n += 1
        return f"<!-- wp:query {json.dumps(attrs, ensure_ascii=False)} -->"
    return re.sub(r"<!-- wp:query (\{.*?\}) -->", rep, raw), n


for kind, slug, force in (("templates", "twentytwentyfive//home", True), ("templates", "twentytwentyfive//single", False),
                          ("templates", "twentytwentyfive//search", False), ("template-parts", "twentytwentyfive//sidebar", False)):
    t = wp._get(f"{kind}/{slug}", context="edit")
    raw = t["content"]["raw"]
    new, n = fix(raw, force)
    if n and new != raw:
        wp._post(f"{kind}/{slug}", content=new)
    print(slug, "변경", n)
