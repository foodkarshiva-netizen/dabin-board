"""썸네일(대표 이미지) 생성: 글마다 색·무늬·구도·아이콘이 달라진다.

고정 요소(브랜드): 카테고리 알약, '5분 정리' 표시, 굵은 후킹 문구, 하단 블로그 이름.
변하는 요소: 팔레트 9종 × 무늬 5종 × 구도 3종 × 아이콘 14종. 기본은 영상 ID 해시로 고르고,
summary.synthesis 의 thumb_palette / thumb_layout / hook_icon 으로 직접 지정할 수 있다.
PEXELS_API_KEY 가 있고 hook_photo(검색어)가 있으면 무료 사진을 배경으로 쓴다(어두운 그라데이션을 얹음).
"""
from __future__ import annotations

import base64
import hashlib
import html
import os
import re

import requests

PALETTES = {
    # name: (bg1, bg2, fg, accent, chip_bg, chip_fg, soft)
    "ink":    ("#0f172a", "#334155", "#ffffff", "#fbbf24", "#fbbf24", "#111827", "rgba(255,255,255,.12)"),
    "forest": ("#052e2b", "#1f6f66", "#ffffff", "#bef264", "#bef264", "#052e2b", "rgba(255,255,255,.12)"),
    "cocoa":  ("#2a1a10", "#6b4423", "#ffffff", "#fcd34d", "#fcd34d", "#2a1a10", "rgba(255,255,255,.12)"),
    "plum":   ("#1f1433", "#55378a", "#ffffff", "#f9a8d4", "#f9a8d4", "#1f1433", "rgba(255,255,255,.12)"),
    "terra":  ("#3a1408", "#9a3c18", "#ffffff", "#fde68a", "#fde68a", "#3a1408", "rgba(255,255,255,.12)"),
    "olive":  ("#1a2209", "#566b20", "#ffffff", "#fef08a", "#fef08a", "#1a2209", "rgba(255,255,255,.12)"),
    "wine":   ("#2b0b12", "#7f1d1d", "#ffffff", "#fdba74", "#fdba74", "#2b0b12", "rgba(255,255,255,.12)"),
    "cream":  ("#f5f3ee", "#e7e2d6", "#1f2937", "#b45309", "#1f2937", "#fbbf24", "rgba(31,41,55,.07)"),
    "amber":  ("#fbbf24", "#f59e0b", "#1f2937", "#7c2d12", "#1f2937", "#fbbf24", "rgba(31,41,55,.10)"),
}
PATTERNS = ["dots", "grid", "stripes", "rings", "plain"]
LAYOUTS = ["split", "bignum", "band"]

# 300x300 선 아이콘 (stroke=currentColor)
_S = "fill='none' stroke='currentColor' stroke-width='14' stroke-linecap='round' stroke-linejoin='round'"
ICONS = {
    "chart": f"<polyline points='30,230 110,150 170,190 270,70' {_S}/><polyline points='200,70 270,70 270,140' {_S}/><line x1='30' y1='270' x2='270' y2='270' {_S}/>",
    "coin": f"<ellipse cx='150' cy='90' rx='100' ry='40' {_S}/><path d='M50 90v60c0 22 45 40 100 40s100-18 100-40V90' {_S}/><path d='M50 150v60c0 22 45 40 100 40s100-18 100-40v-60' {_S}/>",
    "globe": f"<circle cx='150' cy='150' r='115' {_S}/><ellipse cx='150' cy='150' rx='50' ry='115' {_S}/><line x1='35' y1='150' x2='265' y2='150' {_S}/>",
    "bulb": f"<path d='M150 30a85 85 0 0 0-50 153v37h100v-37a85 85 0 0 0-50-153z' {_S}/><line x1='110' y1='255' x2='190' y2='255' {_S}/><line x1='125' y1='285' x2='175' y2='285' {_S}/>",
    "book": f"<path d='M150 80c-30-25-70-30-115-25v175c45-5 85 0 115 25 30-25 70-30 115-25V55c-45-5-85 0-115 25z' {_S}/><line x1='150' y1='80' x2='150' y2='255' {_S}/>",
    "atom": f"<circle cx='150' cy='150' r='16' fill='currentColor'/><ellipse cx='150' cy='150' rx='120' ry='45' {_S}/><ellipse cx='150' cy='150' rx='120' ry='45' transform='rotate(60 150 150)' {_S}/><ellipse cx='150' cy='150' rx='120' ry='45' transform='rotate(120 150 150)' {_S}/>",
    "pulse": f"<path d='M150 255C40 180 30 110 75 70c35-30 65-5 75 20 10-25 40-50 75-20 45 40 35 110-75 185z' {_S}/><polyline points='60,160 115,160 135,120 165,200 185,160 240,160' {_S}/>",
    "clock": f"<circle cx='150' cy='150' r='115' {_S}/><polyline points='150,80 150,150 205,180' {_S}/>",
    "gear": f"<circle cx='150' cy='150' r='48' {_S}/><path d='M150 35v40M150 225v40M35 150h40M225 150h40M69 69l28 28M203 203l28 28M231 69l-28 28M97 203l-28 28' {_S}/><circle cx='150' cy='150' r='95' {_S}/>",
    "chat": f"<path d='M40 60h170a20 20 0 0 1 20 20v85a20 20 0 0 1-20 20H120l-50 40v-40H40a20 20 0 0 1-20-20V80a20 20 0 0 1 20-20z' {_S}/><path d='M255 120h5a20 20 0 0 1 20 20v75a20 20 0 0 1-20 20h-15v35l-42-35h-40' {_S}/>",
    "target": f"<circle cx='140' cy='160' r='110' {_S}/><circle cx='140' cy='160' r='62' {_S}/><circle cx='140' cy='160' r='16' fill='currentColor'/><polyline points='140,160 270,30' {_S}/><polyline points='225,30 270,30 270,75' {_S}/>",
    "mountain": f"<polyline points='20,260 115,95 165,180 205,120 280,260 20,260' {_S}/><polyline points='115,95 115,40 165,58 115,76' {_S}/>",
    "robot": f"<rect x='55' y='90' width='190' height='150' rx='30' {_S}/><circle cx='110' cy='160' r='14' fill='currentColor'/><circle cx='190' cy='160' r='14' fill='currentColor'/><line x1='115' y1='205' x2='185' y2='205' {_S}/><line x1='150' y1='90' x2='150' y2='45' {_S}/><circle cx='150' cy='35' r='12' fill='currentColor'/>",
    "scale": f"<line x1='150' y1='40' x2='150' y2='260' {_S}/><line x1='60' y1='85' x2='240' y2='85' {_S}/><path d='M60 85l-40 95h80z' {_S}/><path d='M240 85l-40 95h80z' {_S}/><line x1='95' y1='260' x2='205' y2='260' {_S}/>",
}
CATEGORY_ICONS = {"경제·투자": ["chart", "coin", "scale", "target"], "건강·의학": ["pulse", "clock", "target"],
                  "역사·인문": ["book", "globe", "mountain"], "과학·기술": ["atom", "robot", "gear", "bulb"],
                  "심리·자기계발": ["bulb", "mountain", "target", "chat"], "사회·문화": ["chat", "globe", "scale"]}

THUMB_CSS = """
.card.tbcard{padding:0;background:#111}
.tb{position:absolute;inset:0;padding:60px 72px;display:flex;flex-direction:column;justify-content:space-between;color:var(--fg);
    background:linear-gradient(135deg,var(--bg1) 0%,var(--bg2) 100%);overflow:hidden}
.tb.photo{background-size:cover;background-position:center}
.tb.photo::after{content:'';position:absolute;inset:0;background:linear-gradient(90deg,rgba(10,12,16,.92) 0%,rgba(10,12,16,.72) 55%,rgba(10,12,16,.35) 100%)}
.tb>*{position:relative;z-index:2}
.tb::before{content:'';position:absolute;inset:0;z-index:1;opacity:.9}
.tb.p-dots::before{background-image:radial-gradient(var(--soft) 3px,transparent 3px);background-size:38px 38px}
.tb.p-grid::before{background-image:linear-gradient(var(--soft) 2px,transparent 2px),linear-gradient(90deg,var(--soft) 2px,transparent 2px);background-size:64px 64px}
.tb.p-stripes::before{background-image:repeating-linear-gradient(135deg,var(--soft) 0 18px,transparent 18px 60px)}
.tb.p-rings::before{background-image:radial-gradient(circle at 88% 18%,transparent 0 150px,var(--soft) 150px 170px,transparent 170px 260px,var(--soft) 260px 280px,transparent 280px 380px,var(--soft) 380px 400px,transparent 400px)}
.tb-top{display:flex;justify-content:space-between;align-items:center}
.tb-cat{background:var(--chipbg);color:var(--chipfg);font-size:30px;font-weight:700;padding:10px 24px;border-radius:999px}
.tb-min{font-size:28px;font-weight:700;border:3px solid var(--fg);opacity:.75;padding:7px 22px;border-radius:999px}
.tb-foot{font-size:26px;opacity:.6}
.tb-hook{font-weight:700;line-height:1.2;letter-spacing:-2px;word-break:keep-all}
.tb-hook .hl{color:var(--ac)}
.tb-hook.xl{font-size:110px}.tb-hook.lg{font-size:92px}.tb-hook.md{font-size:76px}
.tb-ico{color:var(--ac);opacity:.9}
/* split: 문구 왼쪽, 숫자 카드(또는 아이콘) 오른쪽 */
.l-split .tb-body{display:flex;align-items:center;gap:44px;flex:1;margin-top:16px}
.l-split .tb-hook{flex:1}
.l-split .tb-stat{flex:0 0 350px;background:var(--soft);border:3px solid var(--soft);border-radius:32px;padding:30px 22px;text-align:center}
.l-split .tb-sv{font-size:88px;font-weight:700;color:var(--ac);line-height:1.1;letter-spacing:-2px;word-break:keep-all}
.l-split .tb-sv.sm{font-size:62px}
.l-split .tb-sl{font-size:27px;opacity:.85;margin-top:12px;line-height:1.35;word-break:keep-all}
.l-split .tb-ico{flex:0 0 300px}
/* bignum: 숫자를 가장 크게, 문구는 아래 */
.l-bignum .tb-body{flex:1;display:flex;flex-direction:column;justify-content:center;margin-top:10px}
.l-bignum .tb-sv{font-size:170px;font-weight:700;color:var(--ac);line-height:1;letter-spacing:-5px}
.l-bignum .tb-sv.sm{font-size:120px}
.l-bignum .tb-sl{font-size:30px;opacity:.85;margin-top:6px}
.l-bignum .tb-hook{margin-top:26px;max-width:900px}
.l-bignum .tb-hook.xl{font-size:84px}.l-bignum .tb-hook.lg{font-size:72px}.l-bignum .tb-hook.md{font-size:62px}
.l-bignum .tb-ico{position:absolute;right:60px;bottom:90px;width:320px;height:320px;opacity:.35;z-index:1}
/* band: 문구 위, 아래 강조 띠에 숫자 */
.l-band .tb-body{flex:1;display:flex;flex-direction:column;justify-content:center;margin-top:10px}
.l-band .tb-hook{max-width:860px}
.l-band .tb-band{margin-top:30px;display:inline-flex;align-items:baseline;gap:22px;background:var(--ac);color:var(--bg1);padding:16px 30px;border-radius:22px;align-self:flex-start}
.l-band .tb-sv{font-size:72px;font-weight:700;letter-spacing:-2px;line-height:1.1}
.l-band .tb-sv.sm{font-size:56px}
.l-band .tb-sl{font-size:28px;font-weight:700}
.l-band .tb-ico{position:absolute;right:64px;top:170px;width:250px;height:250px;opacity:.3;z-index:1}
"""


def _e(s) -> str:
    return html.escape(str(s or ""))


def _hl(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", lambda m: f"<span class='hl'>{m.group(1)}</span>", _e(text))


def _pick(seed: str, salt: str, options: list):
    h = int(hashlib.sha1((seed + "|" + salt).encode("utf-8")).hexdigest(), 16)
    return options[h % len(options)]


def _photo_data_uri(query: str) -> str:
    key = os.environ.get("PEXELS_API_KEY", "")
    if not (key and query):
        return ""
    try:
        r = requests.get("https://api.pexels.com/v1/search", headers={"Authorization": key},
                         params={"query": query, "orientation": "landscape", "per_page": 5}, timeout=30)
        photos = r.json().get("photos") or []
        if not photos:
            return ""
        img = requests.get(photos[0]["src"]["large2x"], timeout=60).content
        return "data:image/jpeg;base64," + base64.b64encode(img).decode()
    except Exception:  # noqa: BLE001
        return ""


def thumb_html(summary, used_palettes: list[str] | None = None) -> tuple[str, str]:
    """(.card 안에 들어갈 HTML, 카드 클래스). used_palettes 에 최근 쓴 팔레트를 주면 그것들을 피한다."""
    syn = summary.synthesis
    vid = summary.video_id
    hook = (getattr(syn, "hook", "") or "").strip() or (syn.seo.title or "")
    val = (getattr(syn, "hook_value", "") or "").strip()
    lab = (getattr(syn, "hook_label", "") or "").strip()
    cat = (getattr(syn, "category", "") or "").strip()

    pal_names = list(PALETTES.keys())
    pal = getattr(syn, "thumb_palette", "") or ""
    if pal not in PALETTES:
        avoid = set((used_palettes or [])[:3])
        cands = [p for p in pal_names if p not in avoid] or pal_names
        pal = _pick(vid, "pal", cands)
    layout = getattr(syn, "thumb_layout", "") or ""
    if layout not in LAYOUTS:
        layout = _pick(vid, "layout", LAYOUTS if val else ["split", "band"])
    if not val and layout == "bignum":
        layout = "split"
    pattern = _pick(vid, "pattern", PATTERNS)
    icon = getattr(syn, "hook_icon", "") or ""
    if icon not in ICONS:
        icon = _pick(vid, "icon", CATEGORY_ICONS.get(cat) or list(ICONS.keys()))

    bg1, bg2, fg, ac, chipbg, chipfg, soft = PALETTES[pal]
    style = f"--bg1:{bg1};--bg2:{bg2};--fg:{fg};--ac:{ac};--chipbg:{chipbg};--chipfg:{chipfg};--soft:{soft};"
    photo = _photo_data_uri(getattr(syn, "hook_photo", "") or "")
    cls_photo = ""
    if photo:
        style += f"background-image:url({photo});--fg:#ffffff;--soft:rgba(255,255,255,.14);"
        cls_photo = " photo"
        pattern = "plain"

    n = len(hook.replace("*", ""))
    size = "xl" if n <= 16 else ("lg" if n <= 26 else "md")
    sv_cls = "tb-sv sm" if len(val) > 7 else "tb-sv"
    ico = f"<svg class='tb-ico' viewBox='0 0 300 300' xmlns='http://www.w3.org/2000/svg'>{ICONS[icon]}</svg>"
    hook_html = f"<div class='tb-hook {size}'>{_hl(hook)}</div>"

    if layout == "split":
        right = (f"<div class='tb-stat'><div class='{sv_cls}'>{_e(val)}</div><div class='tb-sl'>{_e(lab)}</div></div>" if val else ico)
        body = f"<div class='tb-body'>{hook_html}{right}</div>"
    elif layout == "bignum":
        body = f"<div class='tb-body'><div class='{sv_cls}'>{_e(val)}</div><div class='tb-sl'>{_e(lab)}</div>{hook_html}</div>{ico}"
    else:  # band
        band = (f"<div class='tb-band'><span class='{sv_cls}'>{_e(val)}</span><span class='tb-sl'>{_e(lab)}</span></div>" if val else "")
        body = f"<div class='tb-body'>{hook_html}{band}</div>{ico}"

    inner = (f"<div class='tb l-{layout} p-{pattern}{cls_photo}' style=\"{style}\">"
             f"<div class='tb-top'><span class='tb-cat'>{_e(cat or '핵심 정리')}</span><span class='tb-min'>5분 정리</span></div>"
             f"{body}<div class='tb-foot'>지식채우기 · jisikfill.com</div></div>")
    return inner, "tbcard", pal
