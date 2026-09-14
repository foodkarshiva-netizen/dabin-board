"""자막 수집: youtube-transcript-api(수동 → 자동 자막) → yt-dlp 폴백."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig


@dataclass
class Transcript:
    video_id: str
    language: str
    source: str            # manual | auto | ytdlp
    segments: list[dict]   # [{"start": float, "text": str}]

    def to_text(self, bucket_sec: int = 15) -> str:
        """[mm:ss] 텍스트 형태로 병합. bucket_sec 단위로 묶어 토큰을 줄인다."""
        lines: list[str] = []
        cur_start = None
        cur: list[str] = []
        for seg in self.segments:
            t = seg["text"].replace("\n", " ").strip()
            if not t:
                continue
            if cur_start is None:
                cur_start = seg["start"]
            if seg["start"] - cur_start >= bucket_sec and cur:
                lines.append(f"[{fmt_ts(cur_start)}] {' '.join(cur)}")
                cur, cur_start = [], seg["start"]
            cur.append(t)
        if cur:
            lines.append(f"[{fmt_ts(cur_start)}] {' '.join(cur)}")
        return "\n".join(lines)

    @property
    def duration_sec(self) -> int:
        if not self.segments:
            return 0
        last = self.segments[-1]
        return int(last["start"] + last.get("duration", 0))


def fmt_ts(sec: float) -> str:
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fetch_transcript(video_id: str, languages: tuple[str, ...] = ("ko", "en"),
                     proxy_url: str = "") -> Transcript:
    errors: list[str] = []
    try:
        return _fetch_via_api(video_id, languages, proxy_url)
    except Exception as e:  # noqa: BLE001
        errors.append(f"transcript-api: {type(e).__name__}: {e}")
    if shutil.which("yt-dlp"):
        try:
            return _fetch_via_ytdlp(video_id, languages, proxy_url)
        except Exception as e:  # noqa: BLE001
            errors.append(f"yt-dlp: {type(e).__name__}: {e}")
    raise RuntimeError("자막을 가져오지 못했습니다. " + " | ".join(errors))


def _fetch_via_api(video_id: str, languages: tuple[str, ...], proxy_url: str) -> Transcript:
    proxy = GenericProxyConfig(http_url=proxy_url, https_url=proxy_url) if proxy_url else None
    api = YouTubeTranscriptApi(proxy_config=proxy)
    tlist = api.list(video_id)
    source = "manual"
    try:
        t = tlist.find_manually_created_transcript(list(languages))
    except Exception:  # noqa: BLE001
        t = tlist.find_generated_transcript(list(languages))
        source = "auto"
    fetched = t.fetch()
    segments = [{"start": float(s.start), "duration": float(s.duration), "text": s.text}
                for s in fetched]
    return Transcript(video_id=video_id, language=t.language_code, source=source, segments=segments)


def _fetch_via_ytdlp(video_id: str, languages: tuple[str, ...], proxy_url: str) -> Transcript:
    with tempfile.TemporaryDirectory() as td:
        cmd = ["yt-dlp", "--skip-download", "--write-subs", "--write-auto-subs",
               "--sub-langs", ",".join(languages), "--sub-format", "json3",
               "-o", f"{td}/%(id)s.%(ext)s", f"https://www.youtube.com/watch?v={video_id}"]
        if proxy_url:
            cmd += ["--proxy", proxy_url]
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
        files = sorted(Path(td).glob("*.json3"))
        if not files:
            raise RuntimeError("yt-dlp가 자막 파일을 만들지 않았습니다")
        path = files[0]
        lang = path.name.split(".")[-2]
        data = json.loads(path.read_text(encoding="utf-8"))
        segments = []
        for ev in data.get("events", []):
            text = "".join(s.get("utf8", "") for s in ev.get("segs", [])).strip()
            if text:
                segments.append({"start": ev.get("tStartMs", 0) / 1000.0,
                                 "duration": ev.get("dDurationMs", 0) / 1000.0, "text": text})
        return Transcript(video_id=video_id, language=lang, source="ytdlp", segments=segments)
