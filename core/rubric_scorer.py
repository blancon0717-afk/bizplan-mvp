"""통계 루브릭 기반 스코어링.

data/reference/rubric_all_clean.json의 26개 피처를 키워드 매칭으로
인터뷰 답변 텍스트에서 감지하여 합격 예측 점수를 반환.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_RUBRIC_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "reference" / "rubric_all_clean.json"
)

_rubric_cache: dict | None = None


def _load_rubric() -> dict:
    global _rubric_cache
    if _rubric_cache is None:
        _rubric_cache = json.loads(_RUBRIC_PATH.read_text(encoding="utf-8"))
    return _rubric_cache

# 피처명 → 감지 키워드 (텍스트 포함 여부로 판단)
_KEYWORDS: dict[str, list[str]] = {
    "누적 투자": ["투자유치", "투자금", "시리즈a", "시리즈b", "프리시드", "시드투자", "벤처투자", "엔젤투자", "억원 투자"],
    "FGI": ["fgi", "포커스그룹", "집단심층면접", "포커스 그룹 인터뷰"],
    "필수 재료 공급처 네트워크 보유": ["공급처", "공급망", "원자재 공급", "협력사", "파트너사", "부품 공급", "소재 공급", "공급업체"],
    "제조 전문 인력 (기술자) 보유(팀원 기준)": ["기술자", "제조전문", "생산전문", "제조 인력", "기술인력", "생산기술"],
    "소재지+주관기관 일치 여부": [],  # 텍스트만으로 판단 불가
    "비즈니스 모델 다양화": ["b2b2c", "다양한 수익", "복수 수익", "수익 다각화", "멀티플 수익모델"],
    "MOU 보유 개수": ["mou", "업무협약", "협약체결", "협약서"],
    "실제 실험 사진 보유 여부": ["실험 사진", "시연 사진", "시험 사진", "poc 사진", "프로토타입 사진"],
    "아이템 관련 경력 증빙": ["경력증명", "경력 증빙", "이전 직장", "전직 경험", "경력확인서"],
    "투자의향서 확보 여부": ["투자의향서", "loi", "의향서"],
    "디자인 네트워크 보유": ["디자인 협력", "디자인 파트너", "ux 파트너", "디자인 네트워크"],
    "R&D 인력 or 자체 학습 데이터 네트워크 보유": ["r&d", "연구개발 인력", "학습 데이터", "ai 데이터"],
    "견적서 보유 개수": ["견적서"],
    "수요처 확보 여부": ["수요처", "구매의향서", "수요기관", "초기 고객 확보"],
    "SW 개발 인력 보유": ["sw 개발", "소프트웨어 개발 인력", "앱 개발자", "개발팀"],
    "자체 생산시설 보유 여부": ["자체 공장", "생산 시설 보유", "제조시설 보유", "공장 보유"],
    "고용(현재)": ["직원 수", "명 고용", "명의 직원", "팀원"],
    "직전 수출": ["수출", "해외 판매 실적", "글로벌 매출", "해외 매출"],
    "영업·마케팅 인력 보유": ["영업 인력", "마케팅 인력", "영업팀", "마케팅팀"],
    "디자인 인력 보유": ["디자이너", "ui/ux 담당", "디자인 담당"],
    "수상 이력": ["수상", "대상 수상", "최우수상", "우수상", "입상"],
}


@dataclass
class RubricScore:
    total: int
    max_possible: int                            # 유의미 + 피처 합산 최대치
    detected_plus: list[str] = field(default_factory=list)   # 합격 신호 감지 목록
    detected_minus: list[str] = field(default_factory=list)  # 감점 신호 감지 목록
    missing_signals: list[str] = field(default_factory=list) # 유의미하나 미감지 신호

    def grade(self) -> str:
        if self.max_possible == 0:
            return "중"
        ratio = self.total / self.max_possible
        if ratio >= 0.6:
            return "상"
        if ratio >= 0.3:
            return "중"
        return "하"


def score_text(text: str) -> RubricScore:
    """인터뷰 답변 텍스트를 루브릭 피처로 스코어링."""
    if not _RUBRIC_PATH.exists():
        return RubricScore(total=0, max_possible=0)

    rubric = _load_rubric()
    features = rubric.get("features", [])
    text_lower = text.lower()

    total = 0
    max_possible = 0
    detected_plus: list[str] = []
    detected_minus: list[str] = []
    missing_signals: list[str] = []

    for feat in features:
        name = feat["feature"]
        direction = feat["direction"]
        weight = feat["weight"]
        tier = feat.get("tier", "잠정")

        keywords = _KEYWORDS.get(name, [])
        if not keywords:
            continue

        detected = any(kw.lower() in text_lower for kw in keywords)

        if direction == "+":
            if tier == "유의미":
                max_possible += weight
            if detected:
                total += weight
                detected_plus.append(f"{name} (+{weight}점)")
            elif tier == "유의미":
                missing_signals.append(name)
        else:
            if detected:
                total -= weight
                detected_minus.append(f"{name} (-{weight}점)")

    return RubricScore(
        total=total,
        max_possible=max_possible,
        detected_plus=detected_plus,
        detected_minus=detected_minus,
        missing_signals=missing_signals,
    )


# ── delivery2 Haiku 채점 엔진 ──────────────────────────────────────────────────
# delivery2/data/rubric_weights_*.json 이 업데이트될수록 자동으로 고도화된다.

import os
import sys as _sys

_DELIVERY2 = Path(__file__).parent.parent / "delivery2"
if str(_DELIVERY2) not in _sys.path:
    _sys.path.insert(0, str(_DELIVERY2))


@dataclass
class PredictedScore:
    prob: float                              # 서류합격 확률 0.0~1.0
    base_rate: float                         # 전체 평균 합격률
    hits: list[dict] = field(default_factory=list)    # 충족 피처
    misses: list[dict] = field(default_factory=list)  # 미충족 피처
    features: dict = field(default_factory=dict)
    mode: str = "7types"

    @property
    def prob_pct(self) -> int:
        return round(self.prob * 100)

    @property
    def significant_misses(self) -> list[dict]:
        return [m for m in self.misses if m.get("tier") == "유의미"]

    @property
    def significant_hits(self) -> list[dict]:
        return [h for h in self.hits if h.get("tier") == "유의미"]


def score_with_haiku(text: str, mode: str = "7types") -> "PredictedScore | None":
    """
    Haiku(delivery2 엔진)로 사업계획서 전문을 채점한다.
    rubric_weights_*.json이 업데이트될수록 정확도가 향상된다.
    API 키 없거나 오류 시 None 반환.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    try:
        import score_plan as _sp
        from claude_structurer import Structurer as _Structurer

        structurer = _Structurer(api_key=api_key)
        features = structurer.structure(text)
        weights = _sp.load_weights(mode)
        score = _sp.compute_score(features, weights, "서류합격")
        if score is None:
            return None
        return PredictedScore(
            prob=score["prob"],
            base_rate=score["base_rate"],
            hits=score.get("hits", []),
            misses=score.get("misses", []),
            features=features,
            mode=mode,
        )
    except Exception:
        return None


def advice_for(feature: str) -> str:
    """미충족 피처에 대한 개선 조언 반환."""
    try:
        import score_plan as _sp
        return _sp.ADVICE.get(feature, "계획서에 관련 내용을 명기하세요.")
    except Exception:
        return "계획서에 관련 내용을 명기하세요."
