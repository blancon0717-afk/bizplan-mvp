# 인수인계 — 개발 환경 세팅 가이드

이 문서 하나만 순서대로 따라 하면 원 개발자와 **동일한 개발 환경**이 만들어진다.

> ⚠️ **`README.md`는 보지 말 것.** 구버전(Streamlit 단일 앱) 기준이라 지금 구조와 다르다.
> 프로젝트 구조·개발 규칙은 **`CLAUDE.md`**가 유일한 기준 문서다.

---

## 0. 현재 구조 요약 (30초)

| 구분 | 내용 |
|---|---|
| 백엔드 | FastAPI, 포트 **8000**, 진입점 `backend/main.py` |
| 프론트엔드 | Next.js, 포트 **3000**, `frontend/` |
| LLM | Claude Sonnet (`claude-sonnet-4-6`) — 전 구간 단일 모델, Haiku 미사용 |
| 계정 DB | SQLite (`backend/data/bizplan.db`) — 회원 인증용 |
| 세션 | 파일 기반 JSON (`data/sessions/*.json`) |
| 배포 | Railway (`backend/Procfile`) |

원 개발자 환경 기준 버전 (동일하게 맞추면 안전):

```
Python 3.14.2
Node    v24.14.0
npm     11.9.0
OS      Windows 11 / PowerShell
```

Python은 3.11 이상이면 대체로 동작하지만, 문제가 생기면 3.14로 맞출 것.

---

## 1. 클론

```bash
git clone https://github.com/blancon0717-afk/bizplan-mvp.git
cd bizplan-mvp
```

---

## 2. 파이썬 환경

```powershell
python -m venv .venv
.\.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

> `requirements.txt`와 `requirements-backend.txt` 두 개가 있다.
> - `requirements.txt` — **로컬 개발용. 이걸 설치할 것.** (원 개발자와 동일한 세트)
> - `requirements-backend.txt` — Railway 배포용 최소 세트. 로컬에서 설치할 필요 없음.

---

## 3. 프론트엔드 의존성

```bash
cd frontend
npm install
cd ..
```

---

## 4. 백엔드 `.env` 생성 ← **유일하게 원 개발자 도움이 필요한 단계**

```bash
cp .env.example .env
```

그리고 아래 표대로 값을 채운다.

| 키 | 어떻게 채우나 | 필수 |
|---|---|---|
| `ANTHROPIC_API_KEY` | **원 개발자에게 요청** 또는 본인 키 발급 (console.anthropic.com) | ✅ |
| `AUTH_SECRET` | **직접 생성** — 아래 명령 참고 | ✅ |
| `UNLOCK_SECRET` | **직접 생성** — 같은 방법, 다른 값 | ✅ |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | ✅ |
| `APP_BASE_URL` | `http://localhost:3000` | ✅ |
| `MOCK_MODE` | `0` (API 호출 없이 UI만 볼 땐 `1`) | ✅ |
| `RESEND_API_KEY` | **비워둘 것** — 미설정 시 메일 대신 인증 링크가 서버 로그에 출력됨 | ❌ |
| `MAIL_FROM` | 비워둘 것 | ❌ |
| `NOTION_API_KEY` | **비워둘 것** — 노션 재동기화 시에만 필요. 캐시가 이미 저장소에 있음 | ❌ |
| `NOTION_FEEDBACK_PAGE_ID` | 비워둘 것 | ❌ |
| `DB_PATH` | 비워둘 것 (로컬 기본값 사용) | ❌ |
| `SESSIONS_DIR` | 비워둘 것 (로컬 기본값 사용) | ❌ |

**시크릿 생성 명령** (두 번 실행해서 서로 다른 값을 넣을 것):

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

`random`이 아니라 `secrets` 모듈을 쓰는 이유는 예측 불가능한 난수가 필요하기 때문이다.
`token_hex(32)` = 32바이트 = 64자리 16진수.

### `AUTH_SECRET`이 뭔가 (왜 없으면 서버가 안 켜지나)

로그인 정보를 DB 세션 테이블에 저장하지 않는 구조다. 대신 쿠키에
`session|{user_id}|{만료시각}` 문자열을 담고, 뒤에 **서버만 아는 키로 만든 HMAC 서명**을
붙인다 (`backend/auth.py`). 사용자가 쿠키의 user_id를 바꿔치기해도 서명이 어긋나 거부된다.

이 서명키가 `AUTH_SECRET`이다. 로그인 쿠키(14일) · 이메일 인증 링크(24시간) ·
비밀번호 재설정 링크(1시간) 세 가지 모두 이 키로 서명한다.

키가 없거나 32자 미만이면 `backend/main.py`의 lifespan에서 `require_auth_secret()`이
**앱 기동 시점에 에러를 내고 서버가 뜨지 않는다.** 의도된 동작이다 —
키 없이도 서버가 켜지게 두면 회원가입 버튼을 누르는 순간에야 500이 터져 원인을 늦게 찾는다.

- 로컬 키는 개발자마다 **달라도 무방**하다. 단, 키를 바꾸면 그 환경의 기존 로그인 쿠키는 전부 무효가 된다.
- **운영(Railway) 키는 절대 변경 금지.** 바꾸는 순간 전 회원이 로그아웃되고 발송된 메일 링크가 전부 죽는다.

`UNLOCK_SECRET`은 결제 언락 코드(129,000원 상품) 서명용이다. 로컬에서는 본인이 만든 값으로
`scripts/issue_unlock_code.py`를 돌려 테스트 코드를 발급하면 된다.
**운영 키를 로컬로 가져오지 말 것** — 유출되면 누구나 언락 코드를 위조할 수 있다.

---

## 5. 프론트엔드 `.env.local` 생성

```bash
cp frontend/.env.example frontend/.env.local
```

내용은 `NEXT_PUBLIC_BACKEND_URL=http://localhost:8000` 한 줄이다. 수정할 것 없음.

---

## 6. 실행

```powershell
# 터미널 1 — 백엔드
.\start_backend.ps1
```

```bash
# 터미널 2 — 프론트엔드
cd frontend
npm run dev
```

`start_backend.ps1`은 포트 8000 점유 프로세스를 먼저 정리한 뒤 단일 인스턴스로 띄우는
스크립트다. **여러 개 띄우면 세션 파일이 꼬이므로 반드시 이 스크립트로 실행할 것.**

> **알려진 사소한 문제:** 스크립트 마지막의 토스트 알림이
> `%USERPROFILE%\.claude\notify.ps1`을 호출한다. 그 파일이 없으면 "파일을 찾을 수 없음"
> 에러가 뜨지만 **백엔드는 이미 정상 기동한 상태**이므로 무시해도 된다.
> 거슬리면 `start_backend.ps1`의 `notify.ps1` 호출 부분을 지우면 된다.

macOS/Linux는 이 스크립트가 안 돌아간다. 직접 실행:

```bash
cd backend && uvicorn main:app --host 0.0.0.0 --port 8000
```

---

## 7. 세팅 검증 체크리스트

순서대로 확인한다. 하나라도 실패하면 그 지점이 원인이다.

- [ ] `curl http://localhost:8000/health` → 정상 응답
- [ ] `http://localhost:3000` 접속 → 랜딩 화면 표시
- [ ] 기업정보 입력 → 인터뷰 10문항 진행 가능
- [ ] 초안 생성 완료 (실패 시 `logs/backend.log`에서 Anthropic API 에러 확인 → `ANTHROPIC_API_KEY` 문제)
- [ ] 회원가입 시도 → `logs/backend.log`에 이메일 인증 링크가 출력됨 (`RESEND_API_KEY` 미설정 시 정상 동작)
- [ ] 그 링크를 브라우저에 붙여넣기 → 인증 완료 → 로그인 성공

---

## 8. 저장소에 **없는** 것 — 그리고 없어도 되는 이유

아래는 의도적으로 커밋에서 제외했다. 개인정보 또는 자격증명이거나, 각자 생성되는 실행 상태다.

| 항목 | 이유 |
|---|---|
| `.env`, `delivery2/.env` | 실제 API 키·서명키. 커밋 시 히스토리에 영구 잔존 |
| `backend/data/bizplan.db` | 실제 회원 이메일·비밀번호 해시 (개인정보) |
| `data/leads.jsonl` | 리드 이메일 (개인정보) |
| `data/sessions/*.json` | 원 개발자의 테스트 세션. 직접 인터뷰를 돌리면 본인 세션이 생성됨 |
| `logs/` | 실행 로그 |
| `.venv/`, `node_modules/`, `.next/`, `__pycache__/` | 설치·빌드 산출물. 2·3단계에서 재생성됨 |

**데이터(자료)는 전부 저장소에 있다.** 확인된 목록:

| 자료 | 경로 |
|---|---|
| 양식 YAML 10개 | `data/forms/` |
| 공식 DOCX 템플릿 3개 | `data/templates/` |
| 지원사업 공고 CSV | `data/programs/` |
| 인터뷰 질문지 | `data/interview/` |
| 심사위원 피드백 데이터 | `data/reference/notion_feedback.json` |
| 공고 원본 양식 (docx/hwp) | `SAMPLE/` |
| 4계층 스킬 / 프롬프트 | `skills/`, `prompts/` |

> `data/feedback/`에 README만 있어서 데이터가 빠진 것처럼 보이지만, 실제 피드백 데이터는
> `data/reference/notion_feedback.json`으로 이전되었고 `core/notion_feedback.py`가 그 경로를 읽는다.
> (해당 파일 상단 주석만 옛 경로로 남아 있음 — 무시할 것)

---

## 9. 코드 작성 전 반드시 알아야 할 규칙 4가지

전체 규칙은 `CLAUDE.md`에 있다. 그중 모르면 바로 사고 나는 것만 추린다.

1. **`backend/` 내부 import에 `backend.` 접두사 금지**
   `from routers import ...`, `from services import ...` 형태로 작성.
   Railway Procfile이 `cd backend && uvicorn main:app`으로 실행하므로
   `backend.` 접두사를 쓰면 배포에서 `ModuleNotFoundError`가 난다.

2. **DOCX 텍스트 색상은 `_COLOR_BLACK`만 사용** (`core/docx_export.py`)
   `_COLOR_GRAY`는 정의되어 있지 않다. llm_inferred 세그먼트 포함 전부 검정.

3. **새 양식 추가 시 5곳을 함께 수정** — 누락하면 화면 노출과 실제 파일이 불일치한다.
   `CLAUDE.md`의 "새 양식 추가 시 체크리스트" 참조.

4. **규칙을 추가할 위치가 정해져 있다** — 아무 데나 쓰면 안 된다.
   - 서식 → `skills/L1_universal/BIZPLAN_FORMAT.md`
   - 내용·완성도 → `skills/L1_universal/U01_numbers_with_sources.md`
   - 심사 피드백 → `skills/L3_program/P_judge_feedback_skill.md`
   - LLM 응답 구조 → `prompts/system.md`

---

## 10. Railway 배포 시 추가로 필요한 것 (참고)

로컬 개발만 할 거면 건너뛰어도 된다.

| 변수 | 값 |
|---|---|
| `AUTH_SECRET` | 운영 전용 값 (한번 정하면 **변경 금지**) |
| `UNLOCK_SECRET` | 운영 전용 값 |
| `ANTHROPIC_API_KEY` | 운영 키 |
| `APP_BASE_URL` | `https://` 로 시작하는 실제 도메인 (Secure 쿠키 플래그가 자동으로 붙음) |
| `RESEND_API_KEY`, `MAIL_FROM` | 실제 메일 발송용 |
| `DB_PATH` | **Volume 마운트 경로** (예: `/data/bizplan.db`) |
| `SESSIONS_DIR` | **Volume 마운트 경로** (예: `/data/sessions`) |

> 🔴 `DB_PATH`·`SESSIONS_DIR`을 Volume 경로로 지정하지 않으면
> **재배포할 때마다 전 회원 계정과 문서가 전부 삭제된다.**

미완료 항목: Railway에 `NOTION_API_KEY`·`NOTION_FEEDBACK_DB_ID` 미등록 상태.
