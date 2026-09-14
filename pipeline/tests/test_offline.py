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
from ytblog.render import build_post_html, linkify  # noqa: E402
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
        assert "youtube.com/embed/SAMPLE00001" in html
        assert "출처 및 저작권 안내" in html
        assert "t=" in html  # 타임스탬프 링크
        st = State(Path(td) / "state.json")
        st.set_status("x", "fetched")
        st.mark_failed("x", "summarize", "boom")
        assert st.video("x")["status"] == "failed" and st.video("x")["attempts"] == 1


def test_linkify():
    out = linkify("편도체가 빠르다 [00:42]", "abc")
    assert "watch?v=abc&t=42s" in out and "<a" in out


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
