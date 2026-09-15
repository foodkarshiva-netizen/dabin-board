"""환경 변수(.env)와 channels.json 로딩."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PIPELINE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PIPELINE_DIR / ".env")


@dataclass
class ChannelConfig:
    handle: str                      # "@지식인사이드" 또는 채널 URL
    channel_id: str = ""             # 비어 있으면 첫 실행 때 resolve
    title: str = ""
    enabled: bool = True
    min_duration_sec: int = 180      # 이보다 짧으면(쇼츠 등) 건너뜀
    max_duration_sec: int = 5400     # 90분 초과는 자동 처리하지 않음
    language_hint: str = "ko"
    blog_category: str = "유튜브 요약"
    extra_tags: list[str] = field(default_factory=list)
    tone: str = "친절하고 명확한 설명체, 존댓말"
    show_timestamps: bool = False    # 본문에 [mm:ss] 영상 시점 링크 표시
    embed_video: bool = False        # 원본 영상 임베드
    source_link: bool = False        # 출처 고지에 채널·영상 하이퍼링크 (False 면 텍스트만)
    include_toc: bool = False        # '이 글의 순서' 목차
    include_glossary: bool = False   # 핵심 용어 (본문 + 카드)
    include_questions: bool = False  # 자기 점검 질문 (본문 + 카드)
    include_faq: bool = False        # 자주 묻는 질문
    include_numbers: bool = True     # 숫자로 기억하기 카드
    max_sections: int = 6            # 자동 요약 시 구간 수 상한 (수동 모드는 summary.json 그대로)
    max_details: int = 4             # 구간당 본문 항목 수 상한

    @classmethod
    def from_dict(cls, d: dict) -> "ChannelConfig":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class Settings:
    anthropic_api_key: str
    model_main: str
    model_light: str
    wp_url: str
    wp_user: str
    wp_app_password: str
    yt_proxy_url: str
    mock_llm: bool
    data_dir: Path
    out_dir: Path
    max_cost_per_video_usd: float
    blog_name: str
    max_posts_per_week: int          # 최근 7일 생성 글(초안 포함) 상한. 0 이면 무제한
    max_posts_per_day: int           # 최근 24시간 상한. 0 이면 무제한
    require_editor_note: bool        # True 면 사람이 쓴 편집자 메모 없이는 --publish 해도 초안으로

    @classmethod
    def load(cls) -> "Settings":
        env = os.environ
        data_dir = Path(env.get("YTBLOG_DATA_DIR", PIPELINE_DIR / "data"))
        out_dir = Path(env.get("YTBLOG_OUT_DIR", PIPELINE_DIR / "out"))
        return cls(
            anthropic_api_key=env.get("ANTHROPIC_API_KEY", ""),
            model_main=env.get("MODEL_MAIN", "claude-opus-5"),
            model_light=env.get("MODEL_LIGHT", "claude-sonnet-5"),
            wp_url=env.get("WP_URL", "").rstrip("/"),
            wp_user=env.get("WP_USER", ""),
            wp_app_password=env.get("WP_APP_PASSWORD", ""),
            yt_proxy_url=env.get("YT_PROXY_URL", ""),
            mock_llm=env.get("MOCK_LLM", "") in ("1", "true", "yes"),
            data_dir=data_dir,
            out_dir=out_dir,
            max_cost_per_video_usd=float(env.get("MAX_COST_PER_VIDEO_USD", "1.5")),
            blog_name=env.get("BLOG_NAME", "유튜브 요약 블로그"),
            max_posts_per_week=int(env.get("MAX_POSTS_PER_WEEK", "4")),
            max_posts_per_day=int(env.get("MAX_POSTS_PER_DAY", "1")),
            require_editor_note=env.get("REQUIRE_EDITOR_NOTE", "1") not in ("0", "false", "no"),
        )


def load_channels(path: Path | None = None) -> list[ChannelConfig]:
    path = path or PIPELINE_DIR / "channels.json"
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [ChannelConfig.from_dict(c) for c in raw["channels"]]
