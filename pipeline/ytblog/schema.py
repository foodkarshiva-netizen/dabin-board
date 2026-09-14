"""요약 결과 스키마(구조화 출력용 Pydantic 모델)."""
from __future__ import annotations

from pydantic import BaseModel


class SectionPlanItem(BaseModel):
    title: str
    start: str   # "mm:ss" 또는 "h:mm:ss"
    end: str


class SectionPlan(BaseModel):
    sections: list[SectionPlanItem]


class Quote(BaseModel):
    text: str
    ts: str


class Number(BaseModel):
    label: str
    value: str
    ts: str


class Section(BaseModel):
    title: str
    start: str
    end: str
    summary: str            # 3~6문장
    details: list[str]      # 각 항목 끝에 [mm:ss]
    quotes: list[Quote]
    numbers: list[Number]
    image_caption: str      # 이 구간을 카드 이미지로 만들 때 쓸 한 줄


class Takeaway(BaseModel):
    text: str
    ts: str


class GlossaryItem(BaseModel):
    term: str
    definition: str


class FAQItem(BaseModel):
    q: str
    a: str


class SEO(BaseModel):
    title: str
    slug: str
    description: str
    tags: list[str]


class Synthesis(BaseModel):
    one_liner: str
    intro: str                    # 2~3문장 도입부
    key_takeaways: list[Takeaway]
    conclusion: str               # 결론/시사점
    glossary: list[GlossaryItem]
    faq: list[FAQItem]
    background: str               # 영상에 없는 배경 설명(부가가치). 없으면 빈 문자열
    seo: SEO


class ClaimVerdict(BaseModel):
    claim_id: str
    supported: bool
    reason: str


class Verification(BaseModel):
    verdicts: list[ClaimVerdict]


class Summary(BaseModel):
    """파이프라인 최종 산출물."""
    video_id: str
    sections: list[Section]
    synthesis: Synthesis
    unsupported_ratio: float
    needs_review: bool
    transcript_source: str
    cost_usd: float
