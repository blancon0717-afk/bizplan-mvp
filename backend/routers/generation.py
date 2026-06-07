"""초안 생성 라우터 — SSE 스트리밍으로 섹션별 진행 현황 전달."""
from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.session_store import (
    get_session,
    get_usage_count,
    increment_usage,
    load_results,
    save_company_context,
    save_results,
    save_framework_draft,
    load_framework_draft,
)
from core.context_extraction import extract_company_context
from core.forms import load_form
from core.generation import (
    evaluate_section, apply_eval_result,
    evaluate_business_plan, attach_strategic_feedbacks,
    generate_section,
    generate_framework_draft,
    convert_to_form,
    FRAMEWORK_SECTIONS,
)
from core.interview import load_initial_questions, load_followup_questions
from core.judgment import apply_post_judgment, calculate_overall_completion
from core.skills import load_skills

router = APIRouter(tags=["generation"])
logger = logging.getLogger(__name__)

_SEVERITY_ORDER: dict[str, int] = {"critical": 0, "warning": 1, "info": 2}


class GenerateRequest(BaseModel):
    section_ids: list[str] | None = None


class ConvertToFormRequest(BaseModel):
    program_code: str

_INITIAL_Q_PATH = Path("data/interview/initial_questions.json")
_FOLLOWUP_Q_PATH = Path("data/interview/questions.json")
_SKILLS_DIR = Path("skills")

_executor = ThreadPoolExecutor(max_workers=10)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/sessions/{session_id}/generate")
async def generate_draft(session_id: str, body: GenerateRequest = Body(default=None)):
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if get_usage_count(session_id, "generate") >= 1:
        raise HTTPException(status_code=429, detail="사업계획서 생성은 1회만 가능합니다.")
    increment_usage(session_id, "generate")

    async def event_generator():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        try:
            form = load_form(session.program_code)
            questions = load_initial_questions(_INITIAL_Q_PATH)
            followup = (
                load_followup_questions(_FOLLOWUP_Q_PATH)
                if _FOLLOWUP_Q_PATH.exists()
                else None
            )
            skills = load_skills(_SKILLS_DIR) if _SKILLS_DIR.exists() else []
        except Exception as e:
            yield _sse("error", {"message": f"초기화 실패: {e}"})
            return

        # 컨텍스트가 아직 없으면 즉시 추출 (lazy fallback — 인터뷰 자동 트리거가
        # 누락된 구버전 세션이거나 답변이 늦게 채워진 경우 보완)
        company_context = session.company_context
        if not company_context:
            # 요청 시작 후 다른 경로(인터뷰 자동 트리거)가 먼저 추출을 완료했을 수
            # 있으므로 디스크에서 재확인해 중복 추출 방지
            fresh = get_session(session.session_id)
            if fresh:
                company_context = fresh.company_context
        if not company_context:
            try:
                yield _sse("context_extracting", {"message": "인터뷰 답변 정제 중..."})
                company_context = await loop.run_in_executor(
                    _executor,
                    lambda: extract_company_context(questions, session.answers),
                )
                save_company_context(session.session_id, company_context)
                meta = company_context.get("_meta", {}) if isinstance(company_context, dict) else {}
                yield _sse("context_extracted", {
                    "input_tokens": meta.get("input_tokens"),
                    "output_tokens": meta.get("output_tokens"),
                    "duration_ms": meta.get("duration_ms"),
                })
            except Exception as e:
                yield _sse("error", {"message": f"컨텍스트 추출 실패: {e}"})
                return

        target_sections = form.sections
        if body and body.section_ids:
            id_set = set(body.section_ids)
            target_sections = [s for s in form.sections if s.id in id_set]

        total = len(target_sections)
        yield _sse("init", {
            "sections": [{"id": s.id, "title": s.title, "order": s.order} for s in target_sections],
            "total": total,
        })

        async def run_one(section):
            def _gen():
                result = generate_section(
                    form, section, questions, session.answers, skills, followup,
                    company_context=company_context,
                )
                return apply_post_judgment(result)
            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(_executor, _gen),
                    timeout=90.0,
                )
                await queue.put(result)
            except asyncio.TimeoutError:
                logger.error("[섹션 타임아웃] %s", section.id)
                await queue.put({"__error__": True, "section_id": section.id, "message": "timeout"})
                return
            except Exception as e:
                logger.error("[섹션 생성 실패] %s: %s", section.id, e)
                await queue.put({"__error__": True, "section_id": section.id, "message": str(e)})

        results_map: dict = {}
        if target_sections:
            # 1단계: 첫 섹션 단독 생성 → Anthropic 서버에 Skills 블록 캐시 워밍
            first_task = asyncio.create_task(run_one(target_sections[0]))
            result = await queue.get()
            if isinstance(result, dict) and result.get("__error__"):
                yield _sse("section_error", {
                    "section_id": result["section_id"],
                    "message": "섹션 생성 중 오류가 발생했습니다. 재생성을 시도해주세요.",
                })
            else:
                results_map[result.section_id] = result
                yield _sse("section_done", {
                    "section_id": result.section_id,
                    "section_title": result.section_title,
                    "confidence_level": result.confidence_level,
                    "completion_score": result.effective_completion_score(),
                })
            await first_task

            # 2단계: 나머지 섹션 병렬 생성 → 캐시 히트로 Skills 토큰 90% 절감
            remaining = target_sections[1:]
            if remaining:
                rem_tasks = [asyncio.create_task(run_one(s)) for s in remaining]
                for _ in range(len(remaining)):
                    result = await queue.get()
                    if isinstance(result, dict) and result.get("__error__"):
                        yield _sse("section_error", {
                            "section_id": result["section_id"],
                            "message": "섹션 생성 중 오류가 발생했습니다. 재생성을 시도해주세요.",
                        })
                    else:
                        results_map[result.section_id] = result
                        yield _sse("section_done", {
                            "section_id": result.section_id,
                            "section_title": result.section_title,
                            "confidence_level": result.confidence_level,
                            "completion_score": result.effective_completion_score(),
                        })
                await asyncio.gather(*rem_tasks, return_exceptions=True)

        # 기존 결과와 병합 (부분 생성 지원)
        existing = load_results(session_id) or []
        merged_map = {r.section_id: r for r in existing}
        merged_map.update(results_map)
        ordered = [merged_map[s.id] for s in form.sections if s.id in merged_map]
        save_results(session_id, ordered)

        overall = calculate_overall_completion(ordered)
        yield _sse("all_done", {
            "overall_completion": overall,
            "total_sections": len(ordered),
        })

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/sessions/{session_id}/sections")
def get_sections(session_id: str):
    """양식 섹션 목록 반환 — 초안 생성 전 섹션 선택 UI용."""
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        form = load_form(session.program_code)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "sections": [
            {"id": s.id, "title": s.title, "order": s.order}
            for s in form.sections
        ]
    }


@router.post("/sessions/{session_id}/feedback")
async def generate_feedback(session_id: str):
    """피드백 생성 — 기존 초안에 심사자·전략 피드백을 SSE로 스트리밍."""
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if get_usage_count(session_id, "feedback") >= 1:
        raise HTTPException(status_code=429, detail="피드백 확인은 1회만 가능합니다.")
    increment_usage(session_id, "feedback")

    async def event_generator():
        results = load_results(session_id)
        if not results:
            yield _sse("error", {"message": "생성된 초안이 없습니다."})
            return

        results_map = {r.section_id: r for r in results}
        total = len(results)
        yield _sse("init", {"total": total})

        loop = asyncio.get_event_loop()

        def _to_suggestion(s) -> dict:
            return {"anchor_text": s.anchor_text, "note": s.note, "severity": s.severity, "response": s.response}

        # 순차 처리: 섹션 순서대로 1 → 2 → 3
        for result in results:
            def _eval(r=result):
                eval_data = evaluate_section(r, r.section_id, r.section_title)
                apply_eval_result(r, eval_data)
            await loop.run_in_executor(_executor, _eval)
            result.inline_suggestions.sort(key=lambda s: _SEVERITY_ORDER.get(s.severity, 1))
            result.inline_suggestions = result.inline_suggestions[:5]
            yield _sse("section_feedback_done", {
                "section_id": result.section_id,
                "confidence_level": result.confidence_level,
                "completion_score": result.effective_completion_score(),
                "inline_suggestions": [_to_suggestion(s) for s in result.inline_suggestions],
            })

        # 전략 평가
        def _strategic():
            ordered_inner = list(results_map.values())
            session = get_session(session_id)
            ctx = session.company_context if session else None
            feedbacks = evaluate_business_plan(ordered_inner, company_context=ctx)
            attach_strategic_feedbacks(results_map, feedbacks)
            return len(feedbacks)

        strategic_count = await loop.run_in_executor(_executor, _strategic)

        ordered = [results_map[r.section_id] for r in results if r.section_id in results_map]
        save_results(session_id, ordered)

        # 전략 피드백 포함된 최종 suggestions를 all_done 페이로드로 전달 (재조회 불필요)
        yield _sse("all_done", {
            "strategic_feedback_count": strategic_count,
            "sections": [
                {
                    "section_id": r.section_id,
                    "inline_suggestions": [_to_suggestion(s) for s in r.inline_suggestions],
                }
                for r in ordered
            ],
        })

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/sessions/{session_id}/feedback/{section_id}")
async def generate_single_section_feedback(session_id: str, section_id: str):
    """단일 섹션 피드백 — 섹션 재생성 후 해당 섹션만 재평가."""
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if get_usage_count(session_id, "regenerate") >= 1:
        raise HTTPException(status_code=429, detail="섹션별 고도화는 1회만 가능합니다.")
    increment_usage(session_id, "regenerate")

    results = load_results(session_id)
    if not results:
        raise HTTPException(status_code=404, detail="No draft found")

    target = next((r for r in results if r.section_id == section_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Section '{section_id}' not found")

    loop = asyncio.get_event_loop()

    def _eval():
        eval_data = evaluate_section(target, target.section_id, target.section_title)
        apply_eval_result(target, eval_data)

    await loop.run_in_executor(_executor, _eval)
    target.inline_suggestions.sort(key=lambda s: _SEVERITY_ORDER.get(s.severity, 1))
    target.inline_suggestions = target.inline_suggestions[:5]

    results_map = {r.section_id: r for r in results}
    save_results(session_id, list(results_map.values()))

    return {
        "section_id": target.section_id,
        "confidence_level": target.confidence_level,
        "completion_score": target.effective_completion_score(),
        "inline_suggestions": [
            {"anchor_text": s.anchor_text, "note": s.note, "severity": s.severity, "response": s.response}
            for s in target.inline_suggestions
        ],
    }


@router.post("/sessions/{session_id}/generate_framework")
async def generate_framework(session_id: str):
    """프레임워크 초안 생성 — SSE 스트리밍 (양식 무관, DRAFT_WRITING_GUIDE 기준)."""
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    async def event_generator():
        loop = asyncio.get_event_loop()

        try:
            questions = load_initial_questions(_INITIAL_Q_PATH)
            skills = load_skills(_SKILLS_DIR) if _SKILLS_DIR.exists() else []
        except Exception as e:
            yield _sse("error", {"message": f"초기화 실패: {e}"})
            return

        # 컨텍스트 추출
        company_context = session.company_context
        if not company_context:
            fresh = get_session(session_id)
            if fresh:
                company_context = fresh.company_context
        if not company_context:
            try:
                yield _sse("context_extracting", {"message": "인터뷰 답변 정제 중..."})
                company_context = await loop.run_in_executor(
                    _executor,
                    lambda: extract_company_context(questions, session.answers),
                )
                save_company_context(session_id, company_context)
            except Exception as e:
                yield _sse("error", {"message": f"컨텍스트 추출 실패: {e}"})
                return

        total = len(FRAMEWORK_SECTIONS)
        yield _sse("init", {
            "sections": [{"id": s["id"], "title": s["title"], "parent_title": s["parent_title"]} for s in FRAMEWORK_SECTIONS],
            "total": total,
        })

        def _generate_all():
            return generate_framework_draft(
                questions=questions,
                answers=session.answers,
                skills=skills,
                company_context=company_context,
            )

        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(_executor, _generate_all),
                timeout=180.0,
            )
        except asyncio.TimeoutError:
            yield _sse("error", {"message": "프레임워크 초안 생성 시간 초과 (180초). 다시 시도해주세요."})
            return
        except Exception as e:
            logger.error("[프레임워크 생성 실패] %s: %s", session_id, e)
            yield _sse("error", {"message": f"초안 생성 중 오류: {e}"})
            return

        save_framework_draft(session_id, results)

        for result in results:
            yield _sse("section_done", {
                "section_id": result.section_id,
                "section_title": result.section_title,
                "confidence_level": result.confidence_level,
                "completion_score": result.effective_completion_score(),
            })

        from core.judgment import calculate_overall_completion
        overall = calculate_overall_completion(results)
        yield _sse("all_done", {
            "overall_completion": overall,
            "total_sections": len(results),
        })

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/sessions/{session_id}/convert_to_form")
async def convert_to_form_endpoint(session_id: str, body: ConvertToFormRequest):
    """양식 변환 — 프레임워크 초안을 선택한 양식 섹션 구조로 변환 (SSE 스트리밍)."""
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    async def event_generator():
        loop = asyncio.get_event_loop()

        framework_results = load_framework_draft(session_id)
        if not framework_results:
            yield _sse("error", {"message": "프레임워크 초안이 없습니다. 먼저 초안을 생성해주세요."})
            return

        try:
            form = load_form(body.program_code)
            skills = load_skills(_SKILLS_DIR) if _SKILLS_DIR.exists() else []
        except Exception as e:
            yield _sse("error", {"message": f"양식 로드 실패: {e}"})
            return

        total = len(form.sections)
        yield _sse("init", {
            "sections": [{"id": s.id, "title": s.title, "order": s.order} for s in form.sections],
            "total": total,
        })

        def _convert_all():
            return convert_to_form(framework_results, form, skills)

        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(_executor, _convert_all),
                timeout=240.0,
            )
        except asyncio.TimeoutError:
            yield _sse("error", {"message": "양식 변환 시간 초과 (240초). 다시 시도해주세요."})
            return
        except Exception as e:
            logger.error("[양식 변환 실패] %s: %s", session_id, e)
            yield _sse("error", {"message": f"양식 변환 중 오류: {e}"})
            return

        save_results(session_id, results)

        for result in results:
            yield _sse("section_done", {
                "section_id": result.section_id,
                "section_title": result.section_title,
                "confidence_level": result.confidence_level,
                "completion_score": result.effective_completion_score(),
            })

        from core.judgment import calculate_overall_completion
        overall = calculate_overall_completion(results)
        yield _sse("all_done", {
            "overall_completion": overall,
            "total_sections": len(results),
        })

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/sessions/{session_id}/framework")
def get_framework_draft(session_id: str):
    """저장된 프레임워크 초안 반환."""
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    results = load_framework_draft(session_id)
    if not results:
        raise HTTPException(status_code=404, detail="프레임워크 초안이 없습니다. 먼저 생성해주세요.")
    return {
        "sections": [
            {
                "section_id": r.section_id,
                "section_title": r.section_title,
                "content": r.display_content(),
                "confidence_level": r.confidence_level,
                "completion_score": r.effective_completion_score(),
                "content_segments": [
                    {"text": s.text, "source": s.source, "source_qids": s.source_qids}
                    for s in r.content_segments
                ],
                "inline_suggestions": [
                    {"anchor_text": s.anchor_text, "note": s.note, "severity": s.severity, "response": s.response}
                    for s in r.inline_suggestions
                ],
            }
            for r in results
        ]
    }
