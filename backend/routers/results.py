"""결과 조회, 메모 답변, 섹션 재생성, DOCX 다운로드."""
from __future__ import annotations

import csv
import json
import logging
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.session_store import get_session, get_usage_count, increment_usage, load_results, save_results
from core.docx_export import export_to_docx
from core.forms import load_form
from core.generation import regenerate_section
from core.interview import load_initial_questions, load_followup_questions
from core.judgment import apply_post_judgment, calculate_overall_completion
from core.skills import load_skills

router = APIRouter(tags=["results"])
logger = logging.getLogger(__name__)

_INITIAL_Q_PATH = Path("data/interview/initial_questions.json")
_FOLLOWUP_Q_PATH = Path("data/interview/questions.json")
_SKILLS_DIR = Path("skills")
_PROGRAMS_CSV = Path("data/programs/support_programs.csv")


def _section_to_dict(r, category: str = "") -> dict:
    d = asdict(r)
    d["effective_completion_score"] = r.effective_completion_score()
    d["resolved_memo_count"] = r.resolved_memo_count()
    d["category"] = category
    d["truncated"] = bool(r.llm_meta.get("truncated", False))
    return d


@router.get("/sessions/{session_id}/results")
def get_results(session_id: str):
    results = load_results(session_id)
    if results is None:
        raise HTTPException(status_code=404, detail="Results not found")

    category_map: dict[str, str] = {}
    session = get_session(session_id)
    if session:
        try:
            form = load_form(session.program_code)
            category_map = {s.id: s.category for s in form.sections}
        except Exception:
            pass

    overall = calculate_overall_completion(results)
    return {
        "overall_completion": overall,
        "sections": [_section_to_dict(r, category_map.get(r.section_id, "")) for r in results],
    }


class MemoResponseBody(BaseModel):
    response: str


@router.put("/sessions/{session_id}/results/{section_id}/memo/{memo_index}")
def update_memo(session_id: str, section_id: str, memo_index: int, body: MemoResponseBody):
    results = load_results(session_id)
    if results is None:
        raise HTTPException(status_code=404, detail="Results not found")

    section = next((r for r in results if r.section_id == section_id), None)
    if section is None:
        raise HTTPException(status_code=404, detail="Section not found")
    if memo_index >= len(section.inline_suggestions):
        raise HTTPException(status_code=400, detail="Memo index out of range")

    if get_usage_count(session_id, "memo") >= 3:
        raise HTTPException(status_code=429, detail="피드백 반영은 3회만 가능합니다.")
    increment_usage(session_id, "memo")

    section.inline_suggestions[memo_index].response = body.response
    save_results(session_id, results)

    overall = calculate_overall_completion(results)
    return {
        "section_id": section_id,
        "memo_index": memo_index,
        "effective_completion_score": section.effective_completion_score(),
        "overall_completion": overall,
    }


class EditBody(BaseModel):
    content: str


@router.put("/sessions/{session_id}/results/{section_id}/edit")
def edit_section(session_id: str, section_id: str, body: EditBody):
    results = load_results(session_id)
    if results is None:
        raise HTTPException(status_code=404, detail="Results not found")
    section = next((r for r in results if r.section_id == section_id), None)
    if section is None:
        raise HTTPException(status_code=404, detail="Section not found")
    if get_usage_count(session_id, "edit") >= 1:
        raise HTTPException(status_code=429, detail="섹션 수정은 1회만 가능합니다.")
    increment_usage(session_id, "edit")

    section.user_edited_content = body.content if body.content.strip() else None
    save_results(session_id, results)
    return {"section_id": section_id, "saved": True}


class RegenerateBody(BaseModel):
    memo_response: str | None = None
    memo_index: int | None = None


@router.post("/sessions/{session_id}/results/{section_id}/regenerate")
def regenerate(session_id: str, section_id: str, body: RegenerateBody = RegenerateBody()):
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    results = load_results(session_id)
    if results is None:
        raise HTTPException(status_code=404, detail="Results not found")

    feature_key = f"regenerate_{section_id}"
    if get_usage_count(session_id, feature_key) >= 1:
        raise HTTPException(status_code=429, detail="이 섹션의 고도화는 1회만 가능합니다.")
    increment_usage(session_id, feature_key)

    form = load_form(session.program_code)
    section = form.get_section(section_id)
    if section is None:
        raise HTTPException(status_code=404, detail="Section not found in form")

    prev = next((r for r in results if r.section_id == section_id), None)
    if prev is None:
        raise HTTPException(status_code=404, detail="Previous result not found")

    # 메모 패널에서 보완 내용을 직접 전달한 경우: 해당 메모 응답을 먼저 저장
    if body.memo_response is not None and body.memo_index is not None:
        idx = body.memo_index
        if 0 <= idx < len(prev.inline_suggestions):
            prev.inline_suggestions[idx].response = body.memo_response
            pre_save = [prev if r.section_id == section_id else r for r in results]
            save_results(session_id, pre_save)

    questions = load_initial_questions(_INITIAL_Q_PATH)
    followup = load_followup_questions(_FOLLOWUP_Q_PATH) if _FOLLOWUP_Q_PATH.exists() else None
    skills = load_skills(_SKILLS_DIR) if _SKILLS_DIR.exists() else []

    try:
        new_result = regenerate_section(
            form, section, questions, session.answers, skills, prev, followup,
            company_context=session.company_context,
        )
        new_result = apply_post_judgment(new_result)
        if prev.completion_score > 0 and new_result.completion_score < prev.completion_score:
            new_result.completion_score = prev.completion_score
        if prev.confidence_level == "green" and new_result.confidence_level != "green":
            new_result.confidence_level = "green"
    except Exception as e:
        logger.error("[재생성 실패] %s: %s", section_id, e)
        raise HTTPException(status_code=500, detail="섹션 재생성 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

    updated = [new_result if r.section_id == section_id else r for r in results]
    save_results(session_id, updated)

    overall = calculate_overall_completion(updated)
    return {
        "section": _section_to_dict(new_result),
        "overall_completion": overall,
    }


@router.post("/sessions/{session_id}/results/regenerate-all")
def regenerate_all(session_id: str):
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if get_usage_count(session_id, "regenerate_all") >= 1:
        raise HTTPException(status_code=429, detail="전체 고도화는 1회만 가능합니다.")
    increment_usage(session_id, "regenerate_all")

    results = load_results(session_id)
    if results is None:
        raise HTTPException(status_code=404, detail="Results not found")

    form = load_form(session.program_code)
    questions = load_initial_questions(_INITIAL_Q_PATH)
    followup = load_followup_questions(_FOLLOWUP_Q_PATH) if _FOLLOWUP_Q_PATH.exists() else None
    skills = load_skills(_SKILLS_DIR) if _SKILLS_DIR.exists() else []

    targets = [r for r in results if r.confidence_level in ("red", "yellow")]
    results_map = {r.section_id: r for r in results}
    for prev in targets:
        section = form.get_section(prev.section_id)
        if section is None:
            continue
        try:
            new_result = regenerate_section(
                form, section, questions, session.answers, skills, prev, followup,
                company_context=session.company_context,
            )
            new_result = apply_post_judgment(new_result)
            if prev.completion_score > 0 and new_result.completion_score < prev.completion_score:
                new_result.completion_score = prev.completion_score
            if prev.confidence_level == "green" and new_result.confidence_level != "green":
                new_result.confidence_level = "green"
            results_map[prev.section_id] = new_result
        except Exception as e:
            logger.error("[전체 고도화 실패] %s: %s", prev.section_id, e)

    final = list(results_map.values())
    save_results(session_id, final)
    overall = calculate_overall_completion(final)
    return {
        "sections": [_section_to_dict(r) for r in final],
        "overall_completion": overall,
    }


def _load_program_info(program_code: str) -> tuple[str, str, str]:
    """CSV에서 program_code에 해당하는 (name, 설명, 지원시기) 반환."""
    if not _PROGRAMS_CSV.exists():
        return program_code, "", ""
    try:
        with open(_PROGRAMS_CSV, encoding="utf-8-sig") as f:
            lines = [l for l in f if not l.startswith("#")]
        for row in csv.DictReader(lines):
            if row.get("program_code", "").strip() == program_code:
                return (
                    row.get("name", program_code),
                    row.get("설명", ""),
                    row.get("지원시기", ""),
                )
    except Exception:
        pass
    return program_code, "", ""


_ACTION_PLAN_PROMPT = """\
당신은 정부지원사업 전문 컨설턴트입니다.
사업계획서를 분석해서 대표자가 실제로 실행해야 할
액션플랜을 제시해주세요.

※ 아래 타임라인은 과거 공고일 기준으로 역산한 예상 일정입니다.
   실제 공고일과 차이가 있을 수 있으므로
   가능한 한 빠르게 실행하는 것을 권장합니다.

오늘 날짜: {today}
지원사업: {program_name}
예상 공고 시기: {expected_date}

[사업계획서 요약]
{plan_summary}

[미흡한 섹션]
{weak_sections}

[피드백 메모]
{feedback_memos}

위 정보를 바탕으로 아래 기준으로 액션플랜 작성:

1. 지금 사업에서 없는 것(MVP, 시장검증, 거래처, 인증, 특허 등)을 파악
2. 공고 예상 시기 역산해서 월별 실행 타임라인 작성
3. 각 액션은 아래 형식으로 작성:

- [실행 항목]
   WHY: [왜 이것이 필요한지 - 심사위원 관점에서]
   HOW: [어떻게 실행할지 - 구체적 방법, 예산, 소요 기간]
   기한: [언제까지]

출력 형식:
## 현재 상태 진단
[지금 사업에서 부족한 것 2~3가지]

## 실행 로드맵
### {today} 기준 1~2개월: [단계명]
- [실행 항목]
   WHY: [심사위원 관점에서 왜 필요한지]
   HOW: [구체적 실행 방법, 예산, 소요 기간]
   기한: [구체적 날짜]

### {today} 기준 3~4개월: [단계명]
- [실행 항목]
   WHY: [심사위원 관점에서 왜 필요한지]
   HOW: [구체적 실행 방법, 예산, 소요 기간]
   기한: [구체적 날짜]

### 공고 1개월 전: 사업계획서 최종 업데이트
- 실행 결과 수치를 사업계획서에 반영
- [어떤 섹션에 어떤 내용 추가할지]

절대 금지:
- '사업계획서를 보완하세요' 같은 문서 작업 안내
- 막연한 조언 (예: '시장조사를 하세요')
- 공고일 무시한 타임라인
- WHY/HOW 없이 항목만 나열
"""

_ACTION_PLAN_SYSTEM = "당신은 정부지원사업 전문 컨설턴트입니다. 사업계획서와 심사기준을 분석해 창업자에게 실질적인 액션플랜을 제시합니다."


@router.post("/sessions/{session_id}/action-plan")
def generate_action_plan(session_id: str):
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if get_usage_count(session_id, "action_plan") >= 1:
        raise HTTPException(status_code=429, detail="액션플랜 확인은 1회만 가능합니다.")
    increment_usage(session_id, "action_plan")
    results = load_results(session_id)
    if results is None:
        raise HTTPException(status_code=404, detail="Results not found")

    today = datetime.now().strftime("%Y년 %m월 %d일")

    # 1. 지원사업 정보 (CSV → 없으면 form에서 name만)
    program_name, _criteria, expected_date_raw = _load_program_info(session.program_code)
    if not program_name or program_name == session.program_code:
        try:
            form = load_form(session.program_code)
            program_name = form.program_name
        except Exception:
            pass
    expected_date = expected_date_raw.strip() if expected_date_raw.strip() else "미정 (통상 1~2월 공고)"

    # 2. 미흡한 섹션 (red/yellow)
    weak = [r for r in results if r.confidence_level in ("red", "yellow")]
    weak_sections = "\n".join(
        f"- [{r.confidence_level.upper()}] {r.section_title}" for r in weak
    ) or "없음"

    # 3. 피드백 메모 (warning/critical만, response 있으면 함께 표시)
    memo_lines = []
    for r in results:
        for s in r.inline_suggestions:
            if s.severity in ("warning", "critical"):
                line = f"[{r.section_title}] {s.note}"
                if s.response.strip():
                    line += f" → 보완: {s.response.strip()}"
                memo_lines.append(line)
    feedback_memos = "\n".join(f"- {l}" for l in memo_lines) or "없음"

    # 4. 사업계획서 요약 (섹션별 최대 300자)
    plan_summary = "\n\n".join(
        f"[{r.section_title}]\n{(r.user_edited_content or r.content or '').strip()[:300]}"
        for r in results
    )

    from core.llm import call_claude
    try:
        text, _ = call_claude(
            system=_ACTION_PLAN_SYSTEM,
            user=_ACTION_PLAN_PROMPT.format(
                today=today,
                program_name=program_name,
                expected_date=expected_date,
                plan_summary=plan_summary,
                weak_sections=weak_sections,
                feedback_memos=feedback_memos,
            ),
            model="claude-haiku-4-5-20251001",
            max_tokens=6000,
            temperature=0.4,
            purpose="action_plan",
            metadata={"session_id": session_id},
        )
    except Exception as e:
        logger.error("[액션플랜 생성 실패] %s: %s", session_id, e)
        raise HTTPException(status_code=500, detail="액션플랜 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")
    return {"action_plan": text}


_DOC_CHECK_SYSTEM = "당신은 사업계획서 교정 전문가입니다. 오류를 정확하고 간결하게 지적합니다."

_DOC_CHECK_PROMPT = """\
당신은 사업계획서 교정 전문가입니다.
아래 사업계획서를 검토해서 두 가지를 점검해주세요.

[사업계획서 내용]
{sections_content}

1. 오탈자/맞춤법 오류
   - 명백한 오타, 띄어쓰기 오류, 맞춤법 오류만 지적
   - 형식: 섹션명 | 오류 내용 | 수정 제안

2. 논리 일관성 오류
   - 섹션 간 수치/사실이 충돌하는 경우
   - 예: A섹션 '5년 경력' vs B섹션 '7년 경력'
   - 형식: 충돌 위치 | 내용 A | 내용 B | 권장 수정

오류가 없으면 '오류 없음'으로 표시.
5개 이하로 핵심만 지적할 것.
"""


@router.post("/sessions/{session_id}/document-check")
def document_check(session_id: str):
    results = load_results(session_id)
    if results is None:
        raise HTTPException(status_code=404, detail="Results not found")

    sections_content = "\n\n".join(
        f"## {r.section_title}\n{(r.user_edited_content or r.content or '').strip()[:500]}"
        for r in results
    )

    from core.llm import call_claude
    text, _ = call_claude(
        system=_DOC_CHECK_SYSTEM,
        user=_DOC_CHECK_PROMPT.format(sections_content=sections_content),
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        temperature=0.2,
        purpose="document_check",
    )
    return {"result": text}


@router.get("/sessions/{session_id}/score")
def get_rubric_score(session_id: str):
    results = load_results(session_id)
    if results is None:
        raise HTTPException(status_code=404, detail="Results not found")

    full_text = "\n\n".join(
        f"[{r.section_title}]\n{r.user_edited_content or r.content or ''}"
        for r in results
    )

    from core.rubric_scorer import score_with_haiku
    _t0 = time.time()
    ps = score_with_haiku(full_text)
    _duration_ms = int((time.time() - _t0) * 1000)

    # delivery2 structurer는 call_claude()를 우회하므로 여기서 직접 로그 기록
    try:
        _log_path = Path("logs/llm_calls.jsonl")
        _log_path.parent.mkdir(parents=True, exist_ok=True)
        _entry = {
            "call_id": str(uuid.uuid4())[:8],
            "timestamp": datetime.utcnow().isoformat(),
            "model": "claude-haiku-4-5-20251001",
            "purpose": "rubric_score",
            "session_id": session_id,
            "input_tokens": None,
            "output_tokens": None,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "duration_ms": _duration_ms,
            "stop_reason": "end_turn",
            "system_preview": "(delivery2 structurer — token counts unavailable)",
            "user_preview": full_text[:300],
            "response_full": f"prob_pct={ps.prob_pct}" if ps else "None",
        }
        with _log_path.open("a", encoding="utf-8") as _f:
            _f.write(json.dumps(_entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

    if ps is None:
        return {"available": False}

    return {
        "available": True,
        "prob_pct": ps.prob_pct,
        "base_rate_pct": round(ps.base_rate * 100),
        "hits": [
            {"feature": h["feature"], "direction": h.get("direction", "+")}
            for h in ps.significant_hits
        ],
    }


@router.get("/sessions/{session_id}/usage")
def get_usage(session_id: str):
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    limits = {"generate": 1, "feedback": 1, "memo": 3, "regenerate": 1, "edit": 1, "action_plan": 1, "regenerate_all": 1}
    return {
        feature: {"used": get_usage_count(session_id, feature), "max": max_val}
        for feature, max_val in limits.items()
    }


@router.get("/sessions/{session_id}/export/docx")
def export_docx(session_id: str, business_name: str = "(미지정)"):
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    results = load_results(session_id)
    if results is None:
        raise HTTPException(status_code=404, detail="Results not found")

    form = load_form(session.program_code)
    buf = export_to_docx(form, results, business_name)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="bizplan_{session_id}.docx"'},
    )
