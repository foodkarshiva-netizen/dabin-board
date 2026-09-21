"""명령줄 인터페이스.

  python -m ytblog resolve "@지식인사이드"        핸들 → channel_id 확인
  python -m ytblog discover                        등록 채널의 새 영상 목록
  python -m ytblog run [--limit N] [--video ID] [--publish] [--dry-run]
  python -m ytblog fixture                         샘플 자막으로 오프라인 전체 흐름 실행
  python -m ytblog wp-check                        WordPress 연결 확인
  python -m ytblog note <video_id> "메모" [--publish]  편집자 메모 저장(+WordPress 글 갱신/공개)
  python -m ytblog prepare <URL|ID>                 (수동 모드 1단계) 메타·자막을 out/<id>/ 에 저장
  python -m ytblog finish <ID> [--publish]          (수동 모드 2단계) out/<id>/summary.json 으로 이미지·글·발행
  python -m ytblog quota                           최근 7일·24시간 생성 글 수와 남은 발행 여유
  python -m ytblog queue                            다빈보드 '블로그' 탭 대기열(pending) 보기
  python -m ytblog queue start <docId>              작업 중으로 표시
  python -m ytblog queue done <docId> <postUrl> [제목]   발행 완료 표시
  python -m ytblog queue fail <docId> <사유>        실패 표시
  python -m ytblog finish <ID> --queue <docId>      발행 후 대기열 자동 완료 표시
  python -m ytblog banner ["헤드라인"] ["부제"]      홈 배너 이미지 갱신(기본: 최근 글 반영)
  python -m ytblog report [--no-post]               주간 리포트 → 다빈보드 소통 (+배너 갱신)
  python -m ytblog threads-auth | threads-refresh | threads-test   스레드 토큰 발급·갱신·게시 테스트
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
import traceback
from pathlib import Path

from .config import ChannelConfig, Settings, load_channels
from .discover import VideoMeta, enrich_video, list_recent_videos, resolve_channel
from .images import build_cards
from .render import build_excerpt, build_post_html, replace_note_block
from .state import State
from .schema import Summary
from .summarize import make_llm, summarize_video
from .transcript import Transcript, fetch_transcript


def log(msg: str) -> None:
    print(msg, flush=True)


def _resolve_all(settings: Settings, channels: list[ChannelConfig], state: State) -> None:
    for ch in channels:
        if ch.channel_id:
            continue
        cached = state.channel_id_for(ch.handle)
        if cached:
            ch.channel_id = cached
            continue
        cid, title = resolve_channel(ch.handle, settings.yt_proxy_url)
        ch.channel_id, ch.title = cid, ch.title or title
        state.remember_channel(ch.handle, cid, ch.title)
        log(f"[resolve] {ch.handle} → {cid} ({ch.title})")


def cmd_resolve(args, settings: Settings) -> int:
    cid, title = resolve_channel(args.handle, settings.yt_proxy_url)
    log(f"channel_id: {cid}\ntitle: {title}\nrss: https://www.youtube.com/feeds/videos.xml?channel_id={cid}")
    return 0


def cmd_discover(args, settings: Settings) -> int:
    channels = load_channels()
    state = State(settings.data_dir / "state.json")
    _resolve_all(settings, channels, state)
    for ch in channels:
        if not ch.enabled:
            continue
        vids = list_recent_videos(ch.channel_id, settings.yt_proxy_url)
        log(f"\n## {ch.title or ch.handle} ({len(vids)}개)")
        for v in vids:
            st = state.data["videos"].get(v.video_id, {}).get("status", "new")
            log(f"  {v.video_id}  {st:12s}  {v.published[:10]}  {v.title}")
    return 0


def process_video(settings: Settings, channel: ChannelConfig, meta: VideoMeta, state: State,
                  transcript: Transcript | None = None, publish: bool = False, dry_run: bool = False,
                  wp=None) -> str:
    """한 영상을 끝까지 처리. 반환값은 최종 status."""
    vid = meta.video_id
    out_dir = settings.out_dir / vid
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) 메타 보강 + 필터
    if transcript is None:
        meta = enrich_video(meta, settings.yt_proxy_url)
    if meta.duration_sec and meta.duration_sec < channel.min_duration_sec:
        state.set_status(vid, "skipped", reason=f"too short ({meta.duration_sec}s)")
        return "skipped"
    if meta.duration_sec and meta.duration_sec > channel.max_duration_sec:
        state.set_status(vid, "needs_review", reason=f"too long ({meta.duration_sec}s)")
        return "needs_review"

    # 2) 자막
    try:
        if transcript is None:
            transcript = fetch_transcript(vid, (channel.language_hint, "en"), settings.yt_proxy_url)
        (out_dir / "transcript.txt").write_text(transcript.to_text(), encoding="utf-8")
        state.set_status(vid, "fetched", transcript_source=transcript.source, title=meta.title)
    except Exception as e:  # noqa: BLE001
        state.mark_failed(vid, "fetch", str(e))
        raise

    # 3) 요약
    try:
        llm = make_llm(settings)
        summary = summarize_video(settings, channel, meta, transcript, llm=llm)
        (out_dir / "summary.json").write_text(summary.model_dump_json(indent=2), encoding="utf-8")
        (out_dir / "usage.txt").write_text("\n".join(llm.usage.log), encoding="utf-8")
        state.set_status(vid, "summarized", cost_usd=summary.cost_usd,
                         unsupported_ratio=summary.unsupported_ratio)
        log(f"  요약 완료: 구간 {len(summary.sections)}개, 비용 ${summary.cost_usd:.3f}, "
            f"근거없음 {summary.unsupported_ratio:.0%}{' → 검토 필요' if summary.needs_review else ''}")
    except Exception as e:  # noqa: BLE001
        state.mark_failed(vid, "summarize", str(e))
        raise

    return finish_video(settings, channel, meta, summary, state, out_dir, publish=publish, dry_run=dry_run, wp=wp)


def finish_video(settings: Settings, channel: ChannelConfig, meta: VideoMeta, summary: Summary, state: State,
                 out_dir: Path, publish: bool = False, dry_run: bool = False, wp=None, update_post_id: int = 0) -> str:
    """요약(summary)이 준비된 뒤의 공통 단계: 이미지 → 글 HTML → WordPress 업로드/발행."""
    vid = meta.video_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 4) 이미지
    try:
        cards = build_cards(summary, meta, settings.blog_name, out_dir / "images",
                            include_glossary=channel.include_glossary, include_questions=channel.include_questions,
                            include_numbers=channel.include_numbers, card_footer=channel.card_footer)
        state.set_status(vid, "illustrated", images=len(cards))
        log(f"  이미지 {len(cards)}장 생성")
    except Exception as e:  # noqa: BLE001
        state.mark_failed(vid, "illustrate", str(e))
        raise

    # 5) 글 조립 (+ 업로드) — 로컬 미리보기는 항상 저장
    url_map: dict[str, str] = {}
    id_map: dict[str, int] = {}
    featured = 0
    if wp is not None and not dry_run:
        try:
            import time as _time
            stamp = _time.strftime("%y%m%d%H%M")
            safe_vid = re.sub(r"[^A-Za-z0-9]", "", vid).lower() or "v"
            for c in cards:
                # 파일 이름을 영상·시각별로 고유하게: 워드프레스가 비워진 이름을 재사용하면 브라우저 캐시에 다른 글의 옛 이미지가 뜬다
                mid, url = wp.upload_media(c.path, c.alt, f"{meta.title} - {c.kind}",
                                           filename=f"{safe_vid}-{c.path.stem}-{stamp}{c.path.suffix}")
                url_map[str(c.path)] = url
                id_map[str(c.path)] = mid
                if c.kind == "hero":
                    featured = mid
                mp = getattr(c, "mobile_path", None)
                if mp is not None and mp.exists():
                    m_id, m_url = wp.upload_media(mp, c.alt, f"{meta.title} - {c.kind} (모바일)",
                                                  filename=f"{safe_vid}-{mp.stem}-{stamp}{mp.suffix}")
                    url_map[str(mp)] = m_url
                    id_map[str(mp)] = m_id
        except Exception as e:  # noqa: BLE001
            state.mark_failed(vid, "upload", str(e))
            raise
    note = state.note_for(vid)
    if not url_map:   # 로컬 미리보기: 데스크톱·모바일 파일을 상대 경로로
        for c in cards:
            url_map[str(c.path)] = f"images/{c.path.name}"
            if getattr(c, "mobile_path", None) is not None:
                url_map[str(c.mobile_path)] = f"images/{c.mobile_path.name}"
    html = build_post_html(summary, meta, cards, url_map or {str(c.path): f"images/{c.path.name}" for c in cards},
                           settings.blog_name, id_map=id_map, editor_note=note, note_is_draft=False,
                           show_timestamps=channel.show_timestamps, embed_video=channel.embed_video,
                           source_link=channel.source_link, include_toc=channel.include_toc,
                           include_glossary=channel.include_glossary, include_questions=channel.include_questions,
                           include_faq=channel.include_faq, include_numbers=channel.include_numbers,
                           max_details=channel.max_details, note_auto=channel.editor_note_auto,
                           include_details=channel.include_details)
    preview = f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>{summary.synthesis.seo.title}</title>" \
              "<style>body{max-width:760px;margin:40px auto;font-family:sans-serif;line-height:1.7;padding:0 16px}img{max-width:100%;border-radius:12px}blockquote{border-left:4px solid #ddd;margin:0;padding:4px 16px;color:#555}.yt-box{background:#f4f6fb;padding:12px 16px;border-radius:12px}</style></head><body>" \
              f"<h1>{summary.synthesis.seo.title}</h1>{html}</body></html>"
    (out_dir / "post.html").write_text(preview, encoding="utf-8")

    if wp is None or dry_run:
        state.set_status(vid, "drafted", preview=str(out_dir / "post.html"))
        log(f"  미리보기 저장: {out_dir / 'post.html'}")
        return "drafted"

    # 6) 발행 — 근거 검증 통과 + (설정 시) 사람이 쓴 편집자 메모가 있어야 공개
    try:
        status = "draft"
        if publish and summary.needs_review:
            log("  공개 보류: 근거 검증/비용 기준 미달 → 초안")
        elif publish and settings.require_editor_note and not note:
            log(f"  공개 보류: 편집자 메모 없음 → 초안. `python -m ytblog note {vid} \"메모\" --publish` 로 공개")
        elif publish:
            status = "publish"
        chosen = getattr(summary.synthesis, "category", "") or ""
        if chosen and channel.categories and chosen not in channel.categories:
            chosen = ""
        cats = wp.category_ids([chosen or channel.blog_category])
        tags = wp.tag_ids(list(dict.fromkeys(summary.synthesis.seo.tags + channel.extra_tags)))
        if update_post_id:
            old = wp.get_post(update_post_id)
            if old.get("status") == "publish":
                status = "publish"          # 이미 공개된 글은 공개 상태 유지
            post = wp.update_post(update_post_id, title=summary.synthesis.seo.title or meta.title, content=html,
                                  status=status, slug=summary.synthesis.seo.slug, excerpt=build_excerpt(summary),
                                  categories=cats, tags=tags, featured_media=featured)
            removed = wp.delete_media_in(old.get("content", {}).get("raw", ""), keep=set(url_map.values()))
            old_feat = old.get("featured_media") or 0
            if old_feat and old_feat != featured:
                try:
                    wp.s.delete(f"{wp.api}/media/{old_feat}", params={"force": "true"}, timeout=60); removed += 1
                except Exception:  # noqa: BLE001
                    pass
            log(f"  기존 글 갱신 (이전 이미지 {removed}개 삭제)")
        else:
            post = wp.create_post(
                title=summary.synthesis.seo.title or meta.title,
                content=html, status=status, slug=summary.synthesis.seo.slug,
                excerpt=build_excerpt(summary), categories=cats, tags=tags, featured_media=featured,
            )
        final = "published" if status == "publish" else ("needs_review" if summary.needs_review else "drafted")
        state.mark_post_created(vid, final, wp_post_id=post["id"], wp_link=post.get("link", ""))
        log(f"  WordPress {status}: {post.get('link', '')}")
        return final
    except Exception as e:  # noqa: BLE001
        state.mark_failed(vid, "publish", str(e))
        raise


def _wp_client(settings: Settings, needed: bool):
    if not needed:
        return None
    from .wordpress import WordPressClient
    return WordPressClient(settings.wp_url, settings.wp_user, settings.wp_app_password)


def _recent_counts(settings: Settings, state: State, wp) -> tuple[int, int]:
    """(최근 7일 생성 글 수, 최근 24시간 생성 글 수). WordPress 가 있으면 거기서, 없으면 상태 파일에서."""
    if wp is not None:
        return wp.count_recent_posts(7), wp.count_recent_posts(1)
    return state.count_recent_posts(7), state.count_recent_posts(1)


def quota_left(settings: Settings, state: State, wp) -> tuple[int, str]:
    """남은 발행 여유(글 수)와 설명. 상한이 0 이면 무제한."""
    week, day = _recent_counts(settings, state, wp)
    left = 10**9
    why = []
    if settings.max_posts_per_week:
        left = min(left, settings.max_posts_per_week - week)
        why.append(f"7일 {week}/{settings.max_posts_per_week}")
    if settings.max_posts_per_day:
        left = min(left, settings.max_posts_per_day - day)
        why.append(f"24시간 {day}/{settings.max_posts_per_day}")
    return max(0, left), ", ".join(why) or "상한 없음"


def cmd_run(args, settings: Settings) -> int:
    channels = load_channels()
    state = State(settings.data_dir / "state.json")
    _resolve_all(settings, channels, state)
    wp = _wp_client(settings, needed=not args.dry_run and bool(settings.wp_url))
    if wp is None and not args.dry_run:
        log("WP_URL 이 없어 로컬 미리보기(out/)만 생성합니다.")
    processed = 0
    failures = 0
    # 발행 상한: 글(초안 포함)을 실제로 만드는 실행에만 적용. --video 지정·--dry-run 은 예외
    budget = None
    if not args.dry_run and not args.video:
        left, why = quota_left(settings, state, wp)
        budget = left
        log(f"발행 여유: {left}편 ({why})")
        if left <= 0:
            log("발행 상한에 도달해 이번 실행은 건너뜁니다. (MAX_POSTS_PER_WEEK / MAX_POSTS_PER_DAY)")
            return 0
    for ch in channels:
        if not ch.enabled:
            continue
        vids = list_recent_videos(ch.channel_id, settings.yt_proxy_url)
        for v in vids:
            if args.video and v.video_id != args.video:
                continue
            if not args.video and state.is_done(v.video_id):
                continue
            if wp is not None and not args.video and wp.find_post_by_video(v.video_id):
                state.set_status(v.video_id, "published", note="이미 WordPress 에 존재")
                continue
            if processed >= args.limit or (budget is not None and processed >= budget):
                break
            log(f"\n▶ {v.video_id} {v.title}")
            try:
                process_video(settings, ch, v, state, publish=args.publish, dry_run=args.dry_run, wp=wp)
            except Exception:  # noqa: BLE001
                failures += 1
                log("  실패:\n" + traceback.format_exc(limit=3))
            processed += 1
    log(f"\n처리 {processed}건, 실패 {failures}건")
    return 1 if failures else 0


def cmd_fixture(args, settings: Settings) -> int:
    """샘플 자막으로 전체 흐름(요약→이미지→HTML)을 실행. MOCK_LLM 이 아니면 실제 API 호출."""
    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    transcript = Transcript(video_id=data["video_id"], language=data["language"], source=data["source"],
                            segments=data["segments"])
    meta = VideoMeta(video_id=data["video_id"], title=data.get("title", "샘플 영상: 미루는 습관의 뇌과학"),
                     channel_id="UCSAMPLE", channel_title=data.get("channel_title", "지식인사이드"),
                     duration_sec=transcript.duration_sec)
    channel = ChannelConfig(handle="@sample", channel_id="UCSAMPLE", title=meta.channel_title, min_duration_sec=0)
    state = State(settings.data_dir / "state.json")
    status = process_video(settings, channel, meta, state, transcript=transcript, dry_run=True)
    log(f"status: {status}")
    return 0


VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})")


def parse_video_id(s: str) -> str:
    s = s.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return s
    m = VIDEO_ID_RE.search(s)
    if not m:
        raise SystemExit(f"영상 ID 를 찾지 못했습니다: {s}")
    return m.group(1)


def _channel_for(channels: list[ChannelConfig], channel_id: str) -> ChannelConfig:
    for ch in channels:
        if ch.channel_id and ch.channel_id == channel_id:
            return ch
    for ch in channels:
        if ch.enabled:
            return ch
    return channels[0]


def cmd_prepare(args, settings: Settings) -> int:
    """수동 모드 1단계: 링크 하나를 받아 메타데이터와 자막을 out/<id>/ 에 저장한다(LLM 호출 없음)."""
    vid = parse_video_id(args.video)
    state = State(settings.data_dir / "state.json")
    out_dir = settings.out_dir / vid
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = enrich_video(VideoMeta(video_id=vid, title="", channel_id="", channel_title=""), settings.yt_proxy_url)
    channels = load_channels()
    ch = _channel_for(channels, meta.channel_id)
    if not meta.channel_title:
        meta.channel_title = ch.title or ch.handle
    transcript = fetch_transcript(vid, (ch.language_hint, "en"), settings.yt_proxy_url)
    (out_dir / "meta.json").write_text(json.dumps(dataclasses.asdict(meta), ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "transcript.txt").write_text(transcript.to_text(), encoding="utf-8")
    (out_dir / "transcript.json").write_text(json.dumps(dataclasses.asdict(transcript), ensure_ascii=False), encoding="utf-8")
    state.set_status(vid, "fetched", transcript_source=transcript.source, title=meta.title, mode="manual")
    log(f"영상: {meta.title}\n채널: {meta.channel_title} ({meta.channel_id})\n길이: {meta.duration_sec}s, 챕터 {len(meta.chapters)}개, "
        f"자막 {transcript.source}/{transcript.language} {len(transcript.segments)}구간 {len(transcript.to_text())}자")
    log(f"저장: {out_dir / 'meta.json'}, {out_dir / 'transcript.txt'}")
    if meta.chapters:
        log("챕터:\n" + "\n".join(f"  {c['start']:>5}s  {c['title']}" for c in meta.chapters))
    log(f"다음: {out_dir / 'summary.json'} 을 schema.Summary 형식으로 만든 뒤 `python -m ytblog finish {vid}`")
    return 0


def cmd_finish(args, settings: Settings) -> int:
    """수동 모드 2단계: out/<id>/summary.json 을 읽어 이미지·글·WordPress 업로드까지 진행한다."""
    vid = parse_video_id(args.video)
    out_dir = settings.out_dir / vid
    meta_path, sum_path = out_dir / "meta.json", Path(args.summary) if args.summary else out_dir / "summary.json"
    if not meta_path.exists():
        raise SystemExit(f"{meta_path} 가 없습니다. 먼저 `python -m ytblog prepare {vid}` 를 실행하세요.")
    if not sum_path.exists():
        raise SystemExit(f"{sum_path} 가 없습니다.")
    meta = VideoMeta(**json.loads(meta_path.read_text(encoding="utf-8")))
    summary = Summary.model_validate_json(sum_path.read_text(encoding="utf-8"))
    if summary.video_id != vid:
        raise SystemExit(f"summary.video_id({summary.video_id}) 가 {vid} 와 다릅니다")
    ch = _channel_for(load_channels(), meta.channel_id)
    state = State(settings.data_dir / "state.json")
    state.set_status(vid, "summarized", cost_usd=summary.cost_usd, unsupported_ratio=summary.unsupported_ratio, mode="manual")
    wp = _wp_client(settings, needed=not args.dry_run and bool(settings.wp_url))
    update_id = 0
    if wp is not None:
        existing = state.data["videos"].get(vid, {}).get("wp_post_id") or 0
        if not existing:
            found = wp.find_post_by_video(vid)
            existing = found["id"] if found else 0
        if existing and args.update:
            update_id = int(existing)
        elif existing and not args.force:
            raise SystemExit(f"이미 WordPress 에 {vid} 글(id {existing})이 있습니다. 갱신하려면 --update, 새로 올리려면 --force")
    log(f"▶ {vid} {meta.title}\n  요약: 구간 {len(summary.sections)}개, 핵심 {len(summary.synthesis.key_takeaways)}개")
    status = finish_video(settings, ch, meta, summary, state, out_dir, publish=args.publish, dry_run=args.dry_run,
                          wp=wp, update_post_id=update_id)
    log(f"status: {status}")
    if args.queue:
        from . import queue as q
        v = state.data["videos"].get(vid, {})
        if status == "published":
            q.mark(args.queue, "done", postUrl=v.get("wp_link", ""), title=summary.synthesis.seo.title or meta.title)
            log(f"대기열 완료 표시: {args.queue}")
        else:
            q.mark(args.queue, "working", err=f"상태 {status}")
            log("대기열: 작업 중 유지 (공개 아님)")
    return 0


def cmd_note(args, settings: Settings) -> int:
    """편집자 메모를 저장하고, 해당 영상의 WordPress 글이 있으면 본문을 갱신(선택: 공개)."""
    text = Path(args.file).read_text(encoding="utf-8") if args.file else (args.text or "")
    if not text.strip():
        log("메모 내용이 비어 있습니다. 텍스트 또는 --file 을 주세요.")
        return 2
    state = State(settings.data_dir / "state.json")
    state.set_note(args.video_id, text)
    log(f"메모 저장: {args.video_id} ({len(text.strip())}자)")
    v = state.data["videos"].get(args.video_id, {})
    post_id = v.get("wp_post_id")
    if not post_id:
        log("아직 WordPress 글이 없습니다. 다음 run 에서 이 메모가 글에 들어갑니다.")
        return 0
    wp = _wp_client(settings, True)
    post = wp.get_post(post_id)
    raw = post.get("content", {}).get("raw", "")
    fields = {"content": replace_note_block(raw, text, is_draft=False)}
    if args.publish:
        if v.get("status") == "needs_review":
            log("근거 검증 미달 글이라 공개하지 않고 본문만 갱신합니다. 관리자 화면에서 확인 후 공개하세요.")
        else:
            fields["status"] = "publish"
    updated = wp.update_post(post_id, **fields)
    if updated.get("status") == "publish":
        state.mark_post_created(args.video_id, "published", wp_link=updated.get("link", ""))
    log(f"WordPress 글 갱신 ({updated.get('status')}): {updated.get('link', '')}")
    return 0


def cmd_queue(args, settings: Settings) -> int:
    from . import queue as q
    if args.action == "list":
        items = q.pending()
        if not items:
            log("대기 중인 영상이 없습니다."); return 0
        for it in items:
            log(f"{it.get('_id') or it.get('id')}  {it.get('vid')}  {it.get('url')}  {('· ' + it['note']) if it.get('note') else ''}")
        return 0
    if args.action == "start":
        q.mark(args.doc_id, "working"); log(f"작업 중: {args.doc_id}"); return 0
    if args.action == "done":
        q.mark(args.doc_id, "done", postUrl=args.value, title=" ".join(args.rest)); log(f"완료: {args.doc_id} {args.value}"); return 0
    if args.action == "fail":
        q.mark(args.doc_id, "failed", err=" ".join([args.value] + args.rest)); log(f"실패 표시: {args.doc_id}"); return 0
    log(f"알 수 없는 동작: {args.action}"); return 2


def cmd_banner(args, settings: Settings) -> int:
    from .site import update_banner
    sub = update_banner(settings, args.head or "", args.sub or "")
    log(f"배너 갱신 완료: {sub}")
    return 0


def cmd_stats(args, settings: Settings) -> int:
    from .queue import BOARD_JS
    from .site import site_stats
    s = site_stats(settings, BOARD_JS)
    log(f"오늘 방문자 {s['today']['visitors']}명 · 조회 {s['today']['pageviews']}회 | 7일 {s['d7']['visitors']}명 · {s['d7']['pageviews']}회 | 30일 {s['d30']['visitors']}명 · {s['d30']['pageviews']}회")
    for t in s["top"]:
        log(f"  - {t['title']}  {t['pageviews']}회")
    return 0


def cmd_report(args, settings: Settings) -> int:
    from .queue import BOARD_JS
    from .site import weekly_report
    text = weekly_report(settings, BOARD_JS, post_to_board=not args.no_post)
    log(text)
    return 0


def cmd_threads(args, settings: Settings) -> int:
    from . import threads as th
    if args.cmd == "threads-auth":
        r = th.exchange_code(); log(f"연결됨: @{r['username']} (user_id {r['user_id']}, 만료까지 {r['expires_days']}일). .env 에 저장했습니다.")
    elif args.cmd == "threads-refresh":
        log(th.refresh_if_needed(force=True))
    else:
        pid = th.post("지식채우기 연결 테스트예요. 30분짜리 강연을 5분 글로 정리하는 블로그, jisikfill.com")
        log(f"게시 완료: id {pid}")
    return 0


def cmd_quota(args, settings: Settings) -> int:
    state = State(settings.data_dir / "state.json")
    wp = _wp_client(settings, bool(settings.wp_url))
    left, why = quota_left(settings, state, wp)
    log(f"발행 여유: {left}편 ({why}) — 기준: {'WordPress' if wp else '로컬 상태 파일'}")
    return 0


def cmd_wp_check(args, settings: Settings) -> int:
    wp = _wp_client(settings, True)
    me = wp.check()
    log(f"연결 OK: {me.get('name')} ({', '.join(me.get('roles', []))}) @ {settings.wp_url}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ytblog", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve"); r.add_argument("handle")
    sub.add_parser("discover")
    run = sub.add_parser("run")
    run.add_argument("--limit", type=int, default=1)
    run.add_argument("--video", default="")
    run.add_argument("--publish", action="store_true", help="검토 없이 바로 공개(기본은 WordPress 초안)")
    run.add_argument("--dry-run", action="store_true", help="WordPress 에 올리지 않고 out/ 에만 저장")
    fx = sub.add_parser("fixture"); fx.add_argument("--file", default=str(Path(__file__).resolve().parent.parent / "tests/fixtures/transcript_sample.json"))
    sub.add_parser("wp-check")
    nt = sub.add_parser("note", help="편집자 메모 저장(+WordPress 글 갱신)")
    nt.add_argument("video_id"); nt.add_argument("text", nargs="?", default="")
    nt.add_argument("--file", default="", help="메모를 담은 텍스트 파일")
    nt.add_argument("--publish", action="store_true", help="메모 반영 후 글을 공개로 전환")
    sub.add_parser("quota", help="발행 상한 대비 남은 여유")
    pr = sub.add_parser("prepare", help="수동 모드 1단계: 링크→메타·자막 저장"); pr.add_argument("video")
    fi = sub.add_parser("finish", help="수동 모드 2단계: summary.json→이미지·글·발행"); fi.add_argument("video")
    fi.add_argument("--summary", default="", help="summary.json 경로(기본 out/<id>/summary.json)")
    fi.add_argument("--publish", action="store_true"); fi.add_argument("--dry-run", action="store_true")
    fi.add_argument("--force", action="store_true", help="이미 글이 있어도 새 글로 다시 올림")
    fi.add_argument("--update", action="store_true", help="이미 글이 있으면 그 글을 갱신(이전 이미지 삭제)")
    fi.add_argument("--queue", default="", help="다빈보드 대기열 문서 id (발행 후 완료 표시)")
    bn = sub.add_parser("banner", help="홈 배너 갱신"); bn.add_argument("head", nargs="?", default=""); bn.add_argument("sub", nargs="?", default="")
    rp = sub.add_parser("report", help="주간 리포트"); rp.add_argument("--no-post", action="store_true", help="보드에 올리지 않고 출력만")
    sub.add_parser("stats", help="방문자·조회수 집계 (다빈보드 블로그 탭에도 기록)")
    for name in ("threads-auth", "threads-refresh", "threads-test"):
        sub.add_parser(name, help="스레드 연동")
    qu = sub.add_parser("queue", help="다빈보드 블로그 대기열")
    qu.add_argument("action", nargs="?", default="list", choices=["list", "start", "done", "fail"])
    qu.add_argument("doc_id", nargs="?", default=""); qu.add_argument("value", nargs="?", default=""); qu.add_argument("rest", nargs="*")
    args = p.parse_args(argv)
    settings = Settings.load()
    return {"resolve": cmd_resolve, "discover": cmd_discover, "run": cmd_run,
            "fixture": cmd_fixture, "wp-check": cmd_wp_check,
            "note": cmd_note, "quota": cmd_quota,
            "prepare": cmd_prepare, "finish": cmd_finish, "queue": cmd_queue,
            "banner": cmd_banner, "report": cmd_report, "stats": cmd_stats,
            "threads-auth": cmd_threads, "threads-refresh": cmd_threads, "threads-test": cmd_threads}[args.cmd](args, settings)


if __name__ == "__main__":
    sys.exit(main())
