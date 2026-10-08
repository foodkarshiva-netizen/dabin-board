# Talkpen

영어회화 연습 앱. 다빈보드와 같은 구조: GitHub Pages + Google 로그인 + Firestore, 답은 내 클로드 세션이 🔔 호출을 받고 단다.

- `index.html` — 앱 화면 (마이크로 말하기, 뜻 통했는지 / 원어민 표현 / 대화)
- `SESSION.md` — 답하는 클로드 세션이 읽을 지침

## Firestore

- `english_turns` — 사용자 턴(`role: "user"`)과 AI 답(`role: "ai"`, `re: <사용자 턴 id>`)이 한 컬렉션에
- `debate_state/_wake` — 다빈보드와 같은 호출 문서. sids에 `english`를 추가

비밀키는 이 저장소에 절대 올리지 않는다.
