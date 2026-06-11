"""
독립형 Pydantic AI 피드백 에이전트.

역할: 초안 작성 에이전트가 생성한 섹션을 검수하고,
     기준 미충족 시 재작성 지침(retry_instruction)을 반환.
재작성은 이 에이전트의 역할이 아님 — 지침을 원래 초안 에이전트에게 돌려보냄.
"""
import asyncio
import json
import os
from textwrap import dedent

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from .criteria import CRITERIA, SECTION_CATEGORY_MAP
from .header_checker import check_headers
from .models import FeedbackIssue, ReviewRequest, ReviewResult


class _CriteriaCheckOutput(BaseModel):
    """LLM이 반환하는 체크리스트 판단 결과."""
    failed: list[FeedbackIssue] = Field(
        default_factory=list,
        description="미충족 기준 목록. 모두 충족이면 빈 리스트."
    )


def _build_criteria_prompt(request: ReviewRequest, criteria: list[str]) -> str:
    """LLM에 전달할 프롬프트 구성."""
    criteria_lines = "\n".join(f"{i+1}. {c}" for i, c in enumerate(criteria))
    return dedent(f"""
        당신은 사업계획서 검수 전문가입니다.
        아래 초안이 각 판단 기준을 충족하는지 판단하십시오.

        ## 판단 기준 ({request.section_category} 섹션 — {request.section_id})
        {criteria_lines}

        ## 초안 작성에 사용된 인터뷰 원문 (참조용)
        {request.interview_context[:1500] if request.interview_context else "(없음)"}

        ## 검수할 초안
        {request.draft_content}

        ## 지침
        - 각 기준에 대해 초안이 실제로 해당 내용을 포함하고 있는지 판단하십시오.
        - 미충족 기준만 failed 목록에 포함하십시오.
        - reason 필드: 초안에서 해당 내용이 없거나 부족한 부분을 1문장으로 서술.
          초안에 없는 내용을 만들어내거나 추측하지 마십시오.
        - 모든 기준 충족 시 failed는 빈 리스트.
    """).strip()


def _build_retry_instruction(
    section_id: str,
    missing_headers: list[str],
    failed_criteria: list[FeedbackIssue],
) -> str:
    """재작성 지침 텍스트를 템플릿 기반으로 생성 (LLM 불사용)."""
    lines = [f"[섹션 {section_id} 재작성 지침]", ""]

    if missing_headers:
        lines.append("■ 구조적 누락 항목 (반드시 추가할 것):")
        for h in missing_headers:
            lines.append(f"  • {h}")
        lines.append("")

    if failed_criteria:
        lines.append("■ 기준 미충족 항목 (내용 보완 필요):")
        for issue in failed_criteria:
            lines.append(f"  • {issue.criterion}")
            lines.append(f"    → {issue.reason}")
        lines.append("")

    lines.append("위 항목을 모두 반영하여 해당 섹션을 재작성하십시오.")
    lines.append("DRAFT_WRITING_GUIDE의 소제목(▶) 형식과 분량 기준을 준수하십시오.")
    return "\n".join(lines)


_criteria_agent: Agent | None = None


def _get_criteria_agent() -> Agent:
    global _criteria_agent
    if _criteria_agent is None:
        _criteria_agent = Agent(
            "anthropic:claude-haiku-4-5-20251001",
            output_type=_CriteriaCheckOutput,  # pydantic-ai 1.x: result_type → output_type
            system_prompt=(
                "당신은 사업계획서 검수 전문가입니다. "
                "주어진 기준 목록에 대해 초안이 충족하는지 판단하고 "
                "미충족 항목만 반환합니다. "
                "초안에 없는 내용을 생성하거나 추측하지 마십시오."
            ),
        )
    return _criteria_agent


async def review_section_async(request: ReviewRequest) -> ReviewResult:
    """비동기 검수 실행."""
    # 1단계: 결정론적 헤더 검사 (LLM 없음)
    header_result = check_headers(request.section_id, request.draft_content)

    # 카테고리 결정 (요청에 명시된 것 우선, 없으면 섹션 ID 기반)
    category = request.section_category or SECTION_CATEGORY_MAP.get(request.section_id, "")
    criteria = CRITERIA.get(category, [])

    failed_criteria: list[FeedbackIssue] = []

    # 2단계: LLM 체크리스트 판단 (기준 항목이 있는 경우만)
    if criteria:
        prompt = _build_criteria_prompt(request, criteria)
        result = await _get_criteria_agent().run(prompt)
        failed_criteria = result.output.failed  # pydantic-ai 1.x: .data → .output

    passed = header_result.passed and len(failed_criteria) == 0

    retry_instruction = ""
    if not passed:
        retry_instruction = _build_retry_instruction(
            request.section_id,
            header_result.missing,
            failed_criteria,
        )

    return ReviewResult(
        passed=passed,
        missing_headers=header_result.missing,
        failed_criteria=failed_criteria,
        retry_instruction=retry_instruction,
    )


def review_section(request: ReviewRequest) -> ReviewResult:
    """동기 래퍼 — asyncio 이벤트 루프가 없는 환경에서도 호출 가능."""
    return asyncio.run(review_section_async(request))


def review_section_from_dict(data: dict) -> dict:
    """
    JSON 딕셔너리 입력/출력 인터페이스.
    subprocess 또는 HTTP로 호출하는 동료 개발자용.
    """
    request = ReviewRequest(**data)
    result = review_section(request)
    return result.model_dump()


if __name__ == "__main__":
    import sys

    input_data = json.loads(sys.stdin.read())
    output = review_section_from_dict(input_data)
    print(json.dumps(output, ensure_ascii=False, indent=2))
