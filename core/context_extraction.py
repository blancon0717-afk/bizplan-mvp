"""인터뷰 답변 전처리 모듈.

인터뷰 10문항 답변을 Haiku 1회 호출로 8개 정제 항목(기업 컨텍스트)으로 압축한 뒤,
섹션 생성 시 섹션-항목 매핑 테이블 기준으로 필요한 항목만 프롬프트에 투입한다.

목적:
  - 인터뷰 원문 전체(약 17K 토큰) → 정제 컨텍스트(약 5K 토큰) 투입으로 비용·속도 개선
  - 섹션 카테고리·태그에 무관한 항목은 프롬프트에서 제외
"""

from __future__ import annotations

import logging
import os
import time

from core.interview import Answer, Question
from core.llm import call_claude, parse_json_response

logger = logging.getLogger(__name__)


CONTEXT_FIELDS: list[str] = [
    "문제인식",
    "솔루션",
    "시장규모",
    "비즈니스모델",
    "팀구성",
    "매출계획",
    "경쟁우위",
    "고객타겟",
]


# 섹션 카테고리 → 기본 컨텍스트 항목.
CATEGORY_DEFAULT_FIELDS: dict[str, list[str]] = {
    "Problem":  ["문제인식", "고객타겟", "시장규모"],
    "Solution": ["솔루션", "경쟁우위", "고객타겟"],
    "Scale-up": ["비즈니스모델", "매출계획", "시장규모"],
    "Team":     ["팀구성"],
    "Closure":  ["팀구성", "문제인식"],
    "Overview": ["비즈니스모델", "솔루션", "시장규모"],
}


# 섹션 태그 → 추가 컨텍스트 항목 (카테고리 디폴트에 합집합으로 추가).
TAG_FIELDS: dict[str, list[str]] = {
    "개발동기":   ["문제인식"],
    "시장분석":   ["시장규모"],
    "고객":       ["고객타겟"],
    "BM":         ["비즈니스모델"],
    "차별성":     ["경쟁우위", "솔루션"],
    "경쟁사":     ["경쟁우위"],
    "개발준비":   ["솔루션"],
    "사업화전략": ["매출계획", "비즈니스모델"],
    "일정자금":   ["매출계획"],
    "재무":       ["매출계획"],
    "팀역량":     ["팀구성"],
    "대표자":     ["팀구성"],
    "투자":       ["매출계획"],
    "IP":         ["솔루션"],
    "해외":       ["매출계획"],
    "폐업이력":   ["문제인식", "팀구성"],
    "ESG":        [],
    "지역":       [],
}


def get_section_context_fields(category: str, tags: list[str]) -> list[str]:
    """섹션 카테고리·태그를 기반으로 투입할 컨텍스트 항목 결정."""
    fields = list(CATEGORY_DEFAULT_FIELDS.get(category, []))
    for t in tags or []:
        for f in TAG_FIELDS.get(t, []):
            if f not in fields:
                fields.append(f)
    if not fields:
        # 매핑 미스(특수 카테고리) → 안전하게 전 항목 투입
        return list(CONTEXT_FIELDS)
    return fields


def format_context_block(context: dict, fields: list[str]) -> str:
    """선택된 항목만 프롬프트용 텍스트 블록으로 포맷.

    빈 필드는 생략하지 않고 '정보 없음 - AI 추론 필요'로 표시하여
    LLM이 해당 항목을 추론 대상으로 인식하게 한다.
    """
    blocks: list[str] = []
    for f in fields:
        v = (context.get(f) or "").strip()
        blocks.append(f"### {f}\n{v if v else '(정보 없음 - AI 추론 필요)'}")
    return "\n\n".join(blocks) if blocks else "(전체 항목 정보 없음 - AI 추론 필요)"


def _build_qa_block(questions: list[Question], answers: dict[str, Answer]) -> str:
    q_map = {q.qid: q for q in questions}
    lines: list[str] = []
    for qid, ans in answers.items():
        q = q_map.get(qid)
        text = (ans.text or "").strip()
        if not text:
            continue
        q_text = q.text if q else "(질문 텍스트 없음)"
        lines.append(f"[{qid}] Q: {q_text}\nA: {text}")
    return "\n\n".join(lines)


_EXTRACT_SYSTEM = (
    "당신은 사업계획서 작성 보조 AI입니다. "
    "사용자의 인터뷰 원문에서 사업계획 작성에 필요한 핵심 정보만 정제·요약하세요. "
    "원문에 없는 사실을 추가·창작하지 마세요. 반드시 JSON만 반환합니다."
)


def _build_extract_prompt(qa_block: str) -> str:
    field_list = ", ".join(CONTEXT_FIELDS)
    return f"""아래는 사업 대표자의 인터뷰 답변 원문입니다.
이 정보를 다음 8개 항목으로 정제하여 JSON으로 반환하세요.

## 항목 정의
- 문제인식: 어떤 고객의 어떤 문제를 해결하려 하는지, 기존 해결방식의 한계 포함
- 솔루션: 핵심 기능·제품 형태·작동 메커니즘·기술적 차별성
- 시장규모: TAM/SAM/SOM, 시장 성장성, 측정 기준·출처 (정량 수치 그대로 보존)
- 비즈니스모델: 누구에게·얼마에·어떤 채널·어떤 구조로 수익을 내는지
- 팀구성: 대표자 경력·전문성, 팀원 역할·인원, 핵심 파트너십
- 매출계획: 연도별 매출 목표·산출 근거, 자금 조달·집행 계획, 확보된 수요처(MOU·LOI·구매의향서), 기존 매출 실적.
- 경쟁우위: 경쟁사 실명, 자사 차별점, 진입장벽(IP·노하우 등)
- 고객타겟: 1차 타깃 정의(연령·직군·사용 맥락·고통 지점)만 포함. 매출 실적·수요처 확보·MOU는 포함하지 말 것 (매출계획 항목에 포함).

## 작성 규칙
- 각 항목 2~5문장으로 요약. 원문의 정량 수치·출처·고유명사는 반드시 보존
- 원문에 없는 정보는 추가·추론 금지. 정보가 부족한 항목은 빈 문자열("")로 둠
- 항목 키는 정확히 위 8개 한국어 단어 사용
- 다른 텍스트·코드펜스 없이 JSON 객체만 반환

## 인터뷰 원문
{qa_block}

## 출력 스키마 (JSON only)
{{
  {", ".join(f'"{f}": "..."' for f in CONTEXT_FIELDS)}
}}
"""


def _mock_context() -> dict:
    return {f: f"[MOCK] {f} 더미 컨텍스트 — 실제 API 모드에서 정제됩니다." for f in CONTEXT_FIELDS}


def extract_company_context(
    questions: list[Question],
    answers: dict[str, Answer],
) -> dict:
    """인터뷰 답변을 8개 정제 항목으로 변환.

    Returns:
        {field_name: distilled_text} — 모든 CONTEXT_FIELDS 키 보장(누락은 빈 문자열).
    """
    qa_block = _build_qa_block(questions, answers)
    if not qa_block.strip():
        empty = {f: "" for f in CONTEXT_FIELDS}
        empty["_meta"] = {"reason": "no_answers", "input_tokens": 0, "duration_ms": 0}
        return empty

    if os.getenv("MOCK_MODE", "0") == "1":
        ctx = _mock_context()
        ctx["_meta"] = {"model": "mock", "input_tokens": 0, "output_tokens": 0, "duration_ms": 0}
        logger.info("extract_company_context [MOCK] 8개 항목 더미 반환")
        return ctx

    prompt = _build_extract_prompt(qa_block)
    t0 = time.perf_counter()
    text, meta = call_claude(
        system=_EXTRACT_SYSTEM,
        user=prompt,
        model="claude-haiku-4-5-20251001",
        max_tokens=8192,
        temperature=0.1,
        purpose="context_extraction",
    )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    try:
        data = parse_json_response(text)
        if not isinstance(data, dict):
            raise ValueError(f"expected dict, got {type(data).__name__}")
    except Exception as e:
        logger.error(f"extract_company_context 파싱 실패: {e}")
        data = {}

    result: dict = {f: str(data.get(f, "") or "").strip() for f in CONTEXT_FIELDS}
    result["_meta"] = {
        "model": meta.get("model"),
        "input_tokens": meta.get("input_tokens"),
        "output_tokens": meta.get("output_tokens"),
        "duration_ms": elapsed_ms,
    }
    filled = sum(1 for f in CONTEXT_FIELDS if result[f])
    logger.info(
        "extract_company_context 완료: filled=%d/%d in=%s out=%s dur=%dms",
        filled, len(CONTEXT_FIELDS),
        meta.get("input_tokens"), meta.get("output_tokens"), elapsed_ms,
    )
    return result
