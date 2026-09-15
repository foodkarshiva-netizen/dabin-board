# -*- coding: utf-8 -*-
"""블로그 도메인 교체: 옛 주소 → 새 주소.

사용:  python tools/migrate_domain.py https://jisikfill.com [--apply]
- 새 도메인이 같은 워드프레스로 연결됐는지(REST 응답의 사이트 이름 일치) 확인
- 설정의 사이트 주소(url) 변경 시도 (호스팅이 막으면 안내)
- 모든 글·페이지 본문과 발췌문의 옛 주소를 새 주소로 치환
- .env 의 WP_URL 갱신
--apply 없이 실행하면 바꿀 내용만 보여준다.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ytblog.config import PIPELINE_DIR, Settings  # noqa: E402
from ytblog.wordpress import WordPressClient  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__); return 2
    new = sys.argv[1].rstrip("/")
    apply = "--apply" in sys.argv
    s = Settings.load()
    old = s.wp_url
    wp_old = WordPressClient(old, s.wp_user, s.wp_app_password)
    print(f"옛 주소: {old}\n새 주소: {new}")

    # 1) 새 주소가 같은 사이트인지
    try:
        wp_new = WordPressClient(new, s.wp_user, s.wp_app_password)
        me_new = wp_new.check(); me_old = wp_old.check()
        assert me_new.get("id") == me_old.get("id")
        print("확인: 새 주소로 같은 워드프레스에 로그인됨")
    except Exception as e:  # noqa: BLE001
        print(f"아직 새 주소로 접속이 안 됩니다: {e}\n카페24 도메인 연결과 SSL 발급이 끝난 뒤 다시 실행하세요."); return 1

    # 2) 본문 치환 대상
    posts = wp_old._get("posts", per_page=100, status="publish,draft,pending,future,private", context="edit")
    pages = wp_old._get("pages", per_page=100, status="publish,draft,private", context="edit")
    targets = []
    for kind, items in (("posts", posts), ("pages", pages)):
        for p in items:
            raw = p["content"]["raw"]; ex = p.get("excerpt", {}).get("raw", "")
            n = raw.count(old) + ex.count(old)
            if n:
                targets.append((kind, p["id"], n, raw, ex))
    print(f"치환 대상: 글/페이지 {len(targets)}개, 옛 주소 등장 {sum(t[2] for t in targets)}회")
    if not apply:
        print("--apply 를 붙이면 실제로 바꿉니다."); return 0

    # 3) 사이트 주소 설정
    try:
        r = wp_new._post("settings", url=new)
        print("설정 url:", r.get("url"))
    except Exception as e:  # noqa: BLE001
        print(f"설정 url 변경 실패(호스팅에서 고정했을 수 있음): {e}\n관리자 → 설정 → 일반에서 두 주소를 직접 {new} 로 바꿔 주세요.")

    # 4) 본문 치환
    for kind, pid, n, raw, ex in targets:
        wp_new._post(f"{kind}/{pid}", content=raw.replace(old, new), excerpt=ex.replace(old, new))
        print(f"  {kind}/{pid}: {n}회 치환")

    # 5) .env
    env = PIPELINE_DIR / ".env"
    if env.exists():
        txt = env.read_text(encoding="utf-8")
        txt2 = re.sub(r"^WP_URL=.*$", f"WP_URL={new}", txt, flags=re.M)
        env.write_text(txt2, encoding="utf-8"); print(".env WP_URL 갱신")
    # 6) 검증
    home = wp_new._get("settings").get("url")
    print("완료. 사이트 주소:", home)
    return 0


if __name__ == "__main__":
    sys.exit(main())
