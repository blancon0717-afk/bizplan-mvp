# bizplan-mvp (구버전 아카이브 — Streamlit 기반)

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요 (구버전)

**사업계획서 AI MVP** (`bizplan-mvp/`) — 정부지원사업 사업계획서를 "1회 인터뷰 → 여러 양식"으로 자동 생성하는 Streamlit 앱.

보조 디렉토리:
- `bizplan-analysis/` — 합격/불합격 사업계획서 패턴 분석 리서치 문서

## 빌드 & 실행 (구버전)

```bash
cd bizplan-mvp
python -m venv .venv
.venv/Scripts/activate          # Windows
pip install -r requirements.txt
streamlit run app.py            # http://localhost:8501
```

Mock 모드(API 키 없이 UI 테스트): `MOCK_MODE=1 streamlit run app.py`

환경변수: `.env`에 `ANTHROPIC_API_KEY` 설정 필수. 모델은 `CLAUDE_MODEL` 환경변수로 변경 가능 (기본값: `claude-sonnet-4-6`).

## 아키텍처 (구버전)

### 파이프라인 흐름

```
양식 선택 → 인터뷰(60문항) → 답변→섹션 매핑 → Skill 선택 → 프롬프트 조립 → Claude 호출 → JSON 파싱 → 🟢🟡🔴 판정 → 보완 루프 → DOCX 내보내기
```

### 핵심 모듈 (`core/`)

| 파일 | 역할 |
|------|------|
| `llm.py` | Claude API 래퍼. 모든 호출을 `logs/llm_calls.jsonl`에 기록 (Phase 3 지도학습 대비) |
| `generation.py` | 섹션 생성 파이프라인 총괄 — 매핑, Skill 선택, 프롬프트 조립, Claude 호출, JSON 파싱. `SectionResult`, `InlineSuggestion`, `ContentSegment` 데이터클래스 정의 |
| `interview.py` | 인터뷰지 로드(xlsx→JSON), 세션 관리, 후속 질문 로드 |
| `mapping.py` | tag 기반 답변→섹션 매핑 (1차), 필요 시 LLM 2차 매핑 |
| `skills.py` | 4계층 Skill 마크다운 로더 및 섹션별 Skill 선택 |
| `judgment.py` | 🟢🟡🔴 신뢰도 판정 (completeness 기반) |
| `forms.py` | 양식 YAML 로더 |
| `docx_export.py` | 결과물 DOCX 변환 |

### 4계층 Skill 시스템 (`skills/`)

프롬프트에 주입되는 작성 지침으로, 계층별로 합산:
- **L1_universal** — 모든 섹션 공통 원칙 (수치+출처, 범주격차, 과거-미래 쌍둥이 증명)
- **L2_section** — 섹션별 작성법 (문제정의, 솔루션 메커니즘)
- **L3_program** — 지원사업 공통 규칙
- **L4_industry** — 업종 자동 판별

### 프롬프트 (`prompts/`)

- `system.md` — 시스템 프롬프트
- `section_generation.md` — 섹션 생성 프롬프트 템플릿
- `answer_mapping.md` — 답변 매핑 프롬프트

### 데이터 (`data/`)

- `forms/` — 5개 양식 YAML (changjungdae, comeback_package, initial_package, jumping_package, youth_academy)
- `interview/` — 인터뷰 질문지 (xlsx + 파싱 JSON)
- `examples/` — 테스트 답변 세트

## 설계 원칙 (구버전)

- **양식 중립**: 코드 1개, 양식은 YAML 설정으로 분리 — 새 양식 추가 시 YAML만 작성
- **할루시네이션 방지**: 답변에 없는 수치/고유명사 임의 생성 금지 → `[수치 필요]` 플레이스홀더
- **모든 LLM 호출 로깅**: `logs/llm_calls.jsonl`에 자동 축적
- **한국어 전용**: UI, 프롬프트, Skill 모두 한국어

## 테스트 (구버전)

`tests/` 디렉토리 존재하나 현재 테스트 파일 없음. 사이드바의 "이포에이 답변 세트 불러오기" 버튼으로 실합격작 기반 E2E 수동 테스트 가능.

---

# bizplan-mvp CLAUDE.md

## 프로젝트 개요
정부지원사업 사업계획서 AI 자동 작성 서비스 (외부 공개용 SaaS)

## 기술 스택
- 백엔드: FastAPI (Python), 포트 8000
- 프론트엔드: Next.js, 포트 3000
- LLM: Claude Haiku (claude-haiku-4-5-20251001)
- 세션: data/sessions/*.json (파일 기반)
- 백엔드 실행: start_backend.ps1 (단일 인스턴스 보장)
- 배포: Railway — backend/Procfile (`cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT`)

## ⚠️ 현재 개발 중인 플로우 변경 PRD
**반드시 읽을 것**: `docs/PRD_플로우개편_v2.md`
- 서비스 플로우 개편 작업 진행 중. 모든 개발은 이 PRD를 기준으로 진행.
- 기존 플로우(아래)는 구버전이며, PRD의 새 플로우로 교체 예정.

## 서비스 플로우 (구버전 — PRD로 교체 예정)
1단계: 기업정보 입력 → 지원사업 추천
2단계: 인터뷰 10문항 답변
3단계: AI 사업계획서 초안 자동 생성
4단계: 피드백 메모 기반 보완 (양방향 앵커↔메모 스크롤 연동)
5단계: 액션플랜 도출
6단계: DOCX 다운로드

## 서비스 플로우 (신버전 — PRD 기준)
1단계: 기업정보 입력
2단계: 인터뷰 10문항 답변
3단계: 기본 프레임워크 초안 생성 (DRAFT_WRITING_GUIDE 기준, 양식 무관)
4단계: 초안 열람 + 인라인 피드백 확인 및 수정
5단계: 지원사업 추천 + 양식 선택
6단계: 양식 변환 (선택한 양식 기준으로 재배치)
7단계: 최종 결과 열람 + DOCX 다운로드

## 디렉토리 구조

bizplan-mvp/
├── backend/
│   ├── main.py                  # FastAPI 엔트리포인트
│   ├── Procfile                 # Railway 배포용 시작 명령
│   ├── routers/                 # sessions, interview, generation, results, matching, programs, dev
│   └── services/
│       └── session_store.py     # 파일 기반 세션 저장소
├── core/
│   ├── llm.py                   # Claude API 래퍼
│   ├── generation.py            # 섹션 생성 파이프라인
│   ├── context_extraction.py    # 인터뷰 → 구조화 컨텍스트
│   ├── forms.py                 # 양식 YAML 로더
│   ├── interview.py             # 인터뷰 질문 관리
│   ├── mapping.py               # 답변→섹션 매핑
│   ├── skills.py                # 4계층 Skill 로더
│   ├── judgment.py              # 신뢰도 판정·완성도 계산
│   ├── feedback_rag.py          # 피드백 RAG
│   ├── rubric_scorer.py         # 합격 확률 루브릭
│   ├── matching.py              # 지원사업 추천
│   └── docx_export.py           # DOCX 변환 (모든 텍스트 _COLOR_BLACK)
├── frontend/
│   ├── app/
│   │   ├── page.tsx             # 랜딩 (기업정보 입력)
│   │   ├── recommend/           # 지원사업 추천 결과
│   │   ├── interview/[sessionId]/ # 인터뷰 10문항
│   │   ├── generating/[sessionId]/ # 초안 생성 진행 화면
│   │   └── result/[sessionId]/  # 결과 열람·편집·다운로드
│   ├── components/
│   │   ├── result/
│   │   │   ├── DocumentPanel.tsx  # 섹션 본문 (forwardRef, scrollToAnchor)
│   │   │   ├── MemoPanel.tsx      # 메모 패널 (forwardRef, scrollToMemo)
│   │   │   ├── SegmentRenderer.tsx # 세그먼트 렌더링 + 앵커 표시
│   │   │   ├── RubricBadge.tsx    # 서류합격률 배지
│   │   │   └── RubricScorePanel.tsx # 루브릭 점수 상세
│   │   ├── interview/             # ChatMessage, ChatInput
│   │   ├── program/               # ProgramCard (FORMS_AVAILABLE 관리)
│   │   └── generating/            # SectionProgress
│   ├── lib/
│   │   ├── api.ts               # 백엔드 API 호출 함수
│   │   ├── types.ts             # 공통 TypeScript 타입
│   │   └── exampleAnswers.ts    # 인터뷰 질문별 예시 답변 (런맵 기준)
│   └── store/
│       ├── interviewStore.ts    # 인터뷰 답변 전역 상태
│       ├── resultStore.ts       # 결과 전역 상태 (메모 해소율, prob_pct 계산)
│       └── recommendStore.ts    # 추천 결과 전역 상태
├── data/
│   ├── sessions/                # 세션 JSON 파일
│   ├── forms/                   # 양식 YAML (5개)
│   ├── programs/                # 지원사업 공고 CSV
│   └── feedback/                # 블랜 팀장 피드백 데이터셋
├── prompts/                     # 프롬프트 마스터 파일
├── skills/                      # 4계층 스킬 파일
│   ├── L1_universal/
│   ├── L2_section/
│   ├── L3_program/
│   └── L4_industry/
├── tests/                       # e2e 테스트 파일
├── docs/                        # 납품물 아카이브
├── logs/
├── scripts/
└── start_backend.ps1

## 핵심 파일 역할
- prompts/system.md: LLM 응답 구조 규칙
- prompts/section_generation.md: 섹션 생성 프롬프트
- skills/L1_universal/BIZPLAN_FORMAT.md: 서식 규칙 마스터 (제목 계층·기호·표 서식·음슴체)
- skills/L1_universal/U01_numbers_with_sources.md: 수치·출처·완성도 규칙
- skills/L1_universal/U04_bizplan_writing_guide.md: 사업비 집행계획·추진일정 등 항목별 작성 가이드
- skills/L3_program/P_judge_feedback_skill.md: 심사위원 피드백 전략
- core/context_extraction.py: 인터뷰 답변 → 구조화 컨텍스트 변환 (섹션 생성 전 1회 실행)
- core/docx_export.py: DOCX 변환 — _COLOR_GRAY 사용 금지, 모든 텍스트 _COLOR_BLACK
- frontend/components/result/DocumentPanel.tsx: forwardRef + useImperativeHandle로 scrollToAnchor 노출
- frontend/components/result/MemoPanel.tsx: forwardRef + useImperativeHandle로 scrollToMemo 노출
- frontend/components/program/ProgramCard.tsx: FORMS_AVAILABLE 배열 관리 (신규 양식 추가 시 필수 수정)

## 코드 수정 원칙
- 지시한 대상만 수정한다. 범위가 불명확하면 먼저 물어본다.
- "A를 X로 바꿔" = A만 바꾼다. 비슷해 보이는 B, C는 건드리지 않는다.
- 수정 전 변경할 파일 목록을 먼저 보여주고 확인받는다.

## 개발 원칙
1. 새 파일 생성 금지. 기존 파일에 추가/수정만.
2. 규칙 추가 위치:
   - 서식 → skills/L1_universal/BIZPLAN_FORMAT.md
   - 내용/완성도 → skills/L1_universal/U01_numbers_with_sources.md
   - 피드백 → skills/L3_program/P_judge_feedback_skill.md
   - LLM 응답 구조 → prompts/system.md
3. 유사/중복 규칙은 통폐합. 기존 규칙 보완/고도화 우선.
4. 테스트는 별도 지시 시에만 진행.
5. 불확실하면 먼저 확인 후 진행.
6. backend/ 내부 import는 `backend.` 접두사 없이 작성 (`from routers import ...`, `from services import ...`).
   Railway Procfile이 `cd backend && uvicorn main:app` 방식으로 실행하므로, `backend.` 접두사 사용 시 ModuleNotFoundError 발생.
7. DOCX 내보내기(`core/docx_export.py`)에서 텍스트 색상은 반드시 `_COLOR_BLACK` 사용. `_COLOR_GRAY`는 정의하지 않음.
   llm_inferred 세그먼트 포함 모든 텍스트를 검정으로 출력한다.

## max_tokens 설정
- section_generation: 8192 (generation.py — 섹션 최초 생성 및 재생성)
- section_evaluation: 3072 (generation.py — 섹션별 평가)
- strategic_evaluation: 6144 (generation.py — 전략 평가, evaluate_business_plan)
- action_plan: 6000 (results.py — 액션플랜 생성)
- document_check: 1500 (results.py — 문서 교정)
- context_extraction: 8192 (context_extraction.py — 인터뷰 컨텍스트 추출)

## 활성 양식
data/forms/*.yaml에 존재하는 양식: initial_package, initial_package_deeptech, youth_academy,
jumping_package, jumping_package_deeptech, comeback_package, changjungdae, preliminary_package,
deeptech_academy, innovation_voucher

### 초안 변환(convert) 화면 노출 양식 — 고정 3개
초안 생성 후 "지원사업 양식에 맞춰 작성" 화면에서 선택 가능한 양식은 아래 3개로 고정한다.
(나머지 양식 YAML은 유지하되 이 화면에는 노출하지 않음)
- initial_package (초기창업패키지)
- deeptech_academy (딥테크창업사관학교)
- innovation_voucher (혁신바우처 — 선택 시 컨설팅/기술지원/마케팅 서비스 추가 선택)

## 새 양식 추가 시 체크리스트
새 지원사업 양식을 추가할 때 반드시 아래를 함께 업데이트해야 한다.
누락 시 화면 노출과 실제 파일 존재 여부가 불일치함.

1. data/forms/{program_code}.yaml — 양식 YAML 파일 추가
2. data/programs/support_programs.csv — 해당 program_code 행의 has_form=true 설정
3. frontend/components/program/ProgramCard.tsx — PROGRAM_COLORS에 program_code 색상/아이콘 추가
4. (convert 화면에 노출하려면) frontend/app/recommend/page.tsx — CONVERT_TARGET_CODES에 program_code 추가
5. CLAUDE.md "활성 양식" 목록에 추가
