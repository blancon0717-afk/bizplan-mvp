"""
DRAFT_WRITING_GUIDE 기준 구조적 요소 결정론적 검사.

LLM 없이 문자열 매칭만으로 수행 — 속도 보장, 할루시네이션 0.
"""
from dataclasses import dataclass, field


@dataclass
class HeaderCheckResult:
    missing: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.missing) == 0


# 섹션별 필수 구조 요소 정의
# 각 항목: (사람이 읽을 수 있는 레이블, 검사 함수)
_REQUIREMENTS: dict[str, list[tuple[str, object]]] = {
    "1-1": [
        ("■ 소제목 최소 1개", lambda t: t.count("■") >= 1),
    ],
    "1-2": [
        ("■ 소제목 최소 1개", lambda t: t.count("■") >= 1),
    ],
    "1-3": [
        ("■ 소제목 최소 1개", lambda t: t.count("■") >= 1),
    ],
    "2-1": [
        ("■ 소제목 최소 1개", lambda t: t.count("■") >= 1),
        ("TAM·SAM·SOM 항목 명시", lambda t: "TAM" in t and "SAM" in t and "SOM" in t),
    ],
    "2-2": [
        ("■ 소제목 최소 2개 (아이템 정의 + 핵심 기술)", lambda t: t.count("■") >= 2),
        ("사용 프로세스 단계 표기 (① 또는 1단계 형식)", lambda t: "①" in t or "1)" in t or "1단계" in t),
        ("경쟁사 비교표 포함 여부", lambda t: ("경쟁사" in t or "비교" in t) and "|" in t),
    ],
    "2-3": [
        ("■ 소제목 최소 1개", lambda t: t.count("■") >= 1),
    ],
    "3-1": [
        ("■ 소제목 최소 3개 (BM + 마케팅 + 로드맵)", lambda t: t.count("■") >= 3),
        ("BEP 또는 손익분기점 산출 포함", lambda t: "BEP" in t or "손익분기" in t or "월 고정비" in t),
        ("협약기간 내 KPI 목표 표 (표 A)", lambda t: ("KPI" in t or ("협약" in t and "목표" in t)) and "|" in t),
        ("월별 사업추진 일정 표 (표 B, 최소 5행)", lambda t: "협약" in t and "|" in t and t.count("|") >= 15),
    ],
    "3-2": [
        ("사업비 집행 계획 표 (비목 컬럼 포함)", lambda t: "비목" in t or "정부지원사업비" in t),
        ("자금 조달 계획 표", lambda t: "조달" in t and "|" in t),
    ],
    "4-1": [
        ("■ 소제목 최소 2개 (대표자 + 기업 현황)", lambda t: t.count("■") >= 2),
        ("4대보험 가입 여부 명시", lambda t: "4대보험" in t),
        ("조직 구성 표 (담당직무·경력·4대보험)", lambda t: ("담당" in t or "직무" in t) and "|" in t),
    ],
}


def check_headers(section_id: str, content: str) -> HeaderCheckResult:
    """주어진 섹션 ID 기준으로 초안의 구조적 요소를 검사."""
    requirements = _REQUIREMENTS.get(section_id, [])
    missing: list[str] = []
    for label, check_fn in requirements:
        try:
            if not check_fn(content):
                missing.append(label)
        except Exception:
            missing.append(label)
    return HeaderCheckResult(missing=missing)
