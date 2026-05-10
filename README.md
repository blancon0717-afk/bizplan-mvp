# 사업계획서 AI MVP

정부지원사업 사업계획서를 **1회 인터뷰 → 여러 양식**으로 자동 생성하는 서비스.

## 구조

```
bizplan-mvp/
├── app.py                    # Streamlit UI
├── core/                     # 재사용 가능한 핵심 로직
│   ├── interview.py          # 인터뷰지 로드·세션 관리
│   ├── forms.py              # 양식 YAML 로더
│   ├── skills.py             # Skill 로더
│   ├── mapping.py            # 답변 → 섹션 매핑
│   ├── generation.py         # LLM 호출 파이프라인
│   ├── judgment.py           # 🟢🟡🔴 판정
│   ├── docx_export.py        # 결과물 DOCX 변환
│   └── llm.py                # Claude API 래퍼 + 로그
├── skills/                   # Skill 지식 (4계층)
│   ├── L1_universal/         # 모든 섹션 공통 원칙 (3개)
│   ├── L2_section/           # 섹션별 작성법 (2개)
│   ├── L3_program/           # 지원사업 공통 (1개)
│   └── L4_industry/          # 업종 자동 판별 (1개)
├── prompts/                  # 프롬프트 템플릿
│   ├── system.md
│   ├── section_generation.md
│   └── answer_mapping.md
├── data/
│   ├── interview/            # 인터뷰지 (xlsx + 파싱 JSON)
│   ├── forms/                # 양식별 YAML 설정 (5개)
│   ├── examples/             # 테스트 답변 세트 (이포에이 등)
│   └── sessions/             # 사용자 세션 저장 (gitignore)
└── logs/
    └── llm_calls.jsonl       # 모든 LLM 호출 로그 (Phase 3 학습용)
```

## 설치 & 실행

### 1. 환경 구축
```bash
cd bizplan-mvp
python -m venv .venv
.venv/Scripts/activate     # Windows
# source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
```

### 2. 환경변수 설정
```bash
cp .env.example .env
# .env 파일을 열어 ANTHROPIC_API_KEY 입력
```

### 3. Streamlit 앱 실행
```bash
streamlit run app.py
```
브라우저에서 `http://localhost:8501` 열림.

## 사용 흐름

1. **지원사업 선택** — 5개 양식 중 하나
2. **인터뷰** — 60개 질문에 답변 (중간 건너뛰기 가능)
3. **생성** — 모든 섹션 자동 작성 + 🟢🟡🔴 판정
4. **확인/보완** — 🔴🟡 섹션 추가 정보 보완
5. **DOCX 다운로드**

테스트: 사이드바의 "이포에이 답변 세트 불러오기" 버튼으로 실제 합격작 기반 답변 즉시 로드.

## 주요 설계 원칙

- **양식 중립**: 코드는 1개, 양식은 YAML 설정 (5개 양식 Day 1 지원)
- **업종 자동 판별**: LLM이 답변 읽고 업종 감지, 하드코딩 없음
- **할루시네이션 방지**: 답변에 없는 수치·고유명사 임의 생성 금지 → `[수치 필요]` 표시
- **모든 LLM 호출 로깅**: Phase 3 지도학습 데이터 자동 축적

## 다음 단계 (Phase 2)

- 인터뷰지 v2 (양식 중립 태그 체계)
- 벡터 DB + 합격작 1,000건 RAG
- 사용자 행동 로그 스키마
- 섹션별 추가 질문 자동 생성 (보완 루프 고도화)
- HWP 출력
