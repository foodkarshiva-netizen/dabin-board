# ytblog — 유튜브 요약 → 워드프레스 자동 발행 (Phase 0)

채널: **지식인사이드** (`channels.json`). 화면 캡처는 쓰지 않고 요약 내용으로 새로 만든 카드 이미지만 사용하며, 모든 글과 이미지에 출처를 표기합니다.

## 흐름

```
RSS로 새 영상 감지 → 자막 수집 → 4패스 요약(Claude) → 카드 이미지 렌더링(Playwright)
→ 글 HTML 조립(타임스탬프 링크·임베드·출처 고지) → WordPress 초안(기본) 또는 공개(--publish)
```

## 설치 (Python 3.11+, Node 18+)

```bash
cd pipeline
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
npm i -g playwright && npx playwright install chromium   # 카드 이미지 렌더링용
cp .env.example .env                                     # 값 채우기
```

한글 폰트: 카드에 Noto Sans KR을 씁니다. `render/fonts/NotoSansKR-Regular.otf`, `NotoSansKR-Bold.otf` 를
[noto-cjk 저장소](https://github.com/notofonts/noto-cjk/tree/main/Sans/SubsetOTF/KR)에서 받아 넣거나, OS에 한글 폰트가 있으면 그대로 동작합니다.

## 사용

```bash
python -m ytblog fixture                 # 샘플 자막으로 전체 흐름 확인 (MOCK_LLM=1 이면 API 호출 없음)
python -m ytblog resolve "@지식인사이드"  # channel_id 확인
python -m ytblog discover                # 최근 영상과 처리 상태
python -m ytblog wp-check                # 워드프레스 연결 확인
python -m ytblog run --limit 1 --dry-run # 영상 1편: out/<video_id>/ 에 post.html·images/ 만 생성
python -m ytblog run --limit 1           # 워드프레스에 '초안'으로 업로드 (관리자에서 확인 후 공개)
python -m ytblog run --limit 2 --publish # 검토 없이 바로 공개 (근거없음 20% 초과·비용 초과 시엔 초안으로)
python -m ytblog run --video <ID>        # 특정 영상 강제 처리
```

산출물(`out/<video_id>/`): `transcript.txt`, `summary.json`, `usage.txt`(토큰·비용), `images/*.png`, `post.html`(미리보기).
상태(`data/state.json`): 영상별 `discovered → fetched → summarized → illustrated → drafted/published`, 실패 시 `failed`(3회 넘으면 `dead`).

## 자동 실행

- **PC/NAS cron** (권장, 유튜브 차단이 가장 적음): `*/30 * * * * cd /path/pipeline && .venv/bin/python -m ytblog run --limit 2`
- **GitHub Actions**: `.github/workflows/ytblog.yml` (30분마다). 저장소 Secrets에 `ANTHROPIC_API_KEY`, `WP_URL`, `WP_USER`, `WP_APP_PASSWORD` 등록.
  GitHub 러너 IP는 유튜브 자막 요청이 차단될 때가 있어, 그 경우 `YT_PROXY_URL` Secret 을 추가하세요.
  중복 발행은 워드프레스에서 영상 ID를 검색해 막으므로 러너에 상태 파일이 남지 않아도 안전합니다.

## 비용

- 클로드 API(종량제)만 과금됩니다. 20분 영상 기준 약 $0.3~0.6, 실제 값은 `out/<id>/usage.txt` 에 기록됩니다.
- 영상당 `MAX_COST_PER_VIDEO_USD` 를 넘으면 자동 공개 대신 초안으로 남깁니다.

## 구조

| 파일 | 역할 |
|---|---|
| `ytblog/discover.py` | 핸들→channel_id, RSS 목록, 시청 페이지에서 길이·설명·챕터 |
| `ytblog/transcript.py` | 자막(수동→자동), yt-dlp 폴백, 프록시 |
| `ytblog/summarize.py` | 구간 분할 → 구간별 상세(Opus 5, 프롬프트 캐싱) → 종합 → 근거 검증(Sonnet 5) |
| `ytblog/schema.py` | 구조화 출력 스키마 |
| `ytblog/images.py`, `render/render_card.js` | HTML 카드 → PNG (대표·핵심포인트·흐름도·구간·수치) |
| `ytblog/render.py` | 글 HTML(임베드, 타임스탬프 링크, 용어집, FAQ, 출처 고지) |
| `ytblog/wordpress.py` | 미디어 업로드, 카테고리/태그, 글 생성 |
| `ytblog/state.py` | 상태 파일 |
| `ytblog/cli.py` | 명령 |

다음 단계(Phase 1): 다빈보드 앱에 "블로그" 탭을 붙여 Firestore 초안 승인/반려 → 발행. 설계는 `docs/youtube-blog/PLAN.md`.
