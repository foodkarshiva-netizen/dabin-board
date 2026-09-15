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
    card_points: list[str] = []  # 카드에 그대로 들어갈 완성 문장 2~3개(각 60자 이내, 줄임 없이 표시)


class Takeaway(BaseModel):
    text: str
    ts: str
    short: str = ""         # 카드용 완성 문장(60자 이내). 비어 있으면 text 를 그대로 씀


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
    editor_note_draft: str = ""   # 편집자 메모 초안(운영자가 발행 전 자기 의견으로 교체)
    study_questions: list[str] = []  # 독자가 이해도를 점검할 질문 3~5개
    category: str = ""               # 블로그 카테고리 이름(channels.json categories 중 하나). 비면 채널 기본값
    discussion_question: str = ""    # 글 끝 댓글 유도 질문 한 줄 ("여러분은 …?")
    seo: SEO


class ClaimVerdict(BaseModel):
    claim_id: str
    supported: bool
    reason: str


class Verification(BaseModel):
    verdicts: list[ClaimVerdict]


class Diagram(BaseModel):
    """내용 도식 한 장. type/data 형식은 diagrams.py 참고."""
    type: str                 # flow | cycle | compare | trend | factors | versus | steps
    title: str
    section_index: int = -1   # 어느 구간 아래에 넣을지 (-1 이면 핵심 포인트 아래)
    caption: str = ""
    data: dict = {}


class Summary(BaseModel):
    """파이프라인 최종 산출물."""
    video_id: str
    sections: list[Section]
    synthesis: Synthesis
    diagrams: list[Diagram] = []
    unsupported_ratio: float
    needs_review: bool
    transcript_source: str
    cost_usd: float
