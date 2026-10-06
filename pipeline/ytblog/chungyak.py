"""청약 공고 자동 페이지: 청약홈 공고 → 분양가·주변 시세·계약금·취득세를 정리한 글을 jisikfill.com 에 올린다.

- 공고 목록·상세: 청약홈(applyhome.co.kr) 공개 화면. 무순위/잔여세대 + 일반분양, 전국.
- 시세 추정: 국토부 실거래가 공개시스템(rt.molit.go.kr) 시군구 최근 12개월 아파트 매매 CSV(키·로그인 불필요).
  같은 동 10년 내 신축 중앙값 → 같은 동 상위 25% → 같은 시군구 10년 내 신축 중앙값. 직거래·해제 거래 제외, 3건 미만이면 추정 안 함.
  (다빈보드 board-tools/chungyak-enrich.js 와 같은 방식. 시군구 CSV 는 data/rt_cache 에 3일 보관해 하루 다운로드 한도를 아낀다)
- 글: 카테고리 '청약 분석', 주소 /chungyak-<공고번호>/. 내용이 바뀐 공고만 다시 쓴다. 허브 페이지 /chungyak/ 에 최근 공고 목록.

  python -m ytblog chungyak [--dry-run] [--pages N] [--only 공고번호] [--draft]
"""
from __future__ import annotations

import hashlib
import html as _html
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0 Safari/537.36", "Accept-Language": "ko"}
LISTS = [("무순위", "https://www.applyhome.co.kr/ai/aia/selectAPTRemndrLttotPblancListView.do",
          "https://www.applyhome.co.kr/ai/aia/selectAPTRemndrLttotPblancDetailView.do"),
         ("일반분양", "https://www.applyhome.co.kr/ai/aia/selectAPTLttotPblancListView.do",
          "https://www.applyhome.co.kr/ai/aia/selectAPTLttotPblancDetail.do")]
CATEGORY = "청약 분석"
TYPE_RE = re.compile(r"^\d{3}\.\d{1,4}[A-Z]?$")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _e(s) -> str:
    return _html.escape(str(s or ""), quote=True)


def _strip(h: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]*>", "", h or ""))).strip()


def _tokens(h: str) -> list[str]:
    t = re.sub(r"<script[\s\S]*?</script>", "", h, flags=re.I)
    t = re.sub(r"<[^>]+>", "|", t)
    t = re.sub(r"[ \t\r\n]+", " ", _html.unescape(t))
    return [x.strip() for x in t.split("|") if x.strip()]


# ---------------------------------------------------------------- 목록
def fetch_list(pages: int = 1) -> list[dict]:
    rows = []
    for group, url, detail in LISTS:
        for page in range(1, pages + 1):
            try:
                r = (requests.get(url, headers=UA, timeout=30) if page == 1 else
                     requests.post(url, data={"pageIndex": page}, headers=UA, timeout=30))
                h = r.text
            except Exception as e:  # noqa: BLE001
                print(f"[warn] 목록 실패 {group} p{page}: {e}")
                break
            got = 0
            for m in re.finditer(r'<tr\s+data-pbno="([^"]*)"\s+data-hmno="([^"]*)"([^>]*)>([\s\S]*?)</tr>', h):
                tds = [_strip(t) for t in re.findall(r"<td[^>]*>([\s\S]*?)</td>", m.group(4))]
                attrs = m.group(3)
                honm = _strip((re.search(r'data-honm="([^"]*)"', attrs) or [None, ""])[1])
                if group == "무순위":
                    if len(tds) < 7:
                        continue
                    area, kind, name, period, win = tds[0], tds[1], honm or tds[2], tds[5], tds[6]
                else:
                    if len(tds) < 9:
                        continue
                    area, kind, name, period, win = tds[0], f"{tds[1]} {tds[2]}".strip(), honm or tds[3], tds[7], tds[8]
                pm = re.search(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})", period or "")
                rows.append({"pbno": m.group(1), "hmno": m.group(2), "group": group, "detail": detail, "area": area,
                             "kind": kind, "name": name, "applyStart": pm.group(1) if pm else "",
                             "applyEnd": pm.group(2) if pm else "", "winDate": win})
                got += 1
            if not got:
                break
    seen, out = set(), []
    for r in rows:
        if r["pbno"] not in seen:
            seen.add(r["pbno"]); out.append(r)
    return out


# ---------------------------------------------------------------- 상세
def fetch_detail(row: dict) -> dict:
    h = requests.post(row["detail"], data={"houseManageNo": row["hmno"], "pblancNo": row["pbno"]}, headers=UA, timeout=40).text
    tok = _tokens(h)
    if len(tok) < 30:
        raise RuntimeError("상세 화면을 못 읽음")

    def after(label: str, k: int = 1) -> str:
        for i, x in enumerate(tok):
            if x == label and i + k < len(tok):
                return tok[i + k]
        return ""

    d = {"addr": after("공급위치"), "scale": after("공급규모"), "phone": after("문의처").replace("☎", "").strip(),
         "noticeDate": (DATE_RE.search(after("모집공고일")) or [""])[0]}
    pdf = re.search(r"https?://static\.applyhome\.co\.kr/ai/aia/getAtchmnfl\.do\?[^\"'\s<>]+", h)
    d["pdf"] = _html.unescape(pdf.group(0)) if pdf else ""
    # 접수 일정
    sch, i = [], tok.index("청약접수") if "청약접수" in tok else -1
    if i >= 0:
        j = i + 1
        while j < len(tok) and not tok[j].startswith("당첨자 발표"):
            if tok[j] in ("특별공급", "1순위", "2순위", "일반공급", "무순위", "일반", "임의공급"):
                dates = []
                k = j + 1
                while k < len(tok) and DATE_RE.fullmatch(tok[k]):
                    dates.append(tok[k]); k += 1
                if dates:
                    sch.append({"label": tok[j], "dates": dates})
                j = k
            else:
                j += 1
    if not sch and i >= 0:
        rng = re.findall(r"\d{4}-\d{2}-\d{2}", " ".join(tok[i + 1:i + 3]))
        if rng:
            sch.append({"label": "청약접수", "dates": list(dict.fromkeys(rng))})
    d["schedule"] = sch
    if not re.search(r"\d{3,4}-\d{4}", d["phone"]):
        m2 = re.search(r"((?:\d{2,4}-)?\d{3,4}-\d{4})", " ".join(x for x in tok if "문의" in x or "☎" in x))
        d["phone"] = m2.group(1) if m2 else ""
    d["winDate"] = next((DATE_RE.search(tok[k + 1]).group(0) for k, x in enumerate(tok)
                         if x.startswith("당첨자 발표") and k + 1 < len(tok) and DATE_RE.search(tok[k + 1])), "")
    d["contract"] = after("계약일")
    d["special"] = next((x.replace("* 특이사항 :", "").replace("* 특이사항:", "").strip() for x in tok if x.startswith("* 특이사항")), "")
    # 주택형 표(공급대상)
    types: dict[str, dict] = {}
    try:
        s0 = next(k for k, x in enumerate(tok) if x.startswith("입주자모집공고 공급대상"))
        s1 = next(k for k in range(s0, len(tok)) if tok[k].startswith(("특별공급 공급대상", "공급내역", "공급금액")))
    except StopIteration:
        s0, s1 = 0, 0
    k = s0
    while k < s1:   # 주택형 | 공급면적 | 일반 | 특별 | 계 — 공급면적(예: 109.1804)도 주택형처럼 생겨서 읽은 칸은 건너뛴다
        if TYPE_RE.fullmatch(tok[k]):
            nums = []
            for x in tok[k + 1:k + 6]:
                if re.fullmatch(r"[\d,.]+", x):
                    nums.append(x)
                else:
                    break
            t = types.setdefault(tok[k], {"type": tok[k], "area": round(float(tok[k][:7]), 2)})
            if len(nums) >= 4:
                t["supply"] = float(nums[0]); t["general"], t["specialUnits"], t["units"] = (int(n.replace(",", "")) for n in nums[1:4])
            k += 1 + len(nums)
        else:
            k += 1
    # 공급금액·입주예정월
    # 공급금액 표는 형식이 셋: [주택형|금액|입주월] [주택형|금액|2순위 청약금] [주택형|공급세대수|분양가]
    p0 = next((k for k, x in enumerate(tok) if x.startswith("공급금액(단위")), len(tok))
    for k in range(p0, len(tok)):
        if not TYPE_RE.fullmatch(tok[k]) or (k > 0 and TYPE_RE.fullmatch(tok[k - 1])):
            continue
        nums, mv = [], ""
        for x in tok[k + 1:k + 4]:
            if re.fullmatch(r"\d{4}\.\d{2}", x):
                mv = x; break
            if not re.fullmatch(r"[\d,]+", x):
                break
            nums.append(int(x.replace(",", "")))
        big = [n for n in nums if n >= 10000]
        small = [n for n in nums if 0 < n < 5000]
        if not big and not small:
            continue
        t = types.setdefault(tok[k], {"type": tok[k], "area": round(float(tok[k][:7]), 2)})
        if big:
            t["price"] = max(big)
        if small and not t.get("units"):
            t["units"] = small[0]
        if mv:
            t["moveIn"] = mv
    mv = next((re.search(r"\d{4}\.\d{2}", x).group(0) for x in tok if "입주예정월" in x and re.search(r"\d{4}\.\d{2}", x)), "")
    d["moveIn"] = mv or next((t.get("moveIn") for t in types.values() if t.get("moveIn")), "")
    d["types"] = sorted(types.values(), key=lambda t: t["area"])
    try:
        k = tok.index("사업주체 전화번호")
        d["developer"], d["builder"] = tok[k + 1], tok[k + 2]
    except (ValueError, IndexError):
        d["developer"] = d["builder"] = ""
    return d


# ---------------------------------------------------------------- 실거래 시세
class RT:
    """국토부 실거래가 공개시스템 CSV. 시군구 단위로 받아 3일 캐시."""

    def __init__(self, cache_dir: Path):
        self.cache = cache_dir; self.cache.mkdir(parents=True, exist_ok=True)
        self.s = None; self.sido: dict[str, str] = {}; self.sgg: dict[str, dict] = {}; self.downloads = 0

    def _session(self):
        if self.s:
            return
        self.s = requests.Session(); self.s.headers.update(UA)
        self.s.get("https://rt.molit.go.kr/pt/xls/xls.do?mobileAt=", timeout=60)
        for x in self.s.post("https://rt.molit.go.kr/data/sido.do", data="", timeout=60).json():
            self.sido[x["ctprvnNm"]] = str(x["signguCode"])[:2]

    def trades(self, sido: str, sgg: str) -> list[dict]:
        key = re.sub(r"\s+", "_", f"{sido}_{sgg}")
        cp = self.cache / f"{key}.json"
        if cp.exists() and time.time() - cp.stat().st_mtime < 3 * 86400:
            return json.loads(cp.read_text(encoding="utf-8"))
        if self.downloads >= 25:
            raise RuntimeError("이번 실행의 실거래 다운로드 상한(25)")
        self._session()
        sc = self.sido.get(sido) or next((v for k, v in self.sido.items() if k[:2] == sido[:2]), None)
        if not sc:
            raise RuntimeError(f"시도 코드 없음: {sido}")
        if sc not in self.sgg:
            self.sgg[sc] = {g["signguNm"]: g["signguCode"] for g in
                            self.s.post("https://rt.molit.go.kr/data/sgg.do", data={"signguCode": sc}, timeout=60).json()}
        cands = [sgg, sgg.replace(" ", ""), sgg.replace("특례시", "시"), sgg.replace("특례시", "시").split(" ")[0], sgg.split(" ")[0]]
        sgcd, used = next(((self.sgg[sc][c], c) for c in cands if c in self.sgg[sc]), (None, sgg))
        if not sgcd:   # 행정구역이 새로 바뀐 경우(예: 화성특례시 만세구) — 시 이름이 들어간 첫 항목
            base = sgg.replace("특례시", "시").split(" ")[0]
            sgcd, used = next(((v, k) for k, v in self.sgg[sc].items() if k.startswith(base)), (None, sgg))
        if not sgcd:
            raise RuntimeError(f"시군구 코드 없음: {sido} {sgg}")
        sgg = used
        to, fr = date.today(), date.today() - timedelta(days=364)
        params = {"srhThingNo": "A", "srhDelngSecd": "1", "srhAddrGbn": "1", "srhLfstsSecd": "1", "sidoNm": sido,
                  "sggNm": sgg.split(" ")[-1], "emdNm": "", "loadNm": "", "areaNm": "", "hsmpNm": "", "mobileAt": "",
                  "srhFromDt": fr.isoformat(), "srhToDt": to.isoformat(), "srhNewRonSecd": "", "srhSidoCd": sc,
                  "srhSggCd": sgcd, "srhEmdCd": "", "srhRoadNm": "", "srhLoadCd": "", "srhHsmpCd": "", "srhArea": "",
                  "srhLrArea": "", "srhFromAmount": "", "srhToAmount": ""}
        ref = {"Referer": "https://rt.molit.go.kr/pt/xls/xls.do?mobileAt="}
        chk = self.s.post("https://rt.molit.go.kr/pt/xls/ptXlsDownCheck.do", data="", headers=ref, timeout=60).json() or {}
        if (chk.get("cnt") or 0) > 100:
            raise RuntimeError("실거래 자료 하루 다운로드 한도 도달")
        self.s.post("https://rt.molit.go.kr/pt/xls/ptXlsDownDataCheck.do", data=params, headers=ref, timeout=60)
        r = self.s.post("https://rt.molit.go.kr/pt/xls/ptXlsCSVDown.do", data=params, headers=ref, timeout=120)
        self.downloads += 1
        lines = r.content.decode("euc-kr", "ignore").splitlines()
        hi = next((k for k, l in enumerate(lines) if l.startswith('"NO"')), -1)
        if hi < 0:
            raise RuntimeError("실거래 CSV 형식이 바뀜")
        split = lambda l: re.findall(r'"([^"]*)"', l)  # noqa: E731
        H = split(lines[hi])
        col = lambda p: next((k for k, h in enumerate(H) if re.match(p, h)), -1)  # noqa: E731
        ci = {"sgg": col("시군구"), "name": col("단지명"), "area": col("전용면적"), "ym": col("계약년월"), "day": col("계약일"),
              "price": col("거래금액"), "built": col("건축년도"), "cancel": col(".*해제사유"), "kind": col("거래유형")}
        out = []
        for l in lines[hi + 1:]:
            v = split(l)
            if len(v) < len(H) - 2:
                continue
            if ci["cancel"] >= 0 and v[ci["cancel"]] not in ("", "-"):
                continue
            if ci["kind"] >= 0 and "직거래" in v[ci["kind"]]:
                continue
            try:
                price = int(v[ci["price"]].replace(",", ""))
            except ValueError:
                continue
            out.append({"dong": v[ci["sgg"]].split(" ")[-1], "name": v[ci["name"]], "area": float(v[ci["area"]] or 0),
                        "price": price, "ym": v[ci["ym"]], "day": v[ci["day"]], "built": int(v[ci["built"]] or 0)})
        cp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out


def parse_addr(addr: str) -> dict | None:
    a = re.sub(r"\s+", " ", addr or "").strip()
    m = re.match(r"^(\S+?(?:특별시|광역시|특별자치시|특별자치도|도))\s+(.+)$", a)
    if not m:
        return None
    sido, rest = m.group(1), m.group(2).split(" ")
    if sido.endswith("특별자치시"):            # 세종: 시군구 없음
        sgg, k = sido, 0
    else:
        sgg, k = rest[0], 1
        if sgg.endswith("시") and len(rest) > 1 and rest[1].endswith("구"):
            sgg, k = f"{rest[0]} {rest[1]}", 2
    dong = rest[k] if k < len(rest) else ""
    if not re.search(r"(동|가|리|읍|면)$", dong):
        p = re.search(r"\(([^,()]*?(동|가|리))[,)]", a)
        dong = p.group(1).strip() if p else ""
    return {"sido": sido, "sgg": sgg, "dong": re.sub(r"\d+$", "", dong)}


def _quantile(xs: list[int], q: float) -> int:
    a = sorted(xs)
    p = (len(a) - 1) * q
    lo, hi = int(p), min(int(p) + 1, len(a) - 1)
    return round(a[lo] + (a[hi] - a[lo]) * (p - lo))


def estimate(trades: list[dict], A: dict, area: float) -> dict | None:
    tol, yr = (5 if area < 70 else 6), date.today().year
    near = [t for t in trades if abs(t["area"] - area) <= tol]
    tiers = [(True, lambda t: t["dong"] == A["dong"] and t["built"] >= yr - 10, 0.5, f"{A['dong']} 10년 내 신축 거래 중앙값"),
             (True, lambda t: t["dong"] == A["dong"], 0.75, f"{A['dong']} 전체 거래 상위 25%"),
             (False, lambda t: t["built"] >= yr - 10, 0.5, f"{A['sgg']} 10년 내 신축 거래 중앙값")]
    for need_dong, f, q, label in tiers:
        if need_dong and not A.get("dong"):
            continue
        c = [t for t in near if f(t)]
        if len(c) < 3:
            continue
        ex = sorted(c, key=lambda t: t["ym"] + str(t["day"]).zfill(2), reverse=True)[:3]
        return {"market": _quantile([t["price"] for t in c], q), "n": len(c), "basis": f"{label} · 전용 {round(area)}㎡ ±{tol}㎡ · 최근 12개월",
                "ex": [{"name": t["name"], "area": round(t["area"], 1), "price": t["price"], "ym": t["ym"], "built": t["built"]} for t in ex]}
    return None


# ---------------------------------------------------------------- 계산
def won(man: int) -> str:
    """만원 → '8억 7,900만 원'."""
    man = int(round(man))
    eok, rest = divmod(abs(man), 10000)
    s = (f"{eok}억 " if eok else "") + (f"{rest:,}만" if rest else "")
    return ("-" if man < 0 else "") + s.strip() + " 원"


def acq_tax(price_man: int, area: float) -> int:
    """1주택(무주택자가 사는 경우) 취득세 + 지방교육세 + (85㎡ 초과면) 농특세. 만원."""
    eok = price_man / 10000
    r = 1.0 if eok <= 6 else (3.0 if eok > 9 else eok * 2 / 3 - 3)
    rate = r + r * 0.1 + (0.2 if area > 85 else 0)
    return round(price_man * rate / 100)


# ---------------------------------------------------------------- 글
SIDO_SHORT = {"서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "광주광역시": "광주", "대전광역시": "대전",
              "울산광역시": "울산", "세종특별자치시": "세종", "경기도": "경기", "강원도": "강원", "강원특별자치도": "강원", "충청북도": "충북",
              "충청남도": "충남", "전라북도": "전북", "전북특별자치도": "전북", "전라남도": "전남", "경상북도": "경북", "경상남도": "경남",
              "제주특별자치도": "제주"}


def tname(t: dict) -> str:
    m = re.search(r"([A-Z])$", t["type"])
    return f"{round(t['area'])}{m.group(1) if m else ''}"


def _region(addr: str, area: str) -> str:
    A = parse_addr(addr)
    if not A:
        return area
    short = SIDO_SHORT.get(A["sido"], A["sido"][:2])
    return f"{short} {A['sgg'].split(' ')[0]}" if A["sgg"] != A["sido"] else short


def build_post(row: dict, d: dict, info: dict | None = None) -> dict:
    name, kind, region = row["name"], row["kind"], _region(d["addr"], row["area"])
    all_types = d["types"]
    types = [t for t in all_types if t.get("price")]
    lotto = "불법행위 재공급" in kind
    md = lambda s: f"{int(s[5:7])}/{int(s[8:10])}" if s and len(s) >= 10 else ""  # noqa: E731
    title = f"{name} 분양가·시세 비교 ({region}, {md(row['applyStart'])} 접수)"
    P = lambda s, cls="": f'<!-- wp:paragraph{json.dumps({"className": cls}) if cls else ""} -->\n<p{f" class={chr(34)}{cls}{chr(34)}" if cls else ""}>{s}</p>\n<!-- /wp:paragraph -->'  # noqa: E731
    H2 = lambda s: f'<!-- wp:heading -->\n<h2 class="wp-block-heading">{_e(s)}</h2>\n<!-- /wp:heading -->'  # noqa: E731
    TABLE = lambda head, rows: ('<!-- wp:table {"className":"yt-cy"} -->\n<figure class="wp-block-table yt-cy"><table><thead><tr>'  # noqa: E731
                                + "".join(f"<th>{h}</th>" for h in head) + "</tr></thead><tbody>"
                                + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
                                + "</tbody></table></figure>\n<!-- /wp:table -->")
    parts = []
    # 도입(데이터로 만든 문장)
    big = max(types, key=lambda t: t.get("units", 0), default=None)
    intro = f"{_e(name)}은(는) {_e(d['addr'])}에 공급되는 {_e(d['scale'])} 규모 단지예요. 이번 공고는 <strong>{_e(kind)}</strong> 물량이고, 청약 접수는 {_e(row['applyStart'])}"
    intro += (f"~{_e(row['applyEnd'])}" if row["applyEnd"] and row["applyEnd"] != row["applyStart"] else "") + "예요."
    if big and big.get("market"):
        diff = big["market"] - big["price"]
        intro += (f" 가장 물량이 많은 전용 {round(big['area'])}㎡ 분양가는 최고 {won(big['price'])}으로, 주변 아파트 실거래로 추정한 시세 {won(big['market'])}보다 "
                  f"<strong>{won(abs(diff))} {'낮아요' if diff > 0 else '높아요'}</strong>.")
        if "신축" not in (big.get("mkt") or {}).get("basis", ""):
            intro += " 다만 주변에 비교할 만한 신축 거래가 적어 구축 위주로 추정했기 때문에, 새 아파트 시세는 이보다 높을 수 있어요."
    elif big:
        intro += f" 가장 물량이 많은 전용 {round(big['area'])}㎡ 분양가는 최고 {won(big['price'])}이에요."
    if lotto:
        intro += " 불법행위로 계약이 취소된 집을 다시 내놓는 물량이라 최초 분양 당시 가격 그대로 공급돼요."
    from .chungyak_site import fmt_m, walk
    site = info or {}
    geo = site.get("geo") or {}
    st, sc = geo.get("stations") or [], geo.get("schools") or []
    loc = []
    if site.get("units"):
        loc.append(f"단지 전체는 총 {site['units']:,}세대")
    if st:
        loc.append(f"가장 가까운 역은 {st[0]['name']}(약 {fmt_m(st[0]['m'])})")
    if sc:
        loc.append(f"가장 가까운 초등학교는 {sc[0]['name']}(약 {fmt_m(sc[0]['m'])})")
    if loc:
        intro += " " + ", ".join(loc) + "예요." if len(loc) > 1 else " " + loc[0] + "예요."
    live = (site.get("rules") or {}).get("live", "")
    if live and "해당지역" not in live:
        intro += f" 청약은 <strong>{_e(live)}</strong>만 할 수 있어요."
    duty = (site.get("rules") or {}).get("liveDuty", "")
    if duty and duty != "없음":
        intro += f" 당첨되면 {_e(duty)} 동안 직접 살아야 하는 거주 의무가 있어요."
    parts.append(P(intro))
    # 한눈에
    parts.append(H2("한눈에 보기"))
    sched = " / ".join(f"{s['label']} {' · '.join(dict.fromkeys(s['dates']))}" for s in d["schedule"]) or f"{row['applyStart']} ~ {row['applyEnd']}"
    info = [("위치", _e(d["addr"])), ("구분", _e(kind)), ("공급 규모", _e(d["scale"])), ("청약 접수", _e(sched)),
            ("당첨자 발표", _e(d["winDate"] or row["winDate"])), ("계약일", _e(d["contract"])),
            ("입주 예정", _e(d["moveIn"].replace(".", "년 ") + "월" if d["moveIn"] else "-")),
            ("시행 / 시공", _e(f"{d['developer']} / {d['builder']}")), ("문의", _e(d["phone"]))]
    if d["special"]:
        info.insert(3, ("규제", _e(d["special"])))
    units_txt = (f"총 {site.get('units'):,}세대" + (f" (이번 공급 {_e(d['scale'])})" if d["scale"] else "")) if site.get("units")         else f"모집공고문 확인 필요 (이번 공급 {_e(d['scale'])})"
    def near(lst, n):
        if not lst:
            if not geo:
                return "지도에서 확인 필요"
            r = (geo.get("radius") or {}).get("st" if lst is st else "sc", 2500)
            return f"반경 {r / 1000:g}km 안에 없음"
        if geo.get("approx"):
            return "<br>".join(f"{_e(x['name'])} 약 {fmt_m(x['m'])}" for x in lst[:n]) + "<br><small>(동 중심 기준 대략 거리)</small>"
        return "<br>".join(f"{_e(x['name'])} {fmt_m(x['m'])} · {walk(x['m'])}" for x in lst[:n])
    k = 3 if d["special"] else 2
    info[k + 1:k + 1] = [("단지 규모", units_txt), ("가까운 역", near(st, 2)), ("초등학교", near(sc, 1))]
    R = site.get("rules") or {}
    rule_rows = [(lab, _e(R[key])) for key, lab in (("live", "청약 자격(거주)"), ("liveDuty", "거주 의무"), ("resale", "전매 제한"),
                                                     ("rewin", "재당첨 제한"), ("cap", "분양가 상한제")) if R.get(key)]
    pos = next((n for n, (lab, _) in enumerate(info) if lab == "청약 접수"), 3)
    info[pos:pos] = rule_rows
    parts.append(TABLE(["항목", "내용"], [[f"<strong>{k}</strong>", v] for k, v in info]))
    # 주택형별
    if all_types:
        parts.append(H2("주택형별 분양가와 주변 시세"))
        rows = []
        for t in all_types:
            mk = t.get("market")
            diff = (mk - t["price"]) if (mk and t.get("price")) else None
            rows.append([f"{tname(t)}㎡",
                         f"{t.get('units', '-')}세대" + (f"<br><small>일반 {t.get('general')} · 특별 {t.get('specialUnits')}</small>" if t.get("general") is not None else ""),
                         won(t["price"]) if t.get("price") else "<small>공고문 확인</small>",
                         won(mk) if mk else "<small>추정 불가</small>",
                         (f"<strong class='{'up' if diff > 0 else 'down'}'>{'+' if diff > 0 else '−'}{won(abs(diff))}</strong>" if diff is not None else "-")])
        parts.append(TABLE(["전용", "공급", "분양가(최고가)", "주변 시세", "시세 − 분양가"], rows))
        parts.append(P("분양가는 같은 주택형 중 가장 비싼 층·호 기준이에요. 시세 추정은 아래 근거로 계산한 참고값이라 실제 거래가와 다를 수 있어요."
                       + ("" if types else " 이 공고는 분양가가 청약홈에 공개되지 않아 모집공고문이나 사업주체에 확인해야 해요."), "yt-note"))
    if types:
        # 돈 계산
        parts.append(H2("얼마가 필요할까"))
        rows = [[f"{tname(t)}㎡", won(t["price"] * 0.1), won(t["price"] * 0.2), won(acq_tax(t["price"], t["area"]))] for t in types]
        parts.append(TABLE(["전용", "계약금 10%", "계약금 20%", "취득세(추정)"], rows))
        parts.append(P("보통 분양가는 계약금 10~20%, 중도금 60%(대부분 대출), 잔금 20~30%로 나눠 내요. 당첨 직후 계약일에 계약금이 필요하고, 취득세는 입주 잔금 때 내요. "
                       "취득세는 무주택자가 한 채를 사는 경우의 세율(교육세 포함, 85㎡ 초과는 농특세 포함)로 계산했어요. 실제 비율과 대출 조건은 모집공고문을 꼭 확인하세요.", "yt-note"))
    if all_types:
        # 근거
        exs = [t for t in all_types if t.get("mkt")]
        if exs:
            parts.append(H2("시세 추정 근거"))
            items, seen = [], set()
            for t in exs:          # 같은 면적대(A·B·C…)는 근거가 같으니 한 번만
                m = t["mkt"]
                key = (round(t["area"]), m.get("basis"))
                if key in seen:
                    continue
                seen.add(key)
                ex = ", ".join(f"{_e(x['name'])} {x['area']}㎡ {won(x['price'])}({x['ym'][:4]}.{x['ym'][4:]}, {x['built']}년식)" for x in m.get("ex", []))
                items.append(f"<li><strong>전용 {round(t['area'])}㎡</strong> 시세 {won(t['market'])}: {_e(m['basis'])} · 비교 거래 {m['n']}건" + (f"<br><small>최근 예: {ex}</small>" if ex else "") + "</li>")
            parts.append(f'<!-- wp:list -->\n<ul class="wp-block-list">{"".join(items)}</ul>\n<!-- /wp:list -->')
    # 체크포인트
    tips = []
    if lotto:
        tips.append("불법행위 재공급은 해당 지역 무주택 세대 구성원이면 청약할 수 있는 경우가 많지만, 세부 자격은 공고마다 달라요.")
    if "무순위" in kind or "임의공급" in kind or "사후" in kind:
        tips.append("무순위·임의공급은 청약통장이 필요 없는 경우가 많고, 당첨돼도 재당첨 제한이 붙을 수 있어요.")
    if d["special"]:
        tips.append(f"이 단지는 {_e(d['special'])}이라 전매 제한·재당첨 제한·대출 한도가 일반 지역보다 엄격해요.")
    if d["moveIn"]:
        try:
            y, mth = d["moveIn"].split(".")
            months = (int(y) - date.today().year) * 12 + int(mth) - date.today().month
            if months > 0:
                tips.append(f"입주까지 약 {months // 12}년 {months % 12}개월 남았어요. 그동안 중도금 대출 이자(또는 후불제 여부)를 확인하세요.")
        except ValueError:
            pass
    if geo.get("approx"):
        tips.append("가까운 역·학교 거리는 정확한 단지 위치 대신 동 중심에서 잰 대략적인 직선거리예요. 실제 거리는 지도에서 확인하세요.")
    elif geo:
        tips.append("역·학교 거리는 단지 주소에서 잰 직선거리이고, 걷는 시간은 길 굴곡을 감안한 추정이에요.")
    tips.append("최종 자격·분양가·납부 일정은 반드시 모집공고문에서 확인하세요.")
    parts.append(H2("체크포인트"))
    parts.append(f'<!-- wp:list -->\n<ul class="wp-block-list">{"".join(f"<li>{t}</li>" for t in tips)}</ul>\n<!-- /wp:list -->')
    src = '청약홈 입주자모집공고' + (f' (<a href="{_e(d["pdf"])}" rel="nofollow noopener" target="_blank">모집공고문 PDF</a>)' if d["pdf"] else "") + ", 국토교통부 실거래가 공개시스템" + (f", 위치 {geo.get('src')}" if geo else "")
    parts.append('<!-- wp:separator -->\n<hr class="wp-block-separator has-alpha-channel-opacity"/>\n<!-- /wp:separator -->')
    parts.append(P(f"<strong>출처</strong> {src} · 자동 정리 {date.today().isoformat()}", "yt-source"))
    content = "\n\n".join(parts)
    excerpt = re.sub(r"<[^>]+>", "", intro)[:150]
    A = parse_addr(d["addr"]) or {}
    tags = [t for t in dict.fromkeys([name, f"{region} 청약", kind.split(" ")[0], (A.get("dong") or ""), "청약 일정", "분양가"]) if t]
    return {"title": title, "content": content, "excerpt": excerpt, "tags": tags, "slug": f"chungyak-{row['pbno']}"}


CY_CSS = ("/* yt-cy */.yt-cy table{font-size:.92em}.yt-cy td,.yt-cy th{padding:.55em .6em;vertical-align:top}.yt-cy th{background:#f5f3ee}"
          ".yt-cy strong.up{color:#15803d}.yt-cy strong.down{color:#b91c1c}.yt-cy small{opacity:.7}"
          ".yt-note{font-size:.9em;opacity:.8}@media (max-width:600px){.yt-cy table{font-size:.82em}.yt-cy td,.yt-cy th{padding:.45em .35em}}")


def ensure_css(wp) -> None:
    th = wp._get("themes", status="active")[0]
    gid = th["_links"]["wp:user-global-styles"][0]["href"].rstrip("/").split("/")[-1]
    g = wp._get(f"global-styles/{gid}", context="edit"); styles = g.get("styles") or {}
    css = styles.get("css") or ""
    if "/* yt-cy */" not in css:
        styles["css"] = (css + "\n" + CY_CSS).strip()
        wp._post(f"global-styles/{gid}", styles=styles)


# ---------------------------------------------------------------- 실행
def run(settings, dry_run: bool = False, pages: int = 1, only: str = "", draft: bool = False, log=print) -> dict:
    from .wordpress import WordPressClient
    sp = settings.data_dir / "chungyak_state.json"
    state = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {"posts": {}}
    rt = RT(settings.data_dir / "rt_cache")
    wp = None if dry_run else WordPressClient(settings.wp_url, settings.wp_user, settings.wp_app_password)
    if wp:
        ensure_css(wp)
    rows = fetch_list(pages)
    if only:
        rows = [r for r in rows if r["pbno"] == only]
    today = date.today().isoformat()
    made, updated, skipped, urls = [], [], [], []
    for r in rows:
        try:
            d = fetch_detail(r)
            A = parse_addr(d["addr"])
            if A and d["types"]:
                try:
                    tr = rt.trades(A["sido"], A["sgg"])
                    for t in d["types"]:
                        est = estimate(tr, A, t["area"])
                        if est:
                            t["market"] = est["market"]; t["mkt"] = {k: est[k] for k in ("n", "basis", "ex")}
                except Exception as e:  # noqa: BLE001
                    log(f"  시세 실패({r['name']}): {str(e)[:80]}")
            from .chungyak_site import site_info
            site = site_info(r["pbno"], d["addr"], r["name"], d["pdf"], settings.data_dir / "chungyak_site")
            post = build_post(r, d, site)
            h = hashlib.md5(post["content"].split("자동 정리")[0].encode()).hexdigest()
            st = state["posts"].get(r["pbno"], {})
            if dry_run:
                log(f"[dry] {post['title']}  주택형 {len(d['types'])} · 시세 {sum(1 for t in d['types'] if t.get('market'))}")
                (settings.out_dir / "chungyak").mkdir(parents=True, exist_ok=True)
                (settings.out_dir / "chungyak" / f"{r['pbno']}.json").write_text(json.dumps({"row": r, "detail": d, "post": post}, ensure_ascii=False, indent=1), encoding="utf-8")
                continue
            status = "draft" if draft else "publish"
            if st.get("hash") == h and st.get("status") == status:
                skipped.append(r["name"]); continue
            cats = wp.category_ids([CATEGORY])
            tags = wp.tag_ids(post["tags"])
            if st.get("id"):
                p = wp.update_post(st["id"], title=post["title"], content=post["content"], excerpt=post["excerpt"], tags=tags,
                                   status=status, categories=cats, slug=post["slug"])
                updated.append(p.get("link", ""))
            else:
                p = wp.create_post(title=post["title"], content=post["content"], status=status, slug=post["slug"],
                                   excerpt=post["excerpt"], categories=cats, tags=tags,
                                   meta={"_yoast_wpseo_metadesc": post["excerpt"]})
                made.append(p.get("link", ""))
            state["posts"][r["pbno"]] = {"id": p["id"], "hash": h, "link": p.get("link", ""), "name": r["name"], "region": _region(d["addr"], r["area"]),
                                         "kind": r["kind"], "applyStart": r["applyStart"], "applyEnd": r["applyEnd"], "winDate": d["winDate"] or r["winDate"],
                                         "status": status, "at": time.time()}
            if status == "publish":
                urls.append(p.get("link", ""))
            sp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
            time.sleep(1)
        except Exception as e:  # noqa: BLE001
            log(f"[warn] {r['name']}: {str(e)[:120]}")
    if wp and not draft:
        try:
            hub = update_hub(settings, wp, state)
            urls.append(hub)
        except Exception as e:  # noqa: BLE001
            log(f"[warn] 허브 갱신 실패: {str(e)[:100]}")
        if urls:
            try:
                from .seo import submit
                log(f"IndexNow: {submit(settings, urls, wp)}")
            except Exception:  # noqa: BLE001
                pass
    return {"new": made, "updated": updated, "same": len(skipped), "rt_downloads": rt.downloads}


def update_hub(settings, wp, state: dict) -> str:
    """/chungyak/ 허브: 접수 예정·진행 중 공고와 최근 마감 공고 목록."""
    today = date.today().isoformat()
    items = [v for v in state["posts"].values() if v.get("status") == "publish"]
    up = sorted([v for v in items if (v.get("applyEnd") or v.get("applyStart") or "") >= today], key=lambda v: v.get("applyStart") or "")
    past = sorted([v for v in items if (v.get("applyEnd") or v.get("applyStart") or "") < today], key=lambda v: v.get("applyStart") or "", reverse=True)[:30]
    li = lambda v: f'<li><a href="{_e(v["link"])}">{_e(v["name"])}</a> <small>· {_e(v.get("region",""))} · {_e(v["kind"])} · 접수 {_e(v.get("applyStart",""))}{"~" + _e(v["applyEnd"][5:]) if v.get("applyEnd") and v["applyEnd"] != v.get("applyStart") else ""}</small></li>'  # noqa: E731
    content = (f'<!-- wp:paragraph -->\n<p>청약홈에 올라온 아파트 청약 공고를 매일 정리해요. 공고마다 분양가와 주변 실거래 시세, 계약금·취득세를 한 페이지에 모았어요. (갱신 {datetime.now().strftime("%Y-%m-%d %H:%M")})</p>\n<!-- /wp:paragraph -->\n\n'
               f'<!-- wp:heading -->\n<h2 class="wp-block-heading">접수 예정·진행 중 ({len(up)})</h2>\n<!-- /wp:heading -->\n\n'
               f'<!-- wp:list -->\n<ul class="wp-block-list">{"".join(li(v) for v in up) or "<li>지금은 접수 예정인 공고가 없어요.</li>"}</ul>\n<!-- /wp:list -->\n\n'
               f'<!-- wp:heading -->\n<h2 class="wp-block-heading">최근 마감</h2>\n<!-- /wp:heading -->\n\n'
               f'<!-- wp:list -->\n<ul class="wp-block-list">{"".join(li(v) for v in past) or "<li>-</li>"}</ul>\n<!-- /wp:list -->')
    pages = wp._get("pages", slug="chungyak", status="publish,draft")
    if pages:
        p = wp.s.post(f"{settings.wp_url}/wp-json/wp/v2/pages/{pages[0]['id']}", json={"content": content}, timeout=60).json()
    else:
        p = wp.s.post(f"{settings.wp_url}/wp-json/wp/v2/pages", json={"title": "청약 일정·분양가 정리", "slug": "chungyak", "status": "publish", "content": content}, timeout=60).json()
    return p.get("link", "")
