from pydantic import BaseModel, Field


class FeedbackIssue(BaseModel):
    criterion: str = Field(description="판단 기준 항목 (원문 그대로)")
    reason: str = Field(description="미충족 이유 — 초안에서 해당 내용이 없거나 부족한 부분을 1문장으로")


class ReviewRequest(BaseModel):
    draft_content: str = Field(description="검수할 섹션 초안 텍스트")
    section_id: str = Field(description="섹션 ID: '1-1' ~ '4-1'")
    section_category: str = Field(description="카테고리: 문제인식 / 솔루션 / 스케일업 / 팀구성")
    interview_context: str = Field(description="초안 작성에 사용된 인터뷰 원문 (참조용)")


class ReviewResult(BaseModel):
    passed: bool = Field(description="모든 헤더·기준 충족 시 True")
    missing_headers: list[str] = Field(
        default_factory=list,
        description="누락된 구조적 헤더/요소 목록 (결정론적 검사)"
    )
    failed_criteria: list[FeedbackIssue] = Field(
        default_factory=list,
        description="미충족 체크리스트 기준 목록 (LLM 판단)"
    )
    retry_instruction: str = Field(
        default="",
        description="초안 작성 에이전트에게 전달할 재작성 지침 (실패 시에만 생성)"
    )
