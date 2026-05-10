"""답변 → 섹션 매핑 (태그 기반 룰 매칭)."""

from __future__ import annotations

from core.forms import FormSection
from core.interview import Answer, Question


def map_by_tags(
    section: FormSection,
    questions: list[Question],
    answers: dict[str, Answer],
) -> tuple[list[str], list[str]]:
    """태그 기반 1차 매핑.

    Returns:
        (primary_qids, supporting_qids)
    """
    primary: list[str] = []
    supporting: list[str] = []

    for q in questions:
        if q.qid not in answers or not answers[q.qid].text.strip():
            continue

        # 태그 교집합 계산
        section_tag_set = set(section.tags)
        question_tag_set = set(q.tags)
        overlap = section_tag_set & question_tag_set

        if not overlap:
            continue

        # 카테고리도 일치하면 primary, 아니면 supporting
        if q.category.lower() in section.category.lower() or section.category.lower() in q.category.lower():
            primary.append(q.qid)
        else:
            supporting.append(q.qid)

    # 팀/대표자 정보는 거의 모든 섹션의 supporting으로 유용
    if section.category != "Team":
        for q in questions:
            if "대표자" in q.tags and q.qid in answers and q.qid not in primary and q.qid not in supporting:
                supporting.append(q.qid)

    return primary, supporting


def get_answer_context(
    qids: list[str],
    questions: list[Question],
    answers: dict[str, Answer],
) -> str:
    """선별된 qid들에 대한 Q/A 블록을 프롬프트용 텍스트로 반환."""
    q_map = {q.qid: q for q in questions}
    blocks: list[str] = []
    for qid in qids:
        q = q_map.get(qid)
        if not q or qid not in answers:
            continue
        a = answers[qid].text.strip()
        if not a:
            continue
        blocks.append(f"### [{qid}] {q.section}\nQ: {q.text}\nA: {a}")
    return "\n\n".join(blocks) if blocks else "(관련 답변 없음)"
