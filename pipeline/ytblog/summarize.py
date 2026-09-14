"""4패스 요약: 구간 분할 → 구간별 상세 → 종합 → 검증.

- 자막 전체는 system 블록에 넣고 prompt caching 으로 한 번만 과금되게 한다.
- 모든 항목은 타임스탬프를 동반하도록 스키마/프롬프트로 강제한다.
- MOCK_LLM=1 이면 API 호출 없이 결정적인 가짜 결과를 만든다(오프라인 테스트용).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Type, TypeVar

from pydantic import BaseModel

from .config import ChannelConfig, Settings
from .discover import VideoMeta, parse_timestamp
from .schema import (FAQItem, GlossaryItem, Number, Quote, Section, SectionPlan,
                     SectionPlanItem, SEO, Summary, Synthesis, Takeaway, Verification)
from .transcript import Transcript, fmt_ts

T = TypeVar("T", bound=BaseModel)

# USD per 1M tokens: (input, output). 캐시 읽기 0.1x, 캐시 쓰기 1.25x
PRICES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

SYSTEM_RULES = """당신은 유튜브 영상 내용을 블로그 독자에게 정확하고 상세하게 전달하는 한국어 에디터입니다.

규칙
- 자막에 없는 내용은 절대 추가하지 않습니다. 추측이 필요하면 "영상에서 명시하지 않음"이라고 씁니다.
- 모든 항목에는 근거가 되는 자막 타임스탬프를 붙입니다. 형식은 mm:ss 또는 h:mm:ss 입니다.
- 자막은 자동 생성일 수 있어 고유명사 오탈자가 있습니다. 제목/설명/키워드에 있는 표기를 우선합니다.
- 화자의 말을 그대로 옮기지 말고, 독자가 영상을 보지 않아도 이해할 수 있게 재구성합니다.
- 존댓말, 명확한 설명체를 씁니다. 과장·낚시성 표현을 쓰지 않습니다.
- 결과는 지정된 JSON 스키마로만 출력합니다.
"""


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost_usd: float = 0.0
    log: list[str] = field(default_factory=list)

    def add(self, model: str, u) -> None:
        inp, out = PRICES.get(model, (5.0, 25.0))
        i = getattr(u, "input_tokens", 0) or 0
        o = getattr(u, "output_tokens", 0) or 0
        cr = getattr(u, "cache_read_input_tokens", 0) or 0
        cw = getattr(u, "cache_creation_input_tokens", 0) or 0
        cost = (i * inp + cr * inp * 0.1 + cw * inp * 1.25 + o * out) / 1e6
        self.calls += 1
        self.input_tokens += i
        self.output_tokens += o
        self.cache_read += cr
        self.cache_write += cw
        self.cost_usd += cost
        self.log.append(f"{model}: in={i} cache_read={cr} cache_write={cw} out={o} ${cost:.4f}")


class ClaudeLLM:
    def __init__(self, api_key: str):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key or None)
        self.usage = Usage()

    def parse(self, model: str, system_blocks: list[dict], user_text: str,
              output_format: Type[T], max_tokens: int = 16000) -> T:
        resp = self.client.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=[{"role": "user", "content": user_text}],
            output_format=output_format,
        )
        self.usage.add(model, resp.usage)
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"모델이 응답을 거부했습니다: {getattr(resp, 'stop_details', None)}")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("출력이 max_tokens 에서 잘렸습니다. max_tokens 를 늘리세요.")
        if resp.parsed_output is None:
            raise RuntimeError("구조화 출력 파싱 실패")
        return resp.parsed_output


class MockLLM:
    """오프라인 테스트용. 자막 텍스트로 그럴듯한 결과를 결정적으로 생성한다."""

    def __init__(self):
        self.usage = Usage()

    def parse(self, model, system_blocks, user_text, output_format, max_tokens=16000):
        self.usage.calls += 1
        transcript = ""
        for b in system_blocks:
            if "<transcript>" in b.get("text", ""):
                transcript = b["text"].split("<transcript>")[1].split("</transcript>")[0].strip()
        lines = [ln for ln in transcript.splitlines() if ln.strip()]

        def ts_of(line: str) -> str:
            m = re.match(r"\[([\d:]+)\]", line)
            return m.group(1) if m else "00:00"

        def body(line: str) -> str:
            return re.sub(r"^\[[\d:]+\]\s*", "", line)

        if output_format is SectionPlan:
            n = max(1, min(4, len(lines) // 4))
            step = max(1, len(lines) // n)
            items = []
            for i in range(n):
                s = lines[i * step]
                e = lines[min(len(lines) - 1, (i + 1) * step - 1)]
                items.append(SectionPlanItem(title=f"구간 {i+1}: {body(s)[:18]}", start=ts_of(s), end=ts_of(e)))
            return SectionPlan(sections=items)

        if output_format is Section:
            m = re.search(r"구간 제목: (.+?)\n시작: ([\d:]+)\n끝: ([\d:]+)", user_text)
            title, start, end = (m.group(1), m.group(2), m.group(3)) if m else ("구간", "00:00", "00:30")
            s0, e0 = parse_timestamp(start), parse_timestamp(end)
            sub = [ln for ln in lines if s0 <= parse_timestamp(ts_of(ln)) <= e0] or lines[:3]
            details = [f"{body(ln)[:80]} [{ts_of(ln)}]" for ln in sub[:5]]
            return Section(
                title=title, start=start, end=end,
                summary=" ".join(body(ln) for ln in sub[:2])[:300],
                details=details,
                quotes=[Quote(text=body(sub[0])[:60], ts=ts_of(sub[0]))],
                numbers=[Number(label=w, value=w, ts=ts_of(ln))
                         for ln in sub for w in re.findall(r"\d+[%년개명배]", body(ln))][:3],
                image_caption=body(sub[0])[:40],
            )

        if output_format is Synthesis:
            first = body(lines[0])[:60] if lines else "영상 요약"
            return Synthesis(
                one_liner=f"{first}에 대한 핵심을 정리한 영상입니다.",
                intro="이 글은 영상의 주요 내용을 구간별로 정리한 것입니다. 각 항목의 시간을 누르면 해당 장면으로 이동합니다.",
                key_takeaways=[Takeaway(text=body(ln)[:70], ts=ts_of(ln)) for ln in lines[::max(1, len(lines)//3)][:3]],
                conclusion="영상은 위 내용을 근거로 실천 가능한 제안을 제시하며 마무리됩니다.",
                glossary=[GlossaryItem(term="예시 용어", definition="모의 실행에서 생성된 용어 설명입니다.")],
                faq=[FAQItem(q="이 영상의 핵심은 무엇인가요?", a=first)],
                background="",
                editor_note_draft="(초안) 이 영상의 주장을 실제로 적용해 본다면 어떤 점부터 바꿀지 적어 보세요.",
                study_questions=["이 영상의 핵심 주장은 무엇인가요?", "그 주장의 근거는 무엇인가요?"],
                seo=SEO(title=f"{first} 정리", slug="mock-summary", description=first,
                        tags=["유튜브 요약", "모의"]),
            )

        if output_format is Verification:
            from .schema import ClaimVerdict
            ids = re.findall(r"^\[(c\d+)\]", user_text, flags=re.M)
            return Verification(verdicts=[ClaimVerdict(claim_id=i, supported=True, reason="mock") for i in ids])

        raise NotImplementedError(output_format)


def make_llm(settings: Settings):
    return MockLLM() if settings.mock_llm else ClaudeLLM(settings.anthropic_api_key)


def _system_blocks(meta: VideoMeta, transcript_text: str, channel: ChannelConfig) -> list[dict]:
    video_block = (
        f"<video>\n제목: {meta.title}\n채널: {meta.channel_title}\n길이: {fmt_ts(meta.duration_sec)}\n"
        f"키워드: {', '.join(meta.keywords[:30])}\n설명:\n{meta.description[:3000]}\n</video>\n\n"
        f"<transcript>\n{transcript_text}\n</transcript>"
    )
    return [
        {"type": "text", "text": SYSTEM_RULES + f"\n문체: {channel.tone}\n"},
        {"type": "text", "text": video_block, "cache_control": {"type": "ephemeral"}},
    ]


def _target_section_count(duration_sec: int) -> tuple[int, int]:
    minutes = max(1, duration_sec // 60)
    if minutes <= 8:
        return 3, 5
    if minutes <= 20:
        return 5, 8
    if minutes <= 45:
        return 7, 10
    return 9, 12


def plan_sections(llm, settings: Settings, meta: VideoMeta, system: list[dict]) -> SectionPlan:
    if meta.chapters and len(meta.chapters) >= 3:
        items = []
        for i, ch in enumerate(meta.chapters):
            end = meta.chapters[i + 1]["start"] if i + 1 < len(meta.chapters) else meta.duration_sec
            items.append(SectionPlanItem(title=ch["title"], start=fmt_ts(ch["start"]), end=fmt_ts(end)))
        return SectionPlan(sections=items)
    lo, hi = _target_section_count(meta.duration_sec)
    prompt = (
        f"자막을 주제 전환점 기준으로 {lo}~{hi}개의 구간으로 나누세요. "
        "각 구간은 제목(독자가 목차로 볼 때 내용을 짐작할 수 있게), 시작·끝 타임스탬프를 가집니다. "
        "구간은 서로 겹치지 않고 전체 영상을 빠짐없이 덮어야 합니다. 첫 구간은 00:00 에서 시작합니다."
    )
    return llm.parse(settings.model_light, system, prompt, SectionPlan, max_tokens=4000)


def detail_section(llm, settings: Settings, system: list[dict], item: SectionPlanItem) -> Section:
    prompt = (
        f"구간 제목: {item.title}\n시작: {item.start}\n끝: {item.end}\n\n"
        "위 구간의 자막만을 근거로 다음을 작성하세요.\n"
        "- summary: 이 구간의 내용을 3~6문장으로. 독자가 영상을 안 봐도 이해되게.\n"
        "- details: 핵심 주장·근거·사례를 항목당 1~2문장으로 최소 4개(내용이 적으면 2개). 각 항목 끝에 [mm:ss] 를 붙입니다. "
        "화자의 말을 받아쓰지 말고, 독자가 배워 갈 '핵심'을 설명하는 문장으로 씁니다('~라고 말한다' 같은 화법 서술 금지).\n"
        "- quotes: 인상적인 발언 1~3개를 원문 그대로(오탈자만 교정), 타임스탬프와 함께.\n"
        "- numbers: 언급된 수치(퍼센트, 금액, 연도, 개수 등)를 모두. 없으면 빈 배열.\n"
        "- image_caption: 이 구간을 카드 이미지 한 장으로 표현할 때 들어갈 한 줄(25자 이내).\n"
        "start/end/title 은 주어진 값을 그대로 씁니다."
    )
    return llm.parse(settings.model_main, system, prompt, Section)


def synthesize(llm, settings: Settings, system: list[dict], sections: list[Section],
               channel: ChannelConfig) -> Synthesis:
    outline = "\n".join(f"- [{s.start}~{s.end}] {s.title}: {s.summary}" for s in sections)
    prompt = (
        "다음은 구간별 요약입니다.\n" + outline + "\n\n"
        "전체 영상에 대해 다음을 작성하세요.\n"
        "- one_liner: 영상 전체를 한 문장으로(40자 내외).\n"
        "- intro: 독자가 이 글에서 무엇을 얻는지 알려주는 도입부 2~3문장.\n"
        "- key_takeaways: 가장 중요한 포인트 3~5개, 각각 타임스탬프 포함.\n"
        "- conclusion: 결론과 시사점 3~5문장. 영상이 제시한 제안이나 관점을 정리.\n"
        "- glossary: 일반 독자에게 생소할 수 있는 용어 3~6개와 쉬운 설명. 영상에 나온 용어만.\n"
        "- faq: 독자가 궁금해할 질문 3개와 영상 내용에 근거한 답.\n"
        "- background: 영상 내용을 이해하는 데 도움이 되는 배경 설명 2~4문장. 널리 알려진 사실만 쓰고, "
        "확신이 없으면 빈 문자열로 둡니다. 이 부분은 영상 밖 지식이므로 '참고'로 표시될 것입니다.\n"
        "- editor_note_draft: 블로그 운영자가 '편집자 메모'로 다듬어 쓸 초안 3~5문장. 요약의 반복이 아니라 "
        "독자가 실제로 적용할 때의 포인트, 주의할 점, 영상이 다루지 않은 반대 관점이나 질문을 담습니다. "
        "운영자가 직접 고쳐 쓸 것이므로 1인칭 서술체로 씁니다.\n"
        "- study_questions: 독자가 내용을 이해했는지 스스로 점검할 질문 3~5개. 영상 내용으로 답할 수 있는 것만.\n"
        f"- seo: 검색용 제목(원 영상 제목을 그대로 복사하지 말고 핵심 주제를 담아 45자 내외), 영문 소문자·하이픈 slug, "
        f"메타 설명(120자 내외), 태그 5~8개(채널 관련 태그 '{channel.blog_category}' 포함)."
    )
    return llm.parse(settings.model_main, system, prompt, Synthesis)


def verify(llm, settings: Settings, system: list[dict], sections: list[Section],
           synthesis: Synthesis) -> tuple[list[Section], Synthesis, float]:
    """모든 주장(details, takeaways)이 자막에 근거하는지 검증하고, 근거 없는 것은 제거."""
    claims: list[tuple[str, str]] = []
    for si, s in enumerate(sections):
        for di, d in enumerate(s.details):
            claims.append((f"c{si}_{di}", d))
    for ti, t in enumerate(synthesis.key_takeaways):
        claims.append((f"t{ti}", f"{t.text} [{t.ts}]"))
    if not claims:
        return sections, synthesis, 0.0
    listing = "\n".join(f"[{cid}] {text}" for cid, text in claims)
    prompt = (
        "아래 각 주장이 자막 내용에 실제로 근거하는지 판정하세요. 표현이 달라도 의미가 자막에 있으면 supported=true, "
        "자막에 없는 사실·수치·해석이 들어갔으면 false 입니다. 모든 claim_id 에 대해 판정을 내리세요.\n\n" + listing
    )
    result = llm.parse(settings.model_light, system, prompt, Verification, max_tokens=8000)
    bad = {v.claim_id for v in result.verdicts if not v.supported}
    ratio = len(bad) / len(claims)
    for si, s in enumerate(sections):
        s.details = [d for di, d in enumerate(s.details) if f"c{si}_{di}" not in bad]
    synthesis.key_takeaways = [t for ti, t in enumerate(synthesis.key_takeaways) if f"t{ti}" not in bad]
    return sections, synthesis, ratio


def summarize_video(settings: Settings, channel: ChannelConfig, meta: VideoMeta,
                    transcript: Transcript, llm=None) -> Summary:
    llm = llm or make_llm(settings)
    text = transcript.to_text()
    if meta.duration_sec == 0:
        meta.duration_sec = transcript.duration_sec
    system = _system_blocks(meta, text, channel)

    plan = plan_sections(llm, settings, meta, system)
    sections = [detail_section(llm, settings, system, item) for item in plan.sections]
    synthesis = synthesize(llm, settings, system, sections, channel)
    sections, synthesis, ratio = verify(llm, settings, system, sections, synthesis)

    cost = llm.usage.cost_usd
    return Summary(
        video_id=meta.video_id,
        sections=sections,
        synthesis=synthesis,
        unsupported_ratio=round(ratio, 3),
        needs_review=(ratio > 0.2) or (cost > settings.max_cost_per_video_usd),
        transcript_source=transcript.source,
        cost_usd=round(cost, 4),
    )
