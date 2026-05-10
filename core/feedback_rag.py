"""심사위원 피드백 few-shot RAG.

data/reference/feedback_pairs.json에서 프로그램명·섹션 태그 기준으로
관련 피드백 예시를 검색하여 생성 프롬프트에 주입할 블록을 반환.
"""

from __future__ import annotations

import json
from pathlib import Path

_FEEDBACK_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "reference" / "feedback_pairs.json"
)

_pairs: list[dict] | None = None

# 심사위원 피드백 태그 → 섹션 카테고리/태그 매핑
# feedback_pairs.json 태그와 폼 섹션 태그가 달라서 직접 매핑
_FEEDBACK_TAG_MAP: dict[str, list[str]] = {
    "문제인식":             ["Problem", "개발동기", "문제"],
    "아이템 설명":          ["Problem", "Solution", "창업아이템"],
    "시장/고객":            ["Problem", "시장분석", "고객"],
    "차별성/경쟁사분석":    ["Solution", "차별성", "경쟁"],
    "진입장벽/보안":        ["Solution", "차별성", "기술"],
    "기술부족":             ["Solution", "개발준비"],
    "기술보완":             ["Solution", "개발준비"],
    "비즈니스모델":         ["Scale-up", "BM"],
    "마케팅/영업/수익창출": ["Scale-up", "사업화전략"],
    "자금조달":             ["Scale-up", "일정자금", "재무"],
    "사업비 집행":          ["Scale-up", "일정자금", "재무"],
    "고객검증부족":         ["Problem", "Scale-up", "고객"],
    "데이터부족":           ["Problem", "Solution"],
    "데이터활용도":         ["Solution"],
    "팀구성":               ["Team", "팀역량", "대표자"],
    "차별성":               ["Solution", "차별성"],
}


def _load() -> list[dict]:
    global _pairs
    if _pairs is None:
        if not _FEEDBACK_PATH.exists():
            _pairs = []
        else:
            data = json.loads(_FEEDBACK_PATH.read_text(encoding="utf-8"))
            _pairs = data.get("pairs", [])
    return _pairs


def get_feedback_examples(
    program_name: str,
    section_tags: list[str],
    n: int = 1,
    section_category: str = "",
) -> str:
    """관련 피드백 예시 n건을 프롬프트 블록으로 반환.

    우선순위: (프로그램 일치 × 2) + (섹션 관련도 × 1) 점수 순.
    섹션 카테고리·태그를 _FEEDBACK_TAG_MAP으로 변환해 매칭.
    완전히 무관한 피드백만 남으면 빈 문자열 반환.
    """
    pairs = _load()
    if not pairs:
        return ""

    keywords = {section_category, *section_tags}
    prog_prefix = program_name[:4]

    def _tag_match(p: dict) -> bool:
        for ft in p.get("태그", []):
            if any(term in keywords for term in _FEEDBACK_TAG_MAP.get(ft, [])):
                return True
        return False

    def _score(p: dict) -> int:
        return int(prog_prefix in p.get("사업명", "")) * 2 + int(_tag_match(p))

    selected = sorted(pairs, key=_score, reverse=True)[:n]

    if _score(selected[0]) == 0:
        return ""

    lines = [
        "## 실제 심사위원 피드백 예시 (참고용)\n",
        "아래는 유사한 프로그램·유형에서 실제 심사위원이 남긴 피드백입니다.",
        "이 피드백이 지적한 약점을 **사전에 보완**하는 방향으로 섹션을 작성하세요.\n",
    ]
    for i, p in enumerate(selected, 1):
        lines.append(f"**예시 {i}** | {p.get('사업명', '')} | {p.get('아이템명', '')}")
        lines.append(f"태그: {', '.join(p.get('태그', []))}")
        lines.append(f'심사위원 피드백: "{p.get("피드백", "")}"')
        lines.append("")

    return "\n".join(lines)
