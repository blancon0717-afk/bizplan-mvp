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


# ────────────────────────────────────────────────────────────────────
# PDF 업로드 트랙 — 기존 사업계획서 PDF를 인터뷰 답변으로 사전 채움
#
# 흐름: PDF 바이트 → extract_text_from_pdf → map_pdf_to_answers(질문 매핑)
#       → 인터뷰 답변 사전 저장 → 빈 질문만 보완 인터뷰 → 기존 파이프라인
# 스캔 이미지본 등 텍스트 추출 불가 PDF는 빈 문자열 반환(호출측 no_text 처리).
# ────────────────────────────────────────────────────────────────────


def extract_text_from_pdf(pdf_bytes: bytes, max_chars: int = 60_000) -> str:
    """PDF 바이트에서 텍스트를 추출한다.

    pdfplumber로 페이지별 텍스트를 이어붙인다. 스캔 이미지본 등 추출 가능한
    텍스트가 없으면 빈 문자열("")을 반환한다(호출측에서 no_text로 처리).
    """
    import io

    try:
        import pdfplumber
    except ImportError as e:  # 배포 환경 미설치 시 명확히 로깅
        logger.error("pdfplumber 미설치: %s", e)
        return ""

    parts: list[str] = []
    total = 0
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                try:
                    t = page.extract_text() or ""
                except Exception as e:  # noqa: BLE001 — 페이지 단위 실패는 건너뜀
                    logger.debug("PDF 페이지 텍스트 추출 실패: %s", e)
                    t = ""
                if not t:
                    continue
                parts.append(t)
                total += len(t)
                if total >= max_chars:
                    break
    except Exception as e:  # noqa: BLE001 — 손상 PDF·암호화 PDF 등
        logger.error("PDF 열기/파싱 실패: %s", e)
        return ""
    return "\n".join(parts)[:max_chars].strip()


_PDF_MAP_SYSTEM = (
    "당신은 사업계획서 작성 보조 AI입니다. "
    "사용자가 업로드한 기존 사업계획서(PDF에서 추출한 텍스트)를 읽고, "
    "주어진 인터뷰 질문 각각에 대해 문서에 근거가 있는 경우에만 답변을 채웁니다. "
    "문서에 없는 내용은 절대 지어내지 말고 해당 질문을 생략하세요. 반드시 JSON만 반환합니다."
)


def _build_pdf_map_prompt(pdf_text: str, questions: list[Question]) -> str:
    q_lines = "\n".join(f"- [{q.qid}] {q.text}" for q in questions)
    return f"""아래는 사용자가 업로드한 기존 사업계획서에서 추출한 텍스트입니다.
이 문서 내용을 근거로, 아래 인터뷰 질문 중 문서에서 답을 찾을 수 있는 질문에만 답변을 작성하세요.

## 규칙
- 문서에 근거가 있는 질문만 답변한다. 근거가 없으면 그 질문의 qid를 결과에서 생략한다.
- 답변은 문서 내용을 요약·정리하되, 문서에 없는 사실을 추가·창작하지 않는다.
- 정량 수치·고유명사·출처는 문서에 있는 그대로 보존한다.
- 결과 JSON의 키는 반드시 아래 목록의 qid만 사용한다.
- qid는 대괄호 없이 원문 그대로 사용 (예: "INIT-Q01", "[INIT-Q01]" 아님).
- 다른 텍스트·코드펜스 없이 JSON 객체만 반환한다.

## 인터뷰 질문 목록
{q_lines}

## 업로드된 사업계획서 텍스트
{pdf_text}

## 출력 스키마 (JSON only) — 답변 가능한 qid만 포함
{{ "<qid>": "<문서 근거 답변>", ... }}
"""


def map_pdf_to_answers(pdf_text: str, questions: list[Question]) -> dict[str, str]:
    """업로드된 사업계획서 텍스트를 인터뷰 질문 답변으로 매핑.

    Returns:
        {qid: answer_text} — 문서에서 답을 찾은 질문만 포함(빈 답변·미지원 질문 제외).
        유효한 qid(questions에 존재)만 반환하여 잘못된 키를 차단한다.
    """
    valid_qids = {q.qid for q in questions}
    if not pdf_text.strip() or not questions:
        return {}

    if os.getenv("MOCK_MODE", "0") == "1":
        # MOCK: 앞쪽 절반 질문만 더미 답변 → 보완질문(빈 질문) 흐름 확인용
        half = max(1, len(questions) // 2)
        return {q.qid: f"[MOCK] {q.text} — PDF 기반 더미 답변" for q in questions[:half]}

    prompt = _build_pdf_map_prompt(pdf_text, questions)
    text, meta = call_claude(
        system=_PDF_MAP_SYSTEM,
        user=prompt,
        model="claude-haiku-4-5-20251001",
        max_tokens=8192,
        temperature=0.1,
        purpose="pdf_answer_mapping",
    )
    try:
        data = parse_json_response(text)
        if not isinstance(data, dict):
            raise ValueError(f"expected dict, got {type(data).__name__}")
    except Exception as e:
        logger.error("map_pdf_to_answers 파싱 실패: %s", e)
        return {}

    result: dict[str, str] = {}
    for qid, ans in data.items():
        # LLM이 "[INIT-Q01]"처럼 대괄호를 붙여 반환하는 경우 정규화
        # (form_mapping에서 실측된 것과 동일한 패턴 — 정규화 없으면 매핑 전량 무효)
        qid = str(qid).strip().strip("[]").strip()
        if qid in valid_qids:
            a = str(ans or "").strip()
            if a:
                result[qid] = a
    if not result and data:
        # 정규화 후에도 전량 무효 — 원인 추적용으로 LLM이 쓴 키 형태를 남긴다
        logger.warning("map_pdf_to_answers: 유효 qid 0건 — LLM 반환 키 샘플: %s",
                       list(data.keys())[:5])
    logger.info("map_pdf_to_answers 완료: filled=%d/%d", len(result), len(questions))
    return result
