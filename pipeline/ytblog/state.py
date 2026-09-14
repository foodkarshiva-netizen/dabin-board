"""영상별 처리 상태(JSON 파일). Phase 1에서 Firestore로 교체 예정."""
from __future__ import annotations

import json
import time
from pathlib import Path

STATUSES = [
    "discovered", "fetched", "summarized", "illustrated", "drafted",
    "published", "skipped", "needs_review", "failed", "dead",
]
MAX_ATTEMPTS = 3


class State:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                self.data = json.load(f)
        else:
            self.data = {"videos": {}, "channels": {}}
        self.data.setdefault("notes", {})

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)

    # --- videos ---
    def video(self, video_id: str) -> dict:
        return self.data["videos"].setdefault(video_id, {
            "status": "discovered", "attempts": 0, "cost_usd": 0.0,
            "updated": time.time(),
        })

    def set_status(self, video_id: str, status: str, **fields) -> None:
        assert status in STATUSES, status
        v = self.video(video_id)
        v["status"] = status
        v["updated"] = time.time()
        v.update(fields)
        self.save()

    def mark_failed(self, video_id: str, stage: str, error: str) -> None:
        v = self.video(video_id)
        v["attempts"] = v.get("attempts", 0) + 1
        status = "dead" if v["attempts"] >= MAX_ATTEMPTS else "failed"
        self.set_status(video_id, status, failed_stage=stage, last_error=error[:2000])

    def mark_post_created(self, video_id: str, status: str, **fields) -> None:
        """WordPress 글(초안/공개)이 생성된 시각을 남긴다. 발행 상한 계산용."""
        v = self.video(video_id)
        v.setdefault("post_created", time.time())
        self.set_status(video_id, status, **fields)

    def count_recent_posts(self, days: float) -> int:
        """최근 days 일 안에 생성된 글(초안 포함) 수. WordPress 를 못 쓸 때의 대체 집계."""
        since = time.time() - days * 86400
        return sum(1 for v in self.data["videos"].values() if v.get("post_created", 0) >= since)

    # --- editor notes ---
    def note_for(self, video_id: str) -> str:
        return self.data["notes"].get(video_id, {}).get("text", "")

    def set_note(self, video_id: str, text: str) -> None:
        self.data["notes"][video_id] = {"text": text.strip(), "updated": time.time()}
        self.save()

    def is_done(self, video_id: str) -> bool:
        return self.data["videos"].get(video_id, {}).get("status") in ("published", "skipped", "dead", "drafted", "needs_review")

    # --- channels ---
    def channel_id_for(self, handle: str) -> str:
        return self.data["channels"].get(handle, {}).get("channel_id", "")

    def remember_channel(self, handle: str, channel_id: str, title: str) -> None:
        self.data["channels"][handle] = {"channel_id": channel_id, "title": title}
        self.save()
