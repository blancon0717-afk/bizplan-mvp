# bizplan-mvp CLAUDE.md

## 프로젝트 개요
정부지원사업 사업계획서 AI 자동 작성 서비스 (외부 공개용 SaaS)

## 기술 스택
- 백엔드: FastAPI (Python), 포트 8000
- 프론트엔드: Next.js, 포트 3000
- LLM: Claude Haiku (claude-haiku-4-5-20251001)
- 세션: data/sessions/*.json (파일 기반)
- 백엔드 실행: start_backend.ps1 (단일 인스턴스 보장)

## 서비스 플로우
1단계: 기업정보 입력 → 지원사업 추천
2단계: 인터뷰 10문항 답변
3단계: AI 사업계획서 초안 자동 생성
4단계: 피드백 메모 기반 보완
5단계: 액션플랜 도출
6단계: DOCX 다운로드

## 디렉토리 구조

bizplan-mvp/
├── backend/
│   ├── main.py                  # FastAPI 엔트리포인트
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
│   └── docx_export.py           # DOCX 변환
├── frontend/                    # Next.js 앱
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
- prompts/BIZPLAN_FORMAT.md → skills/L1_universal/BIZPLAN_FORMAT.md 참고
- skills/L1_universal/BIZPLAN_FORMAT.md: 서식 규칙 마스터
- skills/L1_universal/U01_numbers_with_sources.md: 수치·출처·완성도 규칙
- skills/L3_program/P_judge_feedback_skill.md: 심사위원 피드백 전략

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

## max_tokens 설정
- section_generation: 8192
- section_evaluation: 3072
- evaluate_business_plan: 6144
- action_plan: 4000
- document_check: 1500
- context_extraction: 8192

## 활성 양식 (FORMS_AVAILABLE)
initial_package, youth_academy, jumping_package, comeback_package, changjungdae

## 새 양식 추가 시 체크리스트
새 지원사업 양식을 추가할 때 반드시 아래 3곳을 동시에 업데이트해야 한다.
누락 시 버튼 활성/비활성 상태와 실제 파일 존재 여부가 불일치함.

1. data/forms/{program_code}.yaml — 양식 YAML 파일 추가
2. frontend/components/program/ProgramCard.tsx — FORMS_AVAILABLE 배열에 program_code 추가
3. CLAUDE.md 하단 "활성 양식 (FORMS_AVAILABLE)" 목록에 추가
