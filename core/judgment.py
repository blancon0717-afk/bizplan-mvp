"""판정 후처리.

LLM이 반환한 confidence_level을 그대로 신뢰하되,
Rubric 결과·missing_info·답변 사용 여부 등으로 **보정**한다.
(LLM이 과대평가하는 경향 방지)
"""

from __future__ import annotations

from core.generation import SectionResult


CONFIDENCE_COLORS = {
    "green": "🟢",
    "yellow": "🟡",
    "red": "🔴",
}


def apply_post_judgment(result: SectionResult) -> SectionResult:
    """LLM 자가 판정을 Rubric·missing·통계루브릭으로 조정."""
    level = (result.confidence_level or "red").lower()

    # LLM self-rubric 통과 개수
    rubric_pass = sum(
        1 for v in result.rubric_check.values() if v is True
    ) if result.rubric_check else 0

    # 통계 루브릭 점수 (generation.py가 llm_meta["rubric_score"]에 저장)
    stat_rubric: dict = result.llm_meta.get("rubric_score", {})
    stat_total: int = stat_rubric.get("total", 0)
    stat_max: int = stat_rubric.get("max_possible", 0)

    # missing_info 3개 이상이면 yellow 이하로 강등
    if len(result.missing_info) >= 3 and level == "green":
        level = "yellow"

    # used_answer_ids 0개면 red 강제
    if not result.used_answer_ids:
        level = "red"

    # LLM self-rubric 1개 이하 통과면 red
    if result.rubric_check and rubric_pass <= 1:
        level = "red"

    # LLM self-rubric 3개 이상 통과 + missing 1개 이하 + 답변 2개 이상이면 green 승격
    if (
        result.rubric_check
        and rubric_pass >= 3
        and len(result.missing_info) <= 1
        and len(result.used_answer_ids) >= 2
        and level == "yellow"
    ):
        level = "green"

    # 통계 루브릭 보정 (stat_max > 0인 경우만)
    if stat_max > 0:
        ratio = stat_total / stat_max
        # 합격 신호 강함(60%+): yellow → green 승격
        if ratio >= 0.6 and level == "yellow" and len(result.missing_info) <= 2:
            level = "green"
        # 합격 신호 음수 총점: green → yellow 강등
        if stat_total < 0 and level == "green":
            level = "yellow"

    result.confidence_level = level

    # completion_score 정합성 보정 — LLM이 과대평가하는 경향 방지
    score = max(0, min(100, int(result.completion_score or 0)))

    # confidence level과의 최소 일관성 보장 (상한 캡)
    caps = {"green": 100, "yellow": 79, "red": 59}
    score = min(score, caps.get(level, 100))

    # used_answer_ids=0이면 상한 40 (답변 없이 작성된 것은 완성도 낮음)
    if not result.used_answer_ids:
        score = min(score, 40)

    # inline_suggestions가 많으면 상한 하향
    n_sug = len(result.inline_suggestions)
    if n_sug >= 8:
        score = min(score, 45)
    elif n_sug >= 5:
        score = min(score, 65)

    # 하한 보정 — llm이 0으로만 반환하는 경우 대비 (최소 10)
    if score == 0 and result.used_answer_ids:
        score = 25

    result.completion_score = score
    return result


def calculate_overall_completion(results: list[SectionResult]) -> int:
    """전체 사업계획서 완성도 — 섹션별 effective_completion_score의 단순 평균."""
    if not results:
        return 0
    total = sum(r.effective_completion_score() for r in results)
    return round(total / len(results))


def get_color_emoji(level: str) -> str:
    return CONFIDENCE_COLORS.get(level.lower(), "⚪")


def get_color_label(level: str) -> str:
    mapping = {
        "green": "🟢 근거 충분",
        "yellow": "🟡 일부 추론 (검토 권장)",
        "red": "🔴 정보 부족 (보완 필요)",
    }
    return mapping.get(level.lower(), "⚪ 미판정")
