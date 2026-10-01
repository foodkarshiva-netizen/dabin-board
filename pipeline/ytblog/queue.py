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


CACHE = Path(__file__).resolve().parents[1] / "data" / "queue_cache.json"


class QueueUnavailable(RuntimeError):
    """대기열을 실시간으로도, 저장해 둔 목록으로도 읽을 수 없음."""


def _load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_cache(items: list[dict], ts: float | None = None) -> None:
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"ts": ts or time.time(), "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def pending(allow_cache: bool = True) -> list[dict]:
    """status == pending 인 항목을 등록순으로.

    다빈보드 Firestore 는 하루 읽기 한도(무료 5만 건, 한국 16시경 초기화)가 자주 바닥난다. 읽기에 성공할 때마다 목록을
    data/queue_cache.json 에 저장해 두고, 못 읽을 때는 그 목록을 쓴다(항목에 _cached=저장 시각). 저장본도 없으면 QueueUnavailable.
    """
    try:
        out = _run("query", COL, json.dumps([["status", "==", "pending"]]), "50")
    except RuntimeError as e:
        cache = _load_cache()
        if allow_cache and cache.get("ts"):
            return [{**it, "_cached": cache["ts"]} for it in cache.get("items", [])]
        raise QueueUnavailable(str(e)[-300:]) from e
    items = sorted(json.loads(out) if out.strip().startswith("[") else [], key=lambda x: x.get("ts", 0))
    _save_cache(items)
    return items


def mark(doc_id: str, status: str, **fields) -> None:
    data = {"status": status, **fields}
    if status == "done":
        data.setdefault("doneAt", int(time.time() * 1000))
    cache = _load_cache()      # 저장해 둔 목록에서도 뺀다(다음에 못 읽는 날 같은 글을 또 잡지 않게)
    if cache.get("ts") and status != "pending":
        _save_cache([it for it in cache.get("items", []) if (it.get("_id") or it.get("id")) != doc_id], cache["ts"])
    _run("update", COL, doc_id, json.dumps(data, ensure_ascii=False))
