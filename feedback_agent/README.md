# feedback_agent — 사업계획서 초안 검수 에이전트

초안 작성 에이전트가 생성한 섹션을 검수하고, 기준 미충족 시 재작성 지침을 반환하는 독립형 Pydantic AI 모듈.

## 역할

- **Gate/Checker only** — 내용을 직접 수정하거나 재작성하지 않음
- 기준 미충족 시 `retry_instruction`을 생성 → 기존 초안 작성 에이전트에게 돌려보냄
- 두 가지 검사 수행:
  1. **결정론적 헤더 검사** (`header_checker.py`) — ■ 소제목 수, TAM/SAM/SOM, 표 구조 등
  2. **LLM 체크리스트 검사** (`agent.py`) — 카테고리별 콘텐츠 기준 yes/no 판단

## 설치

```bash
pip install -r feedback_agent/requirements.txt
```

환경변수 설정:
```
ANTHROPIC_API_KEY=your_key_here
```

## bizplan 통합 포인트 (1줄)

`core/generation.py`의 `generate_framework_section()` 반환 직전에 추가:

```python
from feedback_agent import ReviewRequest, review_section

review = review_section(ReviewRequest(
    draft_content=result.content,
    section_id=section.section_id,      # "1-1" ~ "4-1"
    section_category=section.category,  # "문제인식" / "솔루션" / "스케일업" / "팀구성"
    interview_context=company_context,  # 인터뷰 원문
))

if not review.passed:
    # retry_instruction을 기존 draft 생성 함수에 다시 전달
    result = await generate_framework_section(
        section, company_context, answers,
        extra_instruction=review.retry_instruction
    )
```

## 입출력 스키마

**입력 (`ReviewRequest`)**

| 필드 | 타입 | 설명 |
|---|---|---|
| `draft_content` | str | 검수할 섹션 초안 |
| `section_id` | str | `"1-1"` ~ `"4-1"` |
| `section_category` | str | `문제인식` / `솔루션` / `스케일업` / `팀구성` |
| `interview_context` | str | 인터뷰 원문 (참조용) |

**출력 (`ReviewResult`)**

| 필드 | 타입 | 설명 |
|---|---|---|
| `passed` | bool | 모든 기준 충족 시 True |
| `missing_headers` | list[str] | 구조적 누락 요소 목록 |
| `failed_criteria` | list[FeedbackIssue] | 미충족 체크리스트 항목 |
| `retry_instruction` | str | 재작성 에이전트에게 전달할 지침 |

## subprocess / HTTP 호출 방식

```python
import subprocess, json

result = subprocess.run(
    ["python", "-m", "feedback_agent.agent"],
    input=json.dumps({
        "draft_content": "...",
        "section_id": "1-1",
        "section_category": "문제인식",
        "interview_context": "...",
    }, ensure_ascii=False),
    capture_output=True, text=True, encoding="utf-8"
)
output = json.loads(result.stdout)
```

## 섹션 ID → 카테고리 매핑

| 섹션 | 카테고리 |
|---|---|
| 1-1, 1-2, 1-3 | 문제인식 |
| 2-1, 2-2, 2-3 | 솔루션 |
| 3-1, 3-2 | 스케일업 |
| 4-1 | 팀구성 |

## 할루시네이션 방지 설계

- LLM은 pre-built 체크리스트(yes/no 판단)만 수행 — 피드백 예시 원본 미주입
- `reason` 필드는 초안에 실제로 있는 내용만 참조하도록 프롬프트 제한
- Pydantic 스키마 강제로 자유형 출력 차단
- `retry_instruction`은 템플릿 기반 생성 (LLM 미사용)
