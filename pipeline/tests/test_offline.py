"""오프라인 테스트: MOCK_LLM 로 요약→이미지→HTML 흐름을 검증한다.  실행: python -m pytest tests/  또는  python tests/test_offline.py"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["MOCK_LLM"] = "1"

from ytblog.config import ChannelConfig, Settings  # noqa: E402
from ytblog.discover import VideoMeta, parse_chapters  # noqa: E402
from ytblog.render import DRAFT_NOTICE, NOTE_END, NOTE_START, build_post_html, linkify, replace_note_block  # noqa: E402
from ytblog.state import State  # noqa: E402
from ytblog.summarize import summarize_video  # noqa: E402
from ytblog.transcript import Transcript, fmt_ts  # noqa: E402


def load_fixture() -> Transcript:
    d = json.loads((ROOT / "tests/fixtures/transcript_sample.json").read_text(encoding="utf-8"))
    return Transcript(video_id=d["video_id"], language=d["language"], source=d["source"], segments=d["segments"])


def test_transcript_text_and_ts():
    t = load_fixture()
    text = t.to_text()
    assert text.startswith("[00:00]")
    assert t.duration_sec == 125
    assert fmt_ts(3725) == "1:02:05"


def test_parse_chapters():
    desc = "소개\n00:00 인트로\n01:10 본론\n(02:30) 결론\n"
    ch = parse_chapters(desc)
    assert [c["start"] for c in ch] == [0, 70, 150]
    assert parse_chapters("01:00 시작이 0이 아님\n02:00 둘") == []


def test_summary_and_html():
    with tempfile.TemporaryDirectory() as td:
        os.environ["YTBLOG_DATA_DIR"] = td
        settings = Settings.load()
        t = load_fixture()
        meta = VideoMeta(video_id=t.video_id, title="샘플", channel_id="UCSAMPLE", channel_title="지식인사이드",
                         duration_sec=t.duration_sec)
        ch = ChannelConfig(handle="@sample", channel_id="UCSAMPLE", min_duration_sec=0)
        s = summarize_video(settings, ch, meta, t)
        assert s.sections and s.synthesis.key_takeaways
        assert not s.needs_review
        html = build_post_html(s, meta, [], {}, "테스트 블로그")
        assert "wp:embed" not in html and "&t=" not in html and "[0" not in html  # 기본: 임베드·타임스탬프 없음
        assert "<strong>출처</strong>" in html and "youtube.com/watch" not in html               # 출처는 텍스트만
        assert "<!-- wp:paragraph -->" in html and "<!-- wp:heading -->" in html and "<!-- wp:separator -->" in html
        assert "스스로 점검해 보기" not in html and "핵심 용어" not in html      # 기본: 짧은 글
        assert "스스로 점검해 보기" in build_post_html(s, meta, [], {}, "테스트 블로그", include_questions=True)
        html_ts = build_post_html(s, meta, [], {}, "테스트 블로그", show_timestamps=True, embed_video=True, source_link=True)
        assert "wp:embed" in html_ts and "youtube.com/watch?v=SAMPLE00001" in html_ts and "&t=" in html_ts
        st = State(Path(td) / "state.json")
        st.set_status("x", "fetched")
        st.mark_failed("x", "summarize", "boom")
        assert st.video("x")["status"] == "failed" and st.video("x")["attempts"] == 1


def test_linkify():
    out = linkify("편도체가 빠르다 [00:42]", "abc")
    assert "watch?v=abc&t=42s" in out and "<a" in out


def _mock_summary(td):
    os.environ["YTBLOG_DATA_DIR"] = td
    settings = Settings.load()
    t = load_fixture()
    meta = VideoMeta(video_id=t.video_id, title="샘플", channel_id="UCSAMPLE", channel_title="지식인사이드",
                     duration_sec=t.duration_sec)
    ch = ChannelConfig(handle="@sample", channel_id="UCSAMPLE", min_duration_sec=0)
    return settings, meta, summarize_video(settings, ch, meta, t)


def test_editor_note_block():
    with tempfile.TemporaryDirectory() as td:
        _, meta, s = _mock_summary(td)
        assert s.synthesis.editor_note_draft  # 모의 LLM 도 초안을 낸다
        # 사람 메모가 없으면 AI 초안 + 교체 안내가 들어간다
        html = build_post_html(s, meta, [], {}, "테스트 블로그", note_auto=False)
        assert NOTE_START in html and NOTE_END in html and "편집자 메모" in html
        assert DRAFT_NOTICE in html
        # 사람 메모가 있으면 초안 안내가 사라지고 메모가 들어간다
        html2 = build_post_html(s, meta, [], {}, "테스트 블로그", editor_note="첫 줄\n\n둘째 줄 <b>")
        assert DRAFT_NOTICE not in html2 and "<p>첫 줄.</p>" in html2 and "<p>둘째 줄 &lt;b&gt;.</p>" in html2   # 마침표 자동 보정
        # 기존 글의 블록을 새 메모로 치환
        html3 = replace_note_block(html, "교체된 메모")
        assert html3.count(NOTE_START) == 1 and "교체된 메모" in html3 and DRAFT_NOTICE not in html3
        # 블록이 없는 글에는 출처 고지 앞에 삽입
        stripped = html.split(NOTE_START)[0] + html.split(NOTE_END)[1]
        html4 = replace_note_block(stripped, "삽입 메모")
        assert html4.index("삽입 메모") < html4.index("<strong>출처</strong>")
        from ytblog.images import CardImage
        from pathlib import Path as _P
        fig_html = build_post_html(s, meta, [CardImage(kind="takeaways", path=_P("x/hero.png"), alt="a", caption="")],
                                   {"x/hero.png": "https://b/hero.png"}, "b", id_map={"x/hero.png": 77})
        assert '<!-- wp:image {"id":77' in fig_html and "wp-image-77" in fig_html


def test_ensure_period_and_auto_note():
    from ytblog.render import ensure_period
    assert ensure_period("마침표 없음") == "마침표 없음." and ensure_period("있음.") == "있음." and ensure_period("질문?") == "질문?"
    assert ensure_period("") == "" and ensure_period("따옴표로 끝남”") == "따옴표로 끝남”"
    with tempfile.TemporaryDirectory() as td:
        _, meta, s = _mock_summary(td)
        assert DRAFT_NOTICE not in build_post_html(s, meta, [], {}, "b")                    # 기본: AI 메모 그대로
        assert DRAFT_NOTICE in build_post_html(s, meta, [], {}, "b", note_auto=False)      # 사람 검토 모드


def test_diagrams():
    from ytblog.diagrams import diagram_html
    assert "→" in diagram_html("flow", {"steps": ["A", "B", "C"]}) and "box last" in diagram_html("flow", {"steps": ["A", "B"]})
    assert "↓" in diagram_html("flow", {"steps": list("ABCDE")})
    assert diagram_html("cycle", {"steps": ["A", "B", "C"]}).count("<rect") == 3
    c = diagram_html("compare", {"unit": "%", "items": [{"label": "한국", "value": 4.4}, {"label": "미국", "value": 4.8}]})
    assert "4.4%" in c and "bar hi" in c
    t = diagram_html("trend", {"unit": "원", "points": [{"label": "a", "value": 1300}, {"label": "b", "value": 1550}]})
    assert "1,550원" in t and t.count("<rect") == 2
    assert "e down" in diagram_html("factors", {"items": [{"cause": "x", "effect": "y", "dir": "down"}]})
    assert "col r" in diagram_html("versus", {"left": {"title": "a", "items": ["1"]}, "right": {"title": "b", "items": ["2"]}})
    assert diagram_html("steps", {"steps": ["a", {"title": "b", "desc": "c"}]}).count("class='st'") == 2
    try:
        diagram_html("nope", {}); assert False
    except ValueError:
        pass


def test_state_notes_and_quota():
    from ytblog.cli import quota_left
    with tempfile.TemporaryDirectory() as td:
        os.environ["YTBLOG_DATA_DIR"] = td
        os.environ["MAX_POSTS_PER_WEEK"] = "2"
        os.environ["MAX_POSTS_PER_DAY"] = "1"
        settings = Settings.load()
        st = State(Path(td) / "state.json")
        assert st.note_for("v1") == ""
        st.set_note("v1", "  메모  ")
        assert State(Path(td) / "state.json").note_for("v1") == "메모"
        left, why = quota_left(settings, st, None)
        assert left == 1 and "7일 0/2" in why and "24시간 0/1" in why
        st.mark_post_created("v1", "drafted", wp_post_id=1)
        assert st.count_recent_posts(7) == 1 and st.count_recent_posts(1) == 1
        assert quota_left(settings, st, None)[0] == 0
        st.mark_post_created("v1", "published")           # 같은 글 공개 전환은 다시 세지 않는다
        assert st.count_recent_posts(7) == 1

        class FakeWP:
            def count_recent_posts(self, days, category_id=0, marker="ytblog"):
                return 5 if days == 7 else 0
        assert quota_left(settings, st, FakeWP())[0] == 0  # 주간 상한 초과면 일간 여유가 있어도 0
        os.environ["MAX_POSTS_PER_WEEK"] = "0"; os.environ["MAX_POSTS_PER_DAY"] = "0"
        assert quota_left(Settings.load(), st, FakeWP())[0] > 100  # 0 = 무제한
        for k in ("MAX_POSTS_PER_WEEK", "MAX_POSTS_PER_DAY"):
            os.environ.pop(k, None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
