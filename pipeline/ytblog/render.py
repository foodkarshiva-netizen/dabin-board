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
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", note or "") if p.strip()]
    body = "".join(f"<p>{_e(p)}</p>" for p in paras) or "<p></p>"
    notice = f"<p class='yt-note-draft'><em>[{_e(DRAFT_NOTICE)}]</em></p>" if is_draft else ""
    return f"{NOTE_START}<h2>편집자 메모</h2><div class='yt-note'>{notice}{body}</div>{NOTE_END}"


def replace_note_block(html_text: str, note: str, is_draft: bool = False) -> str:
    """기존 글 본문의 편집자 메모 블록을 새 메모로 바꾼다. 블록이 없으면 출처 고지 앞에 넣는다."""
    block = build_note_block(note, is_draft)
    if NOTE_RE.search(html_text):
        return NOTE_RE.sub(lambda _m: block, html_text)
    marker = "<hr/><div class='yt-source'>"
    if marker in html_text:
        return html_text.replace(marker, block + "\n" + marker, 1)
    return html_text + "\n" + block


def figure(img: CardImage, url_map: dict[str, str]) -> str:
    src = url_map.get(str(img.path), str(img.path))
    return f"<figure class='wp-block-image size-full'><img src='{_e(src)}' alt='{_e(img.alt)}' loading='lazy'/></figure>"


def build_post_html(summary: Summary, meta: VideoMeta, cards: list[CardImage],
                    url_map: dict[str, str], blog_name: str,
                    editor_note: str = "", note_is_draft: bool = False,
                    show_timestamps: bool = False, embed_video: bool = False, source_link: bool = False) -> str:
    """학습 노트 구조의 본문. editor_note 가 비어 있으면 요약의 editor_note_draft 를 AI 초안 표시와 함께 넣는다."""
    vid = meta.video_id
    syn = summary.synthesis
    by_kind = {c.kind: c for c in cards if c.section_index < 0 and c.kind not in ("glossary",)}
    section_cards = {c.section_index: c for c in cards if c.kind == "section"}
    glossary_cards = [c for c in cards if c.kind == "glossary"]
    parts: list[str] = []

    def ts(t: str) -> str:
        return " " + ts_link(vid, t) if show_timestamps else ""

    def txt(t: str) -> str:
        return linkify(t, vid) if show_timestamps else _e(strip_ts(t))

    # 한 줄 요약 + 도입
    parts.append(f"<p class='yt-oneliner'><strong>{_e(syn.one_liner)}</strong></p>")
    parts.append(f"<p class='yt-intro'>{_e(syn.intro)}</p>")

    if embed_video:
        parts.append(
            "<figure class='wp-block-embed is-type-video'><div class='wp-block-embed__wrapper' style='position:relative;padding-top:56.25%'>"
            f"<iframe style='position:absolute;inset:0;width:100%;height:100%' src='https://www.youtube.com/embed/{vid}' "
            "title='원본 영상' frameborder='0' allow='accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture' "
            "allowfullscreen></iframe></div></figure>")

    # 핵심 포인트
    parts.append("<h2>핵심 포인트</h2>")
    if "takeaways" in by_kind:
        parts.append(figure(by_kind["takeaways"], url_map))
    parts.append("<ol>" + "".join(f"<li>{_e(t.text)}{ts(t.ts)}</li>" for t in syn.key_takeaways) + "</ol>")

    # 목차
    parts.append("<h2>이 글의 순서</h2><ol class='yt-toc'>")
    for i, s in enumerate(summary.sections):
        rng = f" <small>({_e(s.start)}~{_e(s.end)})</small>" if show_timestamps else ""
        parts.append(f"<li><a href='#sec-{i + 1}'>{_e(s.title)}</a>{rng}</li>")
    parts.append("</ol>")

    # 구간별 핵심
    for i, s in enumerate(summary.sections):
        head = f"{i + 1}. {_e(s.title)}"
        if show_timestamps:
            head += f" <small>{ts_link(vid, s.start, s.start + ' 부터')}</small>"
        parts.append(f"<h2 id='sec-{i + 1}'>{head}</h2>")
        if i in section_cards:
            parts.append(figure(section_cards[i], url_map))
        parts.append(f"<p>{_e(strip_ts(s.summary))}</p>")
        if s.details:
            parts.append("<ul>" + "".join(f"<li>{txt(d)}</li>" for d in s.details) + "</ul>")
        if s.numbers:
            parts.append("<p class='yt-nums'><strong>기억할 숫자</strong> · " + " · ".join(
                f"{_e(n.label)} <strong>{_e(n.value)}</strong>{ts(n.ts)}" for n in s.numbers) + "</p>")

    if "numbers" in by_kind:
        parts.append("<h2>숫자로 기억하기</h2>")
        parts.append(figure(by_kind["numbers"], url_map))

    # 정리
    parts.append("<h2>정리와 시사점</h2>")
    parts.append(f"<p>{_e(syn.conclusion)}</p>")

    if syn.background:
        parts.append("<h2>참고: 배경 설명</h2>")
        parts.append(f"<p><em>아래는 영상에 나오지 않는, 이해를 돕기 위한 배경 설명입니다.</em></p><p>{_e(syn.background)}</p>")

    if syn.glossary:
        parts.append("<h2>핵심 용어</h2>")
        for c in glossary_cards:
            parts.append(figure(c, url_map))
        parts.append("<dl class='yt-glossary'>" + "".join(
            f"<dt><strong>{_e(g.term)}</strong></dt><dd>{_e(g.definition)}</dd>" for g in syn.glossary) + "</dl>")

    qs = getattr(syn, "study_questions", []) or []
    if qs:
        parts.append("<h2>스스로 점검해 보기</h2>")
        if "questions" in by_kind:
            parts.append(figure(by_kind["questions"], url_map))
        parts.append("<ol>" + "".join(f"<li>{_e(q)}</li>" for q in qs) + "</ol>")

    if syn.faq:
        parts.append("<h2>자주 묻는 질문</h2>")
        for f in syn.faq:
            parts.append(f"<p><strong>Q. {_e(f.q)}</strong><br/>A. {_e(f.a)}</p>")

    # 편집자 메모 (사람이 쓴 메모 > AI 초안)
    note = editor_note.strip() if editor_note else ""
    if not note:
        note, note_is_draft = getattr(syn, "editor_note_draft", "") or "", True
    if note:
        parts.append(build_note_block(note, note_is_draft))

    # 출처 고지 (기본은 링크 없는 텍스트)
    if source_link:
        who = (f"유튜브 채널 <a href='https://www.youtube.com/channel/{_e(meta.channel_id)}' target='_blank' rel='noopener'>{_e(meta.channel_title)}</a>의 "
               f"영상 <a href='{_e(meta.url)}' target='_blank' rel='noopener'>“{_e(meta.title)}”</a>")
    else:
        who = f"유튜브 채널 {_e(meta.channel_title)}의 영상 “{_e(meta.title)}”"
    parts.append(
        "<hr/><div class='yt-source'><p><strong>출처</strong></p>"
        f"<p>이 글은 {who}의 내용을 학습 목적으로 요약·재구성한 것입니다. "
        "영상의 모든 권리는 원 제작자에게 있으며, 이 글의 이미지는 영상 화면을 사용하지 않고 요약 내용을 바탕으로 새로 제작한 것입니다. "
        f"게시 중단 요청은 {_e(blog_name)} 문의 채널로 보내주시면 즉시 처리합니다.</p></div>"
    )
    return "\n".join(parts)


def build_excerpt(summary: Summary) -> str:
    return summary.synthesis.seo.description or summary.synthesis.one_liner
