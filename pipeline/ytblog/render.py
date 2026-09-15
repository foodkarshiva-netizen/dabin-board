"""요약 JSON + 카드 이미지 → WordPress 본문 HTML (학습 노트 구조).

기본값은 타임스탬프·임베드·원본 링크 없이, 영상 내용을 요약해 핵심을 전달하는 글이다.
채널 설정(show_timestamps / embed_video / source_link)으로 다시 켤 수 있다.
"""
from __future__ import annotations

import html
import re

from .discover import VideoMeta, parse_timestamp
from .images import CardImage
from .schema import Summary

TS_RE = re.compile(r"\[((?:\d{1,2}:)?\d{1,2}:\d{2})\]")
TS_STRIP_RE = re.compile(r"\s*\[(?:\d{1,2}:)?\d{1,2}:\d{2}\]")
NOTE_START = "<!-- ytblog:note:start -->"
NOTE_END = "<!-- ytblog:note:end -->"
NOTE_RE = re.compile(re.escape(NOTE_START) + r".*?" + re.escape(NOTE_END), re.S)
DRAFT_NOTICE = "이 메모는 AI가 쓴 초안입니다. 발행 전에 운영자의 의견으로 바꿔 주세요."


def _e(s: str) -> str:
    return html.escape(s or "")


END_PUNCT = tuple(".!?…」』)”\"'")


def ensure_period(text: str) -> str:
    """문장 끝에 마침표가 없으면 붙인다. 이미 문장부호로 끝나면 그대로."""
    t = (text or "").strip()
    if not t or t.endswith(END_PUNCT):
        return t
    return t + "."


def strip_ts(text: str) -> str:
    """문장 속 [mm:ss] 표기를 제거한다."""
    return TS_STRIP_RE.sub("", text or "").strip()


def ts_link(video_id: str, ts: str, label: str | None = None) -> str:
    sec = parse_timestamp(ts)
    return (f"<a class='yt-ts' href='https://www.youtube.com/watch?v={video_id}&t={sec}s' "
            f"target='_blank' rel='noopener'>{_e(label or ts)}</a>")


def linkify(text: str, video_id: str) -> str:
    """문장 끝의 [mm:ss] 를 영상 시점 링크로 바꾼다."""
    return TS_RE.sub(lambda m: ts_link(video_id, m.group(1)), _e(text))


def build_note_block(note: str, is_draft: bool) -> str:
    """편집자 메모 블록. is_draft=True 면 AI 초안이라는 표시를 붙인다(발행 전 교체 유도)."""
    paras = [ensure_period(p) for p in re.split(r"\n\s*\n|\n", note or "") if p.strip()]
    body = "\n".join(P(_e(p)) for p in paras) or P("")
    notice = P(f"<em>[{_e(DRAFT_NOTICE)}]</em>", "yt-note-draft") if is_draft else ""
    return f"{NOTE_START}\n{H2('편집자 메모')}\n{notice}\n{body}\n{NOTE_END}"


def replace_note_block(html_text: str, note: str, is_draft: bool = False) -> str:
    """기존 글 본문의 편집자 메모 블록을 새 메모로 바꾼다. 블록이 없으면 출처 고지 앞에 넣는다."""
    block = build_note_block(note, is_draft)
    if NOTE_RE.search(html_text):
        return NOTE_RE.sub(lambda _m: block, html_text)
    marker = "<!-- wp:separator -->"
    if marker in html_text:
        return html_text.replace(marker, block + "\n" + marker, 1)
    return html_text + "\n" + block


def figure(img: CardImage, url_map: dict[str, str], id_map: dict[str, int] | None = None) -> str:
    """이미지 블록. 블록 주석(<!-- wp:image -->)이 있어야 워드프레스가 이미지 블록 CSS(max-width:100%)를 싣는다."""
    src = url_map.get(str(img.path), str(img.path))
    mid = (id_map or {}).get(str(img.path), 0)
    attrs = f'{{"id":{mid},"sizeSlug":"full","linkDestination":"none"}}' if mid else '{"sizeSlug":"full","linkDestination":"none"}'
    cls = f" wp-image-{mid}" if mid else ""
    return (f"<!-- wp:image {attrs} -->\n<figure class=\"wp-block-image size-full\">"
            f"<img src=\"{_e(src)}\" alt=\"{_e(img.alt)}\" class=\"{cls.strip()}\" loading=\"lazy\"/></figure>\n<!-- /wp:image -->")


def P(inner: str, cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    return f"<!-- wp:paragraph -->\n<p{c}>{inner}</p>\n<!-- /wp:paragraph -->"


def H2(inner: str, anchor: str = "") -> str:
    a = f' id="{anchor}"' if anchor else ""
    return f"<!-- wp:heading -->\n<h2 class=\"wp-block-heading\"{a}>{inner}</h2>\n<!-- /wp:heading -->"


def LIST(items: list[str], ordered: bool = False, cls: str = "") -> str:
    tag = "ol" if ordered else "ul"
    attr = ' {"ordered":true}' if ordered else ""
    c = f' class="wp-block-list {cls}"'.replace("  ", " ") if cls else ' class="wp-block-list"'
    lis = "".join(f"<!-- wp:list-item -->\n<li>{it}</li>\n<!-- /wp:list-item -->" for it in items)
    return f"<!-- wp:list{attr} -->\n<{tag}{c}>{lis}</{tag}>\n<!-- /wp:list -->"


def build_post_html(summary: Summary, meta: VideoMeta, cards: list[CardImage],
                    url_map: dict[str, str], blog_name: str, id_map: dict[str, int] | None = None,
                    editor_note: str = "", note_is_draft: bool = False,
                    show_timestamps: bool = False, embed_video: bool = False, source_link: bool = False,
                    include_toc: bool = False, include_glossary: bool = False, include_questions: bool = False,
                    include_faq: bool = False, include_numbers: bool = True, max_details: int = 4,
                    note_auto: bool = True, include_details: bool = False) -> str:
    """학습 노트 구조의 본문. editor_note 가 비어 있으면 요약의 editor_note_draft 를 AI 초안 표시와 함께 넣는다."""
    vid = meta.video_id
    syn = summary.synthesis
    by_kind = {c.kind: c for c in cards if c.section_index < 0 and c.kind not in ("glossary", "diagram")}
    section_cards: dict[int, list[CardImage]] = {}
    for c in cards:
        if c.kind in ("section", "diagram") and c.section_index >= 0:
            section_cards.setdefault(c.section_index, []).append(c)
    top_diagrams = [c for c in cards if c.kind == "diagram" and c.section_index < 0]
    glossary_cards = [c for c in cards if c.kind == "glossary"]
    parts: list[str] = []

    def ts(t: str) -> str:
        return " " + ts_link(vid, t) if show_timestamps else ""

    def txt(t: str) -> str:
        return linkify(ensure_period(t), vid) if show_timestamps else _e(ensure_period(strip_ts(t)))

    fig = lambda c: figure(c, url_map, id_map)  # noqa: E731

    # 한 줄 요약 + 도입
    parts.append(P(f"<strong>{_e(ensure_period(syn.one_liner))}</strong>", "yt-oneliner"))
    parts.append(P(_e(ensure_period(syn.intro)), "yt-intro"))

    if embed_video:
        parts.append(
            f"<!-- wp:embed {{\"url\":\"https://www.youtube.com/watch?v={vid}\",\"type\":\"video\",\"providerNameSlug\":\"youtube\",\"responsive\":true}} -->\n"
            f"<figure class=\"wp-block-embed is-type-video is-provider-youtube wp-block-embed-youtube\"><div class=\"wp-block-embed__wrapper\">\n"
            f"https://www.youtube.com/watch?v={vid}\n</div></figure>\n<!-- /wp:embed -->")

    # 핵심 포인트
    parts.append(H2("핵심 포인트"))
    if "takeaways" in by_kind:
        parts.append(fig(by_kind["takeaways"]))
    parts.append(LIST([f"{_e(ensure_period(t.text))}{ts(t.ts)}" for t in syn.key_takeaways], ordered=True))
    for c in top_diagrams:
        parts.append(fig(c))

    # 목차 (옵션)
    if include_toc:
        parts.append(H2("이 글의 순서"))
        items = []
        for i, s in enumerate(summary.sections):
            rng = f" <small>({_e(s.start)}~{_e(s.end)})</small>" if show_timestamps else ""
            items.append(f"<a href=\"#sec-{i + 1}\">{_e(s.title)}</a>{rng}")
        parts.append(LIST(items, ordered=True, cls="yt-toc"))

    # 구간별 핵심
    for i, s in enumerate(summary.sections):
        head = f"{i + 1}. {_e(s.title)}"
        if show_timestamps:
            head += f" <small>{ts_link(vid, s.start, s.start + ' 부터')}</small>"
        parts.append(H2(head, anchor=f"sec-{i + 1}"))
        for c in section_cards.get(i, []):
            parts.append(fig(c))
        parts.append(P(_e(ensure_period(strip_ts(s.summary)))))
        if include_details and s.details:
            parts.append(LIST([txt(d) for d in s.details[:max_details]]))
        if s.numbers and show_timestamps:
            parts.append(P("<strong>기억할 숫자</strong> · " + " · ".join(
                f"{_e(n.label)} <strong>{_e(n.value)}</strong>{ts(n.ts)}" for n in s.numbers), "yt-nums"))

    if include_numbers and "numbers" in by_kind:
        parts.append(H2("숫자로 기억하기"))
        parts.append(fig(by_kind["numbers"]))

    # 정리
    parts.append(H2("정리와 시사점"))
    parts.append(P(_e(ensure_period(syn.conclusion))))

    if syn.background:
        parts.append(H2("참고: 배경 설명"))
        parts.append(P("<em>아래는 영상에 나오지 않는, 이해를 돕기 위한 배경 설명이에요.</em>"))
        parts.append(P(_e(ensure_period(syn.background))))

    if include_glossary and syn.glossary:
        parts.append(H2("핵심 용어"))
        for c in glossary_cards:
            parts.append(fig(c))
        parts.append(LIST([f"<strong>{_e(g.term)}</strong>: {_e(ensure_period(g.definition))}" for g in syn.glossary], cls="yt-glossary"))

    qs = (getattr(syn, "study_questions", []) or []) if include_questions else []
    if qs:
        parts.append(H2("스스로 점검해 보기"))
        if "questions" in by_kind:
            parts.append(fig(by_kind["questions"]))
        parts.append(LIST([_e(q) for q in qs], ordered=True))

    if include_faq and syn.faq:
        parts.append(H2("자주 묻는 질문"))
        for f in syn.faq:
            parts.append(P(f"<strong>Q. {_e(f.q)}</strong><br>A. {_e(ensure_period(f.a))}"))

    # 편집자 메모 (사람이 쓴 메모 > AI 초안)
    note = editor_note.strip() if editor_note else ""
    if not note:
        note, note_is_draft = getattr(syn, "editor_note_draft", "") or "", not note_auto
    if note:
        parts.append(build_note_block(note, note_is_draft))

    # 댓글 유도 질문
    dq = getattr(syn, "discussion_question", "") or ""
    if dq:
        parts.append(H2("여러분 생각은요?"))
        parts.append(P(_e(ensure_period(dq)) + " 댓글로 편하게 남겨 주세요.", "yt-question"))

    # 출처: 한 줄만 정확히 (기본은 링크 없는 텍스트)
    if source_link:
        who = (f"유튜브 채널 <a href=\"https://www.youtube.com/channel/{_e(meta.channel_id)}\" target=\"_blank\" rel=\"noopener\">{_e(meta.channel_title)}</a> · "
               f"<a href=\"{_e(meta.url)}\" target=\"_blank\" rel=\"noopener\">“{_e(meta.title)}”</a>")
    else:
        who = f"유튜브 채널 {_e(meta.channel_title)} · “{_e(meta.title)}”"
    parts.append("<!-- wp:separator -->\n<hr class=\"wp-block-separator has-alpha-channel-opacity\"/>\n<!-- /wp:separator -->")
    parts.append(P(f"<strong>출처</strong> {who}", "yt-source"))
    return "\n\n".join(parts)


def build_excerpt(summary: Summary) -> str:
    return summary.synthesis.seo.description or summary.synthesis.one_liner
