"""명령줄 인터페이스.

  python -m ytblog resolve "@지식인사이드"        핸들 → channel_id 확인
  python -m ytblog discover                        등록 채널의 새 영상 목록
  python -m ytblog run [--limit N] [--video ID] [--publish] [--dry-run]
  python -m ytblog fixture                         샘플 자막으로 오프라인 전체 흐름 실행
  python -m ytblog wp-check                        WordPress 연결 확인
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from .config import ChannelConfig, Settings, load_channels
from .discover import VideoMeta, enrich_video, list_recent_videos, resolve_channel
from .images import build_cards
from .render import build_excerpt, build_post_html
from .state import State
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

    # 4) 이미지
    try:
        cards = build_cards(summary, meta, settings.blog_name, out_dir / "images")
        state.set_status(vid, "illustrated", images=len(cards))
        log(f"  이미지 {len(cards)}장 생성")
    except Exception as e:  # noqa: BLE001
        state.mark_failed(vid, "illustrate", str(e))
        raise

    # 5) 글 조립 (+ 업로드) — 로컬 미리보기는 항상 저장
    url_map: dict[str, str] = {}
    featured = 0
    if wp is not None and not dry_run:
        try:
            for c in cards:
                mid, url = wp.upload_media(c.path, c.alt, f"{meta.title} - {c.kind}")
                url_map[str(c.path)] = url
                if c.kind == "hero":
                    featured = mid
        except Exception as e:  # noqa: BLE001
            state.mark_failed(vid, "upload", str(e))
            raise
    html = build_post_html(summary, meta, cards, url_map or {str(c.path): f"images/{c.path.name}" for c in cards}, settings.blog_name)
    preview = f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>{summary.synthesis.seo.title}</title>" \
              "<style>body{max-width:760px;margin:40px auto;font-family:sans-serif;line-height:1.7;padding:0 16px}img{max-width:100%;border-radius:12px}blockquote{border-left:4px solid #ddd;margin:0;padding:4px 16px;color:#555}.yt-box{background:#f4f6fb;padding:12px 16px;border-radius:12px}</style></head><body>" \
              f"<h1>{summary.synthesis.seo.title}</h1>{html}</body></html>"
    (out_dir / "post.html").write_text(preview, encoding="utf-8")

    if wp is None or dry_run:
        state.set_status(vid, "drafted", preview=str(out_dir / "post.html"))
        log(f"  미리보기 저장: {out_dir / 'post.html'}")
        return "drafted"

    # 6) 발행
    try:
        status = "publish" if (publish and not summary.needs_review) else "draft"
        cats = wp.category_ids([channel.blog_category])
        tags = wp.tag_ids(list(dict.fromkeys(summary.synthesis.seo.tags + channel.extra_tags)))
        post = wp.create_post(
            title=summary.synthesis.seo.title or meta.title,
            content=html, status=status, slug=summary.synthesis.seo.slug,
            excerpt=build_excerpt(summary), categories=cats, tags=tags, featured_media=featured,
        )
        final = "published" if status == "publish" else ("needs_review" if summary.needs_review else "drafted")
        state.set_status(vid, final, wp_post_id=post["id"], wp_link=post.get("link", ""))
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


def cmd_run(args, settings: Settings) -> int:
    channels = load_channels()
    state = State(settings.data_dir / "state.json")
    _resolve_all(settings, channels, state)
    wp = _wp_client(settings, needed=not args.dry_run and bool(settings.wp_url))
    if wp is None and not args.dry_run:
        log("WP_URL 이 없어 로컬 미리보기(out/)만 생성합니다.")
    processed = 0
    failures = 0
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
            if processed >= args.limit:
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
    args = p.parse_args(argv)
    settings = Settings.load()
    return {"resolve": cmd_resolve, "discover": cmd_discover, "run": cmd_run,
            "fixture": cmd_fixture, "wp-check": cmd_wp_check}[args.cmd](args, settings)


if __name__ == "__main__":
    sys.exit(main())
