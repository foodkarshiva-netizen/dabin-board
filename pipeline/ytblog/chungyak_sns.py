"""청약 공고를 SNS·주간 글로: 스레드 '오늘의 청약' 1건(월요일은 이번 주 모음) + 블로그 '이번 주 청약 일정 총정리'.

  python -m ytblog chungyak-threads [--dry-run]   스레드 1건(월요일은 주간 모음). 직전 게시 3시간 안이면 보류
  python -m ytblog chungyak-week [--dry-run]      이번 주(월~일) 접수 공고 정리 글 만들기·갱신
재료는 chungyak.py 가 data/chungyak_state.json 에 남긴 공고별 facts(시세 차이·자격·세대수·역).
"""
from __future__ import annotations

import html as _html
import json
import re
import time
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
CAPITAL = ("서울", "경기", "인천")


def _today() -> date:
    return datetime.now(KST).date()


def _e(s) -> str:
    return _html.escape(str(s or ""), quote=True)


def eok(man: int) -> str:
    """만원 → '5.9억' / '8,700만'."""
    man = abs(int(man))
    return f"{man / 10000:.1f}억".replace(".0억", "억") if man >= 10000 else f"{man:,}만"


def md(s: str) -> str:
    return f"{int(s[5:7])}/{int(s[8:10])}" if s and len(s) >= 10 else ""


def _load(settings) -> tuple[dict, object]:
    sp = settings.data_dir / "chungyak_state.json"
    return json.loads(sp.read_text(encoding="utf-8")), sp


def _live_short(live: str) -> str:
    if not live:
        return ""
    m = re.search(r"해당지역:\s*([^/]+?)(?:\s*\(|\s*/|$)", live)
    if m:
        return f"{m.group(1).strip()} 우선"
    return re.sub(r"\s*\(.*?\)", "", live).strip() + "만"


def _score(v: dict) -> float:
    f = v.get("facts") or {}
    cap = 1.6 if any(v.get("region", "").startswith(c) for c in CAPITAL) else 1.0
    gap = f.get("gap")
    s = (gap / 10000 if gap and gap > 0 else -1) * cap
    if "불법행위" in v.get("kind", ""):
        s += 1.5
    s += min((f.get("units") or 0) / 2000, 0.5)
    return s


def upcoming(state: dict, days: int = 7) -> list[dict]:
    t = _today().isoformat()
    end = (_today() + timedelta(days=days)).isoformat()
    rows = []
    for pb, v in state["posts"].items():
        if v.get("status") != "publish" or not v.get("facts"):
            continue
        a, b = v.get("applyStart") or "", v.get("applyEnd") or v.get("applyStart") or ""
        if b >= t and a <= end:
            rows.append({**v, "pbno": pb})
    return rows


# ---------------------------------------------------------------- 스레드
def pick_text(v: dict) -> tuple[str, list[str]]:
    f = v["facts"]
    name, region = v["name"], v.get("region", "")
    gap = f.get("gap")
    if gap and gap > 0:
        head = f"{region} {name}, 근처 시세보다 {eok(gap)} 싸게 나옴."
    else:
        head = f"{region} {name} 청약 나옴."
    lines = [head, ""]
    if f.get("price"):
        lines.append(f"전용 {f['area']}㎡ 분양가 {eok(f['price'])}")
        if f.get("market"):
            lines.append(f"근처 실거래로 본 시세 {eok(f['market'])}" + ("" if f.get("basisNew") else " (구축 기준이라 더 높을 수도)"))
        lines.append("")
    lines.append(f"접수 {md(v.get('applyStart'))}" + (f"~{md(v['applyEnd'])}" if v.get("applyEnd") and v["applyEnd"] != v.get("applyStart") else "")
                 + (f" · 발표 {md(v['winDate'])}" if v.get("winDate") else ""))
    extra = []
    if f.get("units"):
        extra.append(f"총 {f['units']:,}세대")
    if f.get("station"):
        st = re.match(r"(.+?)\s(\d+)m$", f["station"])
        if st:
            m = int(st.group(2))
            extra.append(f"{st.group(1)} {m / 1000:.1f}km" if m >= 1000 else f"{st.group(1)} {m // 10 * 10}m")
        else:
            extra.append(f["station"])
    if extra:
        lines.append(" · ".join(extra))
    if f.get("live"):
        lines.append(f"청약은 {_live_short(f['live'])}")
    if f.get("liveDuty") and f["liveDuty"] != "없음":
        lines.append(f"거주의무 {f['liveDuty']}")
    lines += ["", "이런 거 넣어 봄?"]
    return "\n".join(lines)[:480], ["넣어 봄", "자격이 안 됨", "그냥 구경"]


def roundup_text(rows: list[dict]) -> str:
    n = len(rows)
    rows = sorted(rows, key=_score, reverse=True)[:5]
    lines = [f"이번 주 청약 접수 {n}건. 눈에 띄는 것만 추림.", ""]
    for v in rows:
        f = v["facts"]
        gap = f.get("gap")
        tail = (f"시세보다 {eok(gap)} 쌈" if gap and gap > 0 else (f"분양가 {eok(f['price'])}" if f.get("price") else v.get("kind", "")))
        lines.append(f"· {v['name']} ({v.get('region', '')}) {md(v.get('applyStart'))} — {tail}")
    lines += ["", "자격(거주 지역) 꼭 보고 넣어야 함.", "", "이번 주 넣어 볼 데 있어?"]
    return "\n".join(lines)[:480]


def post_threads(settings, dry_run: bool = False, log=print) -> str:
    from . import threads as th
    state, sp = _load(settings)
    rows = upcoming(state, 7)
    monday = _today().weekday() == 0
    if monday:
        week = [v for v in rows if (v.get("applyStart") or "") <= (_today() + timedelta(days=6)).isoformat()]
        if not week:
            return "이번 주 접수 공고 없음"
        text, poll = roundup_text(week), None
        link = state.get("week", {}).get("link") or f"{settings.wp_url}/chungyak/"
        key, target = f"week-{_today().isoformat()}", None
        reply = f"이번 주 공고 단지별 분양가·시세·자격 정리해 둠\n{link}"
    else:
        tmr = (_today() + timedelta(days=1)).isoformat()      # 오늘 마감인 공고는 저녁에 알려 봐야 소용없음
        open_ = [v for v in rows if (v.get("applyEnd") or v.get("applyStart") or "") >= tmr and not (v.get("sns") or {}).get("threads")]
        cand = [v for v in open_ if (v["facts"].get("gap") or 0) > 0] or [v for v in open_ if v["facts"].get("price")]
        if not cand:
            return "오늘 올릴 만한 청약 없음"
        target = max(cand, key=_score)
        text, poll = pick_text(target)
        key = target["pbno"]
        reply = f"단지 규모·역·학교·계약금·취득세까지 정리해 둠\n{target['link']}"
    if dry_run:
        return f"[dry-run]\n{text}\n  투표 {poll}\n  └ {reply}"
    if key in state.setdefault("snsDone", []):
        return "이미 올림"
    th.refresh_if_needed()
    gap = th.hours_since_last_post()
    if gap < 3:
        return f"보류: 직전 게시가 {gap:.1f}시간 전"
    tid = th.post(text, topic="청약", poll=poll)
    state["snsDone"].append(key)
    if target:
        state["posts"][target["pbno"]].setdefault("sns", {})["threads"] = tid
    sp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    msg = "링크 답글 실패"
    for attempt in range(4):
        time.sleep(8 if attempt == 0 else 15 * attempt)
        try:
            th.post(reply, reply_to=tid); msg = "링크 답글 달림"; break
        except Exception:  # noqa: BLE001
            continue
    return f"스레드 게시 완료 (id {tid}, {'주간 모음' if monday else target['name']}) · {msg}"


# ---------------------------------------------------------------- 주간 글
def week_post(settings, dry_run: bool = False) -> str:
    from .chungyak import CATEGORY
    from .wordpress import WordPressClient
    state, sp = _load(settings)
    today = _today()
    mon = today - timedelta(days=today.weekday()); sun = mon + timedelta(days=6)
    rows = [{**v, "pbno": pb} for pb, v in state["posts"].items() if v.get("status") == "publish" and v.get("facts")
            and mon.isoformat() <= (v.get("applyStart") or "") <= sun.isoformat()]
    if not rows:
        return "이번 주 접수 공고 없음"
    rows.sort(key=lambda v: (v.get("applyStart") or "", -_score(v)))
    wk = (mon.day - 1) // 7 + 1
    title = f"이번 주 청약 일정 총정리 ({mon.month}월 {wk}주차, {md(mon.isoformat())}~{md(sun.isoformat())})"
    slug = f"chungyak-week-{mon.isoformat()}"
    cap = [v for v in rows if any(v.get("region", "").startswith(c) for c in CAPITAL)]
    cheap = sorted([v for v in rows if (v["facts"].get("gap") or 0) > 0], key=lambda v: v["facts"]["gap"], reverse=True)
    intro = (f"{md(mon.isoformat())}부터 {md(sun.isoformat())}까지 청약 접수를 받는 아파트는 {len(rows)}곳이에요"
             + (f"(수도권 {len(cap)}곳)" if cap else "") + ". "
             + (f"주변 실거래 시세보다 분양가가 낮게 나온 곳은 {len(cheap)}곳이고, 차이가 가장 큰 곳은 {cheap[0]['name']}(약 {eok(cheap[0]['facts']['gap'])})예요. " if cheap else "")
             + "단지마다 분양가·시세·계약금·자격을 정리한 페이지로 이어져요.")
    trs = []
    for v in rows:
        f = v["facts"]
        gap = f.get("gap")
        trs.append("<tr>" + "".join(f"<td>{c}</td>" for c in [
            f'<a href="{_e(v["link"])}">{_e(v["name"])}</a>',
            _e(v.get("region", "")),
            _e(md(v.get("applyStart"))) + (f"~{_e(md(v['applyEnd']))}" if v.get("applyEnd") and v["applyEnd"] != v.get("applyStart") else ""),
            (f"{f['area']}㎡ {eok(f['price'])}" if f.get("price") else "공고문 확인"),
            (f"<strong class='{'up' if gap > 0 else 'down'}'>{'−' if gap > 0 else '+'}{eok(gap)}</strong>" if gap is not None else "-"),
            _e(_live_short(f.get("live", "")) or "-")]) + "</tr>")
    table = ('<!-- wp:table {"className":"yt-cy"} -->\n<figure class="wp-block-table yt-cy"><table><thead><tr>'
             "<th>단지</th><th>지역</th><th>접수</th><th>분양가</th><th>시세 대비</th><th>자격</th></tr></thead><tbody>"
             + "".join(trs) + "</tbody></table></figure>\n<!-- /wp:table -->")
    picks = sorted(rows, key=_score, reverse=True)[:3]
    li = []
    for v in picks:
        f = v["facts"]
        bits = [f"{v.get('region', '')} · {v.get('kind', '')}"]
        if f.get("gap") and f["gap"] > 0:
            bits.append(f"전용 {f['area']}㎡ 분양가 {eok(f['price'])}, 근처 시세보다 약 {eok(f['gap'])} 낮음")
        if f.get("units"):
            bits.append(f"총 {f['units']:,}세대")
        if f.get("live"):
            bits.append(f"청약 자격: {_live_short(f['live'])}")
        li.append(f'<li><a href="{_e(v["link"])}"><strong>{_e(v["name"])}</strong></a> — {_e(" / ".join(bits))}</li>')
    H2 = lambda t: f'<!-- wp:heading -->\n<h2 class="wp-block-heading">{_e(t)}</h2>\n<!-- /wp:heading -->'  # noqa: E731
    P = lambda t, cls="": (f'<!-- wp:paragraph{json.dumps({"className": cls}) if cls else ""} -->\n'  # noqa: E731
                           f'<p{f" class={chr(34)}{cls}{chr(34)}" if cls else ""}>{t}</p>\n<!-- /wp:paragraph -->')
    content = "\n\n".join([
        P(_e(intro)), H2("이번 주 접수 일정"), table,
        P("시세 대비는 같은 면적대 주변 실거래로 추정한 시세에서 분양가를 뺀 값이에요(−는 분양가가 더 낮음). 구축 위주로 추정한 곳은 실제 새 아파트 시세가 더 높을 수 있어요.", "yt-note"),
        H2("눈여겨볼 단지"), f'<!-- wp:list -->\n<ul class="wp-block-list">{"".join(li)}</ul>\n<!-- /wp:list -->',
        P(f'지난 공고와 다음 주 공고는 <a href="{settings.wp_url}/chungyak/">청약 일정·분양가 정리</a>에서 매일 갱신돼요. 청약 자격과 일정은 반드시 모집공고문으로 확인하세요.'),
    ])
    excerpt = re.sub(r"<[^>]+>", "", intro)[:150]
    if dry_run:
        return f"[dry-run] {title} · {len(rows)}건\n{excerpt}"
    wp = WordPressClient(settings.wp_url, settings.wp_user, settings.wp_app_password)
    cats = wp.category_ids([CATEGORY]); tags = wp.tag_ids(["청약 일정", "이번 주 청약", "아파트 청약", "분양가"])
    w = state.get("week") or {}
    if w.get("slug") == slug and w.get("id"):
        p = wp.update_post(w["id"], title=title, content=content, excerpt=excerpt, tags=tags)
    else:
        p = wp.create_post(title=title, content=content, status="publish", slug=slug, excerpt=excerpt, categories=cats, tags=tags,
                           meta={"_yoast_wpseo_metadesc": excerpt})
        try:
            from .seo import submit
            submit(settings, [p.get("link", "")], wp)
        except Exception:  # noqa: BLE001
            pass
    state["week"] = {"slug": slug, "id": p["id"], "link": p.get("link", "")}
    sp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return f"주간 글 {'갱신' if w.get('slug') == slug else '게시'}: {p.get('link', '')} ({len(rows)}건)"
