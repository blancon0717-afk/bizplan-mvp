"""노션 기반 심사위원 피드백 데이터 접근 계층.

scripts/sync_notion_feedback.py가 생성한 data/feedback/notion_feedback.json
(노션 '서류평가 피드백 모음' 표 캐시)만을 읽어, 섹션별로 관련 피드백 사례를
프롬프트 블록으로 반환한다.

- get_feedback_examples(): 초안 생성 프롬프트용 few-shot (기존 feedback_rag와 동일 시그니처)
- get_evaluation_examples(): 심사위원 평가(evaluate_section)용 사례 블록

캐시 파일이 없거나 매칭 결과가 없으면 빈 문자열을 반환한다(에러 없이 통과).
"""

from __future__ import annotations

import json
from pathlib import Path

_CACHE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "reference" / "notion_feedback.json"
)

_records: list[dict] | None = None

# 노션 '태그(사업계획서 섹션)' → 폼 섹션 카테고리/태그 매핑.
# 노션 태그 값과 코드 섹션 태그가 달라서 직접 매핑한다.
_NOTION_TAG_MAP: dict[str, list[str]] = {
    "문제인식": ["Problem", "개발동기", "문제"],
    "아이템 설명": ["Problem", "Solution", "창업아이템"],
    "시장/고객": ["Problem", "Solution", "시장분석", "고객"],
    "데이터부족": ["Problem", "Solution"],
    "차별성/경쟁사분석": ["Solution", "차별성", "경쟁"],
    "진입장벽/보안": ["Solution", "차별성", "기술"],
    "기술부족": ["Solution", "개발준비"],
    "기술보완": ["Solution", "개발준비"],
    "비즈니스모델": ["Scale-up", "BM"],
    "마케팅/영업/수익창출": ["Scale-up", "사업화전략"],
    "원가/재무": ["Scale-up", "일정자금", "재무"],
    "자금조달": ["Scale-up", "일정자금", "재무"],
    "사업비 집행": ["Scale-up", "일정자금", "재무"],
    "고객검증부족": ["Problem", "Scale-up", "고객"],
    "팀구성": ["Team", "팀역량", "대표자"],
    "차별성": ["Solution", "차별성"],
}

# 섹션 ID 선두 숫자 → 카테고리 (evaluate_section은 태그 정보 없이 section_id만 받음)
_SECTION_ID_CATEGORY: dict[str, str] = {
    "1": "Problem",
    "2": "Solution",
    "3": "Scale-up",
    "4": "Team",
}


def _load() -> list[dict]:
    global _records
    if _records is None:
        if not _CACHE_PATH.exists():
            _records = []
        else:
            try:
                data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
                _records = data.get("records", []) if isinstance(data, dict) else []
            except (json.JSONDecodeError, OSError):
                _records = []
    return _records


def reset_cache() -> None:
    """메모리 캐시를 비워 다음 호출 시 JSON을 다시 읽게 한다 (동기화 직후 호출)."""
    global _records
    _records = None


def _category_from_section_id(section_id: str) -> str:
    """'2-2' → 'Solution' 처럼 섹션 ID 선두 숫자로 카테고리 추정."""
    head = section_id.strip()[:1]
    return _SECTION_ID_CATEGORY.get(head, "")


def _record_keywords(record: dict) -> set[str]:
    """레코드의 노션 태그들을 코드 카테고리/태그 키워드 집합으로 확장."""
    kws: set[str] = set()
    for tag in record.get("태그", []):
        kws.update(_NOTION_TAG_MAP.get(tag, []))
    return kws


def _select(keywords: set[str], program_name: str, n: int) -> list[dict]:
    """관련도 점수(사업명 일치 × 2 + 태그 일치 × 1) 순으로 상위 n건 선택."""
    records = _load()
    if not records:
        return []

    prog_prefix = (program_name or "")[:4]

    def _score(r: dict) -> int:
        tag_hit = int(bool(keywords & _record_keywords(r)))
        prog_hit = int(bool(prog_prefix) and prog_prefix in r.get("사업명", ""))
        return prog_hit * 2 + tag_hit

    ranked = sorted(records, key=_score, reverse=True)
    selected = [r for r in ranked if _score(r) > 0][:n]
    return selected


def _format_records(records: list[dict], intro_lines: list[str]) -> str:
    """선택된 레코드를 프롬프트 블록 텍스트로 변환."""
    if not records:
        return ""
    lines = list(intro_lines)
    for i, r in enumerate(records, 1):
        company = str(r.get("기업명", "")).strip()
        program = str(r.get("사업명", "")).strip()
        tags = ", ".join(r.get("태그", []))
        content = str(r.get("피드백내용", "")).strip()
        evaluation = str(r.get("피드백평가", "")).strip()

        lines.append(f"**사례 {i}** | {program} | {company}")
        if tags:
            lines.append(f"태그: {tags}")
        lines.append(f'심사위원 피드백: "{content}"')
        if evaluation:
            lines.append(f"담당자 해설: {evaluation}")
        lines.append("")
    return "\n".join(lines)


def get_feedback_examples(
    program_name: str,
    section_tags: list[str],
    n: int = 2,
    section_category: str = "",
) -> str:
    """초안 생성 프롬프트용 few-shot 블록.

    기존 core.feedback_rag.get_feedback_examples와 동일 시그니처 — 호출부 변경 불필요.
    노션 캐시에서 섹션 관련 실제 피드백을 찾아 '사전 보완' 지침으로 제공.
    """
    keywords = {section_category, *section_tags} - {""}
    selected = _select(keywords, program_name, n)
    return _format_records(
        selected,
        [
            "## 실제 심사위원 피드백 사례 (노션 데이터 · 참고용)\n",
            "아래는 유사 프로그램·유형에서 실제 심사위원이 남긴 피드백입니다.",
            "이 피드백이 지적한 약점을 **사전에 보완**하는 방향으로 섹션을 작성하세요.\n",
        ],
    )


def get_evaluation_examples(
    section_id: str,
    section_title: str = "",
    n: int = 3,
) -> str:
    """심사위원 평가(evaluate_section)용 사례 블록.

    section_id로 카테고리를 추정해, 해당 영역의 실제 심사위원 피드백을 제공.
    평가 LLM은 이 사례들의 관점·지적 방식을 기준으로 섹션을 평가한다.
    """
    category = _category_from_section_id(section_id)
    keywords = {category} - {""}
    selected = _select(keywords, program_name="", n=n)
    return _format_records(
        selected,
        [
            "아래는 이 영역에서 실제 심사위원이 남긴 피드백 사례입니다.",
            "이 사례들이 지적한 관점·기준으로 평가 대상 섹션을 엄격하게 검토하세요.\n",
        ],
    )
