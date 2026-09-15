"""다빈보드 '블로그' 탭 대기열(Firestore yt_queue) 연동. board.js(서비스 계정)로 읽고 쓴다.

문서: {url, vid, note, status: pending|working|done|failed, ts, by, title, postUrl, doneAt, err}
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

BOARD_JS = Path(os.environ.get("BOARD_JS", r"C:\Users\karsh\.claude\board-tools\board.js"))
COL = "yt_queue"


def _run(*args: str) -> str:
    if not BOARD_JS.exists():
        raise RuntimeError(f"board.js 가 없습니다: {BOARD_JS}")
    r = subprocess.run(["node", str(BOARD_JS), *args], capture_output=True, text=True, encoding="utf-8", timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"board.js 실패: {r.stderr[-800:] or r.stdout[-800:]}")
    return r.stdout


def pending() -> list[dict]:
    """status == pending 인 항목을 등록순으로."""
    out = _run("query", COL, json.dumps([["status", "==", "pending"]]), "50")
    items = json.loads(out) if out.strip().startswith("[") else []
    return sorted(items, key=lambda x: x.get("ts", 0))


def mark(doc_id: str, status: str, **fields) -> None:
    data = {"status": status, **fields}
    if status == "done":
        data.setdefault("doneAt", int(time.time() * 1000))
    _run("update", COL, doc_id, json.dumps(data, ensure_ascii=False))
