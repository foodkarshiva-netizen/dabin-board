"""청약 공고 입지 정보: 단지 총 세대수(모집공고문 PDF), 가까운 지하철역·초등학교.

- 총 세대수: 청약홈 상세의 '공급규모'는 이번 공고 물량이라, 모집공고문 PDF 첫 몇 쪽에서 "총 N세대"를 읽는다(Git 의 pdftotext).
- 위치: KAKAO_REST_KEY 가 .env 에 있으면 카카오 로컬 API(지번 주소 → 좌표, 주변 지하철역·초등학교 거리순)로 정확하게.
  없으면 오픈스트리트맵(Nominatim 으로 동 중심 좌표 → Overpass 로 주변 역·학교)으로 대략 찾는다(approx=True).
결과는 data/chungyak_site/<공고번호>.json 에 저장해 다시 묻지 않는다(위치 7일, 세대수는 영구).
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import requests

PDFTOTEXT = next((p for p in ["C:/Program Files/Git/mingw64/bin/pdftotext.exe", "C:/Program Files (x86)/Git/mingw64/bin/pdftotext.exe"]
                  if Path(p).exists()), "")
OSM_UA = {"User-Agent": "jisikfill-chungyak/1.0 (+https://jisikfill.com/chungyak/)"}


# ---------------------------------------------------------------- 총 세대수
def pdf_text(pdf_url: str) -> str:
    if not (pdf_url and PDFTOTEXT):
        return ""
    r = requests.get(pdf_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=90)
    if r.content[:4] != b"%PDF":
        return ""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(r.content); tmp = f.name
    try:
        txt = subprocess.run([PDFTOTEXT, "-enc", "UTF-8", "-l", "14", tmp, "-"], capture_output=True, timeout=120).stdout.decode("utf-8", "ignore")
    finally:
        Path(tmp).unlink(missing_ok=True)
    return re.sub(r"\s+", " ", txt)


def units_from(flat: str) -> tuple[int, str]:
    if not flat:
        return 0, "모집공고문을 못 읽음"
    for p in [r"공급\s*규모[^■]{0,240}?총\s*([\d,]+)\s*세대", r"건립\s*세대\s*수?\s*[:：]?\s*(?:총\s*)?([\d,]+)\s*세대",
              r"총\s*([\d,]+)\s*세대\s*\(", r"총\s*([\d,]+)\s*세대"]:
        m = re.search(p, flat)
        if m:
            n = int(m.group(1).replace(",", ""))
            if 0 < n < 100000:
                return n, ""
    return 0, ("모집공고문이 이미지라 글자를 못 읽음" if len(flat) < 200 else "모집공고문에서 총 세대수 못 찾음")


_STOP = r"(?=\s(?:분양가상한제|재당첨제한|전매제한|거주의무기간|규제지역여부|택지유형|구분|당첨자발표일|입주자모집공고일|특별공급|일반공급|서류접수|계약체결|기타지역|구글플레이스토어|\(분양문의\)|1 공통)|$)"


def rules_from(flat: str) -> dict:
    """모집공고문 첫머리 '단지 주요정보' 표에서 거주요건·거주의무·전매·재당첨·규제지역을 읽는다(표가 글자로 흩어져 있어 패턴 여러 개)."""
    i = flat.find("단지 주요정보")
    seg = flat[i:i + 1500] if i >= 0 else flat[:1500]
    out = {}
    m = re.search(r"거주요건\s*(.+?)" + _STOP, seg)
    if m:
        out["live"] = m.group(1).strip()
    else:
        h = re.search(r"해당지역\s*(?:민영|국민)?\s*(.+?거주자(?:\s*\([^)]*\))?)", seg)
        o = re.search(r"기타지역\s*(.+?거주자)", seg)
        if h:
            out["live"] = "해당지역: " + h.group(1).strip() + (f" / 기타지역: {o.group(1).strip()}" if o else "")
    m = re.search(r"재당첨제한\s*전매제한\s*(없음|\d+\s*년)\s*(없음|\d+\s*년|\d+\s*개월)", seg)
    if m:
        out["rewin"], out["resale"] = m.group(1), m.group(2)
    else:
        m = re.search(r"재당첨제한\s*(없음|\d+\s*년)", seg)
        if m:
            out["rewin"] = m.group(1)
        m = re.search(r"전매제한\s*(없음(?:\s*\([^)]{0,60}\))?|최초[^■]{0,40}?로부터\s*\d+\s*(?:년|개월)간?(?:\s*적용)?|\d+\s*(?:년|개월))", seg)
        if m:
            out["resale"] = m.group(1).strip()
    m = re.search(r"거주의무기간\s*분양가상한제\s*(없음|\d+\s*년(?:\s*\d+\s*개월)?)\s*(적용|미적용)", seg)
    if m:
        out["liveDuty"], out["cap"] = m.group(1), m.group(2)
    else:
        m = re.search(r"거주의무기간\s*(없음|\d+\s*년(?:\s*\d+\s*개월)?|\d+\s*개월)", seg)
        if m:
            out["liveDuty"] = m.group(1)
        m = re.search(r"분양가상한제\s*(적용|미적용)", seg)
        if m:
            out["cap"] = m.group(1)
    m = re.search(r"규제지역여부\s*(비규제지역|투기과열지구(?:\s*[및/]\s*청약과열지역)?|청약과열지역|조정대상지역)", seg)
    if m:
        out["zone"] = m.group(1)
    return out


def total_units(pdf_url: str) -> tuple[int, str]:
    return units_from(pdf_text(pdf_url))


# ---------------------------------------------------------------- 거리
def _dist(a, b) -> int:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return round(6371000 * 2 * math.asin(math.sqrt(h)))


def _clean_addr(addr: str) -> str:
    a = re.sub(r"\(.*?\)", " ", addr or "")
    a = re.sub(r"(번지)?\s*(일원|일대|외\s*\d+필지).*$", "", a)
    return re.sub(r"\s+", " ", a.replace("번지", "")).strip()


# ---------------------------------------------------------------- 카카오
def _kakao(addr: str, name: str, key: str) -> dict | None:
    H = {"Authorization": f"KakaoAK {key}"}
    xy = None
    for q in (_clean_addr(addr), name):
        if not q:
            continue
        r = requests.get("https://dapi.kakao.com/v2/local/search/address.json", params={"query": q}, headers=H, timeout=30)
        docs = r.json().get("documents", []) if r.ok else []
        if not docs:
            r = requests.get("https://dapi.kakao.com/v2/local/search/keyword.json", params={"query": q}, headers=H, timeout=30)
            docs = r.json().get("documents", []) if r.ok else []
        if docs:
            xy = (float(docs[0]["y"]), float(docs[0]["x"])); break
    if not xy:
        return None

    def near(params):
        radius = params.pop("_r", 2000)
        r = requests.get("https://dapi.kakao.com/v2/local/search/" + params.pop("_path"), params={**params, "x": xy[1], "y": xy[0],
                         "radius": radius, "sort": "distance"}, headers=H, timeout=30)
        return r.json().get("documents", []) if r.ok else []
    st = [{"name": d["place_name"], "m": int(d["distance"])} for d in near({"_path": "category.json", "category_group_code": "SW8", "_r": 3000})]
    sc = [{"name": d["place_name"], "m": int(d["distance"])} for d in near({"_path": "keyword.json", "query": "초등학교", "category_group_code": "SC4"})
          if d["place_name"].endswith("초등학교")]
    return {"lat": xy[0], "lon": xy[1], "stations": st[:3], "schools": sc[:2], "approx": False, "src": "카카오맵", "radius": {"st": 3000, "sc": 2000}}


# ---------------------------------------------------------------- 오픈스트리트맵(대략)
def _osm(addr: str) -> dict | None:
    a = _clean_addr(addr)
    xy = None
    parts = a.split(" ")
    for k in range(len(parts), 1, -1):          # 지번 → 동 → 시군구 순으로 줄여 가며
        q = " ".join(parts[:k])
        try:
            r = requests.get("https://nominatim.openstreetmap.org/search", params={"q": q, "format": "json", "limit": 1, "countrycodes": "kr"},
                             headers=OSM_UA, timeout=30)
            time.sleep(1.1)                      # 사용 정책: 초당 1회
            j = r.json()
        except Exception:  # noqa: BLE001
            j = []
        if j:
            xy = (float(j[0]["lat"]), float(j[0]["lon"])); break
    if not xy:
        return None
    lat, lon = xy
    q = (f"[out:json][timeout:30];(node(around:2500,{lat},{lon})[railway=station];"
         f"nwr(around:2500,{lat},{lon})[amenity=school][name~\"초등학교$\"];);out center tags;")
    els = []
    for attempt in range(4):         # 공용 서버라 자주 '잠시 후 다시' 를 준다 → 간격을 늘려 가며 재시도
        url = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")[attempt % 2]
        try:
            r = requests.post(url, data={"data": q}, headers=OSM_UA, timeout=60)
            if r.ok and r.text.lstrip().startswith("{"):
                els = r.json().get("elements", []); break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(8 * (attempt + 1))
    time.sleep(2)
    if not els:
        return None          # 지도 서버 오류일 수 있으니 '없음'으로 저장하지 않고 다음에 다시
    st, sc, seen = [], [], set()
    for e in els:
        t = e.get("tags", {})
        nm = t.get("name") or ""
        c = (e.get("lat") or e.get("center", {}).get("lat"), e.get("lon") or e.get("center", {}).get("lon"))
        if not nm or not c[0] or nm in seen:
            continue
        seen.add(nm)
        d = _dist(xy, c)
        if t.get("railway") == "station":
            st.append({"name": nm if nm.endswith("역") else nm + "역", "m": d, "line": t.get("line") or ""})
        elif t.get("amenity") == "school":
            sc.append({"name": nm, "m": d})
    st.sort(key=lambda x: x["m"]); sc.sort(key=lambda x: x["m"])
    return {"lat": lat, "lon": lon, "stations": st[:3], "schools": sc[:2], "approx": True, "src": "오픈스트리트맵"}


# ---------------------------------------------------------------- 묶음
def site_info(pbno: str, addr: str, name: str, pdf: str, cache_dir: Path) -> dict:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cp = cache_dir / f"{pbno}.json"
    info = json.loads(cp.read_text(encoding="utf-8")) if cp.exists() else {}
    if not info.get("rulesChecked"):
        try:
            flat = pdf_text(pdf)
            info["units"], info["unitsWhy"] = units_from(flat)
            info["rules"] = rules_from(flat) if flat else {}
        except Exception as e:  # noqa: BLE001
            info["units"], info["unitsWhy"], info["rules"] = 0, f"모집공고문을 못 받음({str(e)[:40]})", {}
        info["unitsChecked"] = info["rulesChecked"] = True
    key = os.environ.get("KAKAO_REST_KEY", "").strip()
    stale = time.time() - info.get("geoAt", 0) > 7 * 86400
    need_upgrade = key and (info.get("geo") or {}).get("approx", True)
    if stale or need_upgrade or "geo" not in info:
        g = None
        try:
            g = _kakao(addr, name, key) if key else None
        except Exception:  # noqa: BLE001
            g = None
        if g is None:
            try:
                g = _osm(addr)
            except Exception:  # noqa: BLE001
                g = None
        info["geo"] = g
        info["geoAt"] = time.time() if g else 0
    cp.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return info


def walk(m: int) -> str:
    """직선거리 → 걸어서 몇 분(길 굴곡 1.3배, 분당 75m)."""
    mins = max(1, round(m * 1.3 / 75))
    return f"걸어서 약 {mins}분" if mins <= 30 else "걷기엔 멂"


def fmt_m(m: int) -> str:
    return f"{m / 1000:.1f}km" if m >= 1000 else f"{round(m, -1)}m"
