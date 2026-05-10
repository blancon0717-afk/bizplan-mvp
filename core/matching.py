"""지원사업 추천 매칭 엔진.

1. CSV 로드 (data/programs/support_programs.csv)
2. 업력/지역 rule-based 필터
3. 아이템·청년 키워드 스코어링
4. MOCK_MODE=0 이면 LLM으로 최종 스코어 보정
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

_CSV_PATH = Path(__file__).resolve().parent.parent / "data/programs/support_programs.csv"

# 업력 → 해당 프로그램 연차 조건 매핑
_ELIGIBLE_STAGES: dict[str, set[str]] = {
    "예비": {"예비(사업자등록X)", "7년 미만", "5년 미만", "제한없음"},
    "초기": {"초기(0~3년 미만)", "7년 미만", "5년 미만", "2년이상", "제한없음(예비 제외)", "제한없음"},
    "도약": {"도약(3~7년 미만)", "7년 미만", "5년 미만", "성장(10년 미만)", "2년이상", "제한없음(예비 제외)", "제한없음"},
    "장기": {"성장(10년 미만)", "7년 이상", "2년이상", "제한없음(예비 제외)", "제한없음"},
}

# 지역 → 해당 프로그램 지역 조건 매핑
_ELIGIBLE_REGIONS: dict[str, set[str]] = {
    "수도권":   {"전국", "수도권"},
    "비수도권": {"전국", "비수도권"},
    "무관":     {"전국", "수도권", "비수도권"},
}

# 특화분야 → 아이템 키워드 매핑 (도메인 일치 보너스 + 불일치 패널티 공용)
_KEYWORD_DOMAIN: dict[str, list[str]] = {
    "R&D":          ["r&d", "연구", "기술개발", "특허", "연구개발"],
    "제조업":        ["제조", "생산", "공장", "부품", "소재", "하드웨어", "설비"],
    "신산업/초격차": ["ai", "인공지능", "딥테크", "빅데이터", "블록체인", "우주", "바이오", "반도체", "첨단"],
    "투자":          ["투자", "벤처", "엑셀러레이터", "팁스"],
    "수출":          ["수출", "글로벌", "해외"],
    "소상공인":      ["소상공인", "자영업", "소규모"],
    "그린/친환경":   ["친환경", "환경", "그린", "재활용", "업사이클", "에코", "탄소", "새활용", "녹색"],
    "농업/식품":     ["농업", "식품", "농식품", "농산물", "음식", "먹거리", "축산"],
    "스포츠":        ["스포츠", "운동", "체육", "헬스", "피트니스"],
    "관광":          ["관광", "여행", "숙박", "호텔", "레저"],
    "문화/예술":     ["문화", "예술", "공연", "전시", "미술", "음악", "전통"],
    "콘텐츠":        ["콘텐츠", "미디어", "영상", "웹툰", "게임", "엔터"],
    "해양/수산":     ["해양", "수산", "바다", "선박", "어업"],
    "SW":            ["sw", "소프트웨어", "앱", "플랫폼", "saas", "클라우드", "솔루션"],
}

# 도메인 불일치 패널티 면제 분야 (어떤 아이템에도 열려 있는 범용 분야)
_GENERAL_DOMAINS = {"일반(공통)", "일반", "★통합공고", "청년(만 39세 이하)"}


_programs_cache: list[SupportProgram] | None = None


@dataclass
class SupportProgram:
    name: str
    연차: list[str]
    특화분야: list[str]
    지역: str
    최대지원금액_만원: int
    지원시기: list[str]
    상태: str
    program_code: str
    설명: str
    has_form: bool = False


def load_programs() -> list[SupportProgram]:
    global _programs_cache
    if _programs_cache is not None:
        return _programs_cache
    if not _CSV_PATH.exists():
        return []
    programs: list[SupportProgram] = []
    with _CSV_PATH.open(encoding="utf-8-sig") as f:
        lines = (line for line in f if not line.startswith("#"))
        for row in csv.DictReader(lines):
            try:
                amount = int(row.get("최대지원금액_만원", "0") or "0")
            except ValueError:
                amount = 0
            programs.append(SupportProgram(
                name=row.get("name", "").strip(),
                연차=[v.strip() for v in row.get("연차", "").split("|") if v.strip()],
                특화분야=[v.strip() for v in row.get("특화분야", "").split("|") if v.strip()],
                지역=row.get("지역", "전국").strip(),
                최대지원금액_만원=amount,
                지원시기=[v.strip() for v in row.get("지원시기", "").split("|") if v.strip()],
                상태=row.get("상태", "").strip(),
                program_code=row.get("program_code", "").strip(),
                설명=row.get("설명", "").strip(),
                has_form=row.get("has_form", "false").strip().lower() == "true",
            ))
    _programs_cache = programs
    return programs


def _is_stage_eligible(업력: str, prog: SupportProgram) -> bool:
    if not prog.연차:
        return True  # 연차 제한 없음 (통합공고 등)
    eligible = _ELIGIBLE_STAGES.get(업력, set())
    return any(stage in eligible for stage in prog.연차)


def _is_region_eligible(지역: str, prog: SupportProgram) -> bool:
    eligible = _ELIGIBLE_REGIONS.get(지역, {"전국"})
    return prog.지역 in eligible


def _is_domain_eligible(아이템: str, prog: SupportProgram) -> bool:
    """특화분야 적합성 하드 필터.

    - 아이템 미입력 → 필터 없음 (모든 사업 표시)
    - 프로그램이 일반(공통)·통합공고만 → 누구나 지원 가능
    - 특화 분야가 있으면 키워드 일치 여부로 판단
    """
    if not 아이템.strip():
        return True
    specific = [d for d in prog.특화분야 if d not in _GENERAL_DOMAINS]
    if not specific:
        return True
    아이템_lower = 아이템.lower()
    return any(
        any(kw in 아이템_lower for kw in _KEYWORD_DOMAIN.get(d, []))
        for d in specific
    )


def _rule_score(profile: dict, prog: SupportProgram) -> tuple[int, list[str]]:
    score = 50
    reasons: list[str] = []

    # 업력 정확 일치 보너스
    업력 = profile.get("업력", "")
    exact_map = {"예비": "예비(사업자등록X)", "초기": "초기(0~3년 미만)", "도약": "도약(3~7년 미만)"}
    if exact_map.get(업력) in prog.연차:
        score += 20
        reasons.append(f"업력({업력}) 정확 일치")

    # 청년 매칭
    is_youth = profile.get("청년", False)
    prog_youth = any("청년" in d for d in prog.특화분야)
    if is_youth and prog_youth:
        score += 15
        reasons.append("청년 우대 프로그램")
    elif not is_youth and "청년창업사관학교" in prog.name:
        score -= 25

    # 아이템 키워드 매칭
    아이템_lower = profile.get("아이템", "").lower()
    for domain, keywords in _KEYWORD_DOMAIN.items():
        if any(kw in 아이템_lower for kw in keywords) and domain in prog.특화분야:
            score += 15
            reasons.append(f"아이템 분야 일치({domain})")
            break

    # 상태 보정
    if prog.상태 == "종료":
        score -= 20
    elif prog.상태 == "통합공고":
        score = 30  # 통합공고는 고정 낮은 점수

    return max(0, min(100, score)), reasons



def recommend(profile: dict) -> list[dict]:
    """기업 프로필 기반 지원사업 추천.

    profile keys:
      업력  — 예비 | 초기 | 도약 | 장기
      아이템 — 자유 텍스트
      청년  — bool
      지역  — 수도권 | 비수도권 | 무관
    """
    programs = load_programs()
    results: list[dict] = []

    for prog in programs:
        if prog.상태 == "종료":
            continue
        stage_ok = _is_stage_eligible(profile.get("업력", "초기"), prog)
        region_ok = _is_region_eligible(profile.get("지역", "무관"), prog)
        domain_ok = _is_domain_eligible(profile.get("아이템", ""), prog)
        is_eligible = stage_ok and region_ok and domain_ok

        score, reasons = _rule_score(profile, prog)
        results.append({
            "name": prog.name,
            "연차": prog.연차,
            "특화분야": prog.특화분야,
            "지역": prog.지역,
            "최대지원금액_만원": prog.최대지원금액_만원,
            "지원시기": prog.지원시기,
            "상태": prog.상태,
            "program_code": prog.program_code,
            "설명": prog.설명,
            "has_form": prog.has_form,
            "score": score,
            "match_reasons": reasons,
            "is_eligible": is_eligible,
        })

    results.sort(key=lambda x: (
        not x["is_eligible"],
        x["상태"] == "통합공고",
        x["상태"] == "종료",
        -x["score"],
    ))
    return results
