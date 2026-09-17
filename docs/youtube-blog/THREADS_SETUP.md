# 스레드(Threads) 자동 발행 준비 안내

지식채우기 블로그 글이 발행되면 핵심 카드 한 장과 요약을 스레드에 자동으로 올리기 위한 준비입니다.
직접 하실 일은 **1~3단계(약 20분)**이고, 4단계부터는 명령 한 줄이면 끝납니다. 비밀번호·시크릿·토큰은 채팅에 올리지 말고 `.env` 파일에만 넣어 주세요.

---

## 1단계. 스레드 계정 만들기 (5분)

1. 휴대폰에 **Threads 앱**을 설치하고, 인스타그램 계정으로 로그인합니다. 인스타그램이 없으면 인스타그램 앱에서 먼저 계정을 만듭니다.
2. 아이디는 블로그와 맞추는 걸 권합니다. 예: `jisikfill` (인스타그램 아이디가 곧 스레드 아이디입니다).
3. 프로필을 채웁니다.
   - 이름: 지식채우기
   - 소개: "30분짜리 강연, 5분이면 다 읽어요. 유튜브 강연 핵심 정리"
   - 링크: `https://jisikfill.com`
   - 프로필 사진: 워드프레스에 올린 로고(먹색 바탕 "지")를 쓰면 됩니다. 제가 파일로 드릴 수 있습니다.
4. 글 하나를 아무거나 올려 둡니다. 빈 계정은 API 테스트 때 간혹 막힙니다.

---

## 2단계. Meta 개발자 앱 만들기 (10분)

스레드 API는 Meta(페이스북)의 개발자 앱을 통해 씁니다. 심사(앱 검수) 없이 **내 계정에만** 올리는 용도라면 아래만 하면 됩니다.

1. PC 브라우저에서 https://developers.facebook.com 접속 → 오른쪽 위 **시작하기** → 인스타그램/페이스북 계정으로 로그인 → 개발자 계정 등록(전화번호 인증).
2. 상단 **내 앱** → **앱 만들기**.
3. "사용 사례" 화면에서 **Threads API 액세스**(Access the Threads API)를 선택 → 다음.
4. 앱 이름: `jisikfill-threads`, 연락처 이메일 입력 → **앱 만들기**.
5. 앱 대시보드 왼쪽 메뉴 **사용 사례** → Threads API 옆 **사용자 지정** → 권한(Permissions)에서 다음 두 개가 켜져 있는지 확인:
   - `threads_basic`
   - `threads_content_publish`
6. 왼쪽 메뉴 **앱 설정 → 기본 설정**에서 다음 두 값을 복사해 둡니다. (시크릿은 "표시" 클릭)
   - **Threads 앱 ID** → `.env`의 `THREADS_APP_ID`
   - **Threads 앱 시크릿** → `.env`의 `THREADS_APP_SECRET`
7. 같은 화면(또는 "사용 사례 → Threads API 설정")의 **리디렉션 콜백 URL**에 `https://jisikfill.com/` 을 넣고 저장합니다.
   "인증 취소 콜백 URL", "데이터 삭제 요청 URL"도 같은 주소를 넣으면 됩니다.
8. **Threads 테스터 등록**(내 계정으로 게시하려면 필수):
   - 왼쪽 메뉴 **앱 역할 → 역할** → **사람 추가** → 역할 "Threads 테스터" → 스레드 아이디(`jisikfill`) 입력 → 추가.
   - 휴대폰 Threads 앱 → 프로필 → 오른쪽 위 **≡ → 계정 → 웹사이트 권한 → 초대**에서 방금 초대를 **수락**합니다.

---

## 3단계. 인증 코드 받기 (2분)

1. `.env` 파일(`C:\Users\karsh\dev\ytblog-repo\pipeline\.env`)을 메모장으로 열어 아래 세 줄을 채웁니다.
   ```
   THREADS_APP_ID=여기에_앱ID
   THREADS_APP_SECRET=여기에_앱시크릿
   THREADS_REDIRECT_URI=https://jisikfill.com/
   ```
2. 아래 주소의 `앱ID` 자리에 앱 ID를 넣어 PC 브라우저 주소창에 붙여 넣습니다. (스레드 계정으로 로그인된 브라우저)
   ```
   https://threads.net/oauth/authorize?client_id=앱ID&redirect_uri=https://jisikfill.com/&scope=threads_basic,threads_content_publish&response_type=code
   ```
3. 권한 허용 화면에서 **허용**을 누르면 `https://jisikfill.com/?code=AQB...#_` 처럼 블로그로 돌아옵니다. 주소창의 **`code=` 뒤의 값**(끝의 `#_` 제외)을 복사합니다.
4. `.env`에 한 줄 추가하고 저장합니다.
   ```
   THREADS_AUTH_CODE=복사한값
   ```
   이 코드는 몇 분만 유효하니 바로 4단계로 갑니다.

---

## 4단계. 토큰 발급 (명령 한 줄)

```bash
cd /c/Users/karsh/dev/ytblog-repo/pipeline && PYTHONUTF8=1 .venv/Scripts/python.exe -m ytblog threads-auth
```

- 성공하면 `연결됨: @jisikfill (만료까지 60일)` 처럼 나오고, 60일짜리 장기 토큰과 사용자 ID가 `.env`에 자동 저장됩니다. 코드 줄은 자동으로 비워집니다.
- 실패 메시지가 나오면 그대로 알려 주세요. 흔한 원인은 (1) 테스터 초대를 수락하지 않음, (2) 리디렉션 URL이 앱 설정과 다름, (3) 코드가 만료됨(3단계 다시).

토큰은 60일마다 갱신해야 하는데, 주간 리포트 작업이 만료 7일 전이면 자동으로 갱신합니다. 수동 갱신은 `python -m ytblog threads-refresh` 입니다.

---

## 5단계. 연결 확인

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe -m ytblog threads-test
```

스레드에 "지식채우기 연결 테스트" 글이 올라가면 끝입니다. 확인 후 그 글은 앱에서 지워도 됩니다.

---

## 그다음 (제가 합니다)

- 블로그 글 발행 직후: 핵심 포인트 세로 카드(1080×1350) 1장 + 한 줄 요약 + 핵심 3개 + `jisikfill.com` 링크를 스레드에 자동 게시.
- 매일 9시 5분 발행 작업에 이어 붙이고, 주간 리포트에 스레드 게시 수를 추가.
- 스레드 API 한도는 24시간 250건이라 하루 1~2편은 여유가 큽니다.

## 자주 막히는 곳

| 증상 | 원인 | 해결 |
|---|---|---|
| 인증 URL에서 "잘못된 리디렉션" 오류 | 앱에 등록한 콜백 URL과 주소가 다름 | 둘 다 정확히 `https://jisikfill.com/` (끝 슬래시 포함) |
| threads-auth에서 "user not tester" 류 오류 | 테스터 초대 미수락 | 휴대폰 Threads 앱 → 설정 → 계정 → 웹사이트 권한 → 초대 수락 |
| 게시는 되는데 이미지가 안 뜸 | 이미지 주소가 비공개이거나 8MB 초과 | 워드프레스 미디어 공개 URL 사용(파이프라인이 자동 처리) |
| 60일 뒤 갑자기 게시 실패 | 토큰 만료 | `threads-refresh` 또는 3~4단계 다시 |
