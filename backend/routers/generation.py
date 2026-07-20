"""초안 생성 라우터 — SSE 스트리밍으로 섹션별 진행 현황 전달."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
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
    update_program_code,
    save_draft_analysis,
    load_draft_analysis,
)
from core.context_extraction import extract_company_context
from core.forms import load_form
from core.generation import (
    evaluate_section, apply_eval_result,
    evaluate_business_plan, attach_strategic_feedbacks,
    generate_section,
    generate_framework_draft,
    generate_one_framework_section,
    SectionResult,
    convert_to_form,
    convert_to_form_v2,
    analyze_framework_draft,
    map_analysis_to_form,
    compute_draft_hash,
    build_parallel_prior_note,
    FRAMEWORK_SECTIONS,
    _SEQUENTIAL_IDS,
    _PARALLEL_IDS,
    _SECTION_INNER_DEADLINE_S,
    _build_full_prior_context,
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
    # 혁신바우처 전용 — 사용자가 선택한 바우처 서비스(컨설팅/기술지원/마케팅).
    # 다른 양식에서는 생략(None). core.generation에서 화이트리스트 검증됨.
    voucher_options: list[str] | None = None
    # 갭 보완 인터뷰 답변 {질문id: 답변}. 양식 YAML gap_questions의 고정 5문항에 대한
    # 사용자 답변 — 생략/빈 답변 허용(해당 질문은 무시).
    gap_answers: dict[str, str] | None = None

_ROOT_DIR = Path(__file__).resolve().parent.parent.parent  # bizplan-mvp/
_INITIAL_Q_PATH = _ROOT_DIR / "data" / "interview" / "initial_questions.json"
_FOLLOWUP_Q_PATH = _ROOT_DIR / "data" / "interview" / "questions.json"
_SKILLS_DIR = _ROOT_DIR / "skills"

_executor = ThreadPoolExecutor(max_workers=10)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# SSE 주석 라인 — 이벤트가 아니므로 프론트 파서(event:/data: 없는 청크는 무시)가 건너뛴다.
# Railway·Next.js 프록시가 응답 무침묵 구간을 연결 끊김으로 판단하지 않도록 주기적으로 흘려보낸다.
_KEEPALIVE = ": keepalive\n\n"
_KEEPALIVE_INTERVAL_S = 15.0


async def _run_with_keepalive(loop, fn, timeout: float):
    """executor 작업을 돌리며 15초마다 keepalive 신호를 방출하는 async generator.

    yield ("ping", None)  — 대기 중 15초 경과
    yield ("done", result) — 작업 완료(정상 반환값)
    yield ("error", exc)   — 작업이 예외로 종료
    yield ("timeout", None) — timeout 초과 (executor 스레드는 계속 도므로 호출측에서 cancel 신호 필요)
    generator는 done/error/timeout 중 하나를 방출한 직후 종료한다.
    """
    task = loop.run_in_executor(_executor, fn)
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            yield ("timeout", None)
            return
        done, _pending = await asyncio.wait(
            {task}, timeout=min(_KEEPALIVE_INTERVAL_S, remaining)
        )
        if task in done:
            try:
                yield ("done", task.result())
            except Exception as e:  # noqa: BLE001 — 상위에서 red 플레이스홀더로 처리
                yield ("error", e)
            return
        yield ("ping", None)


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
        # 변환 결과 우선, 없으면 프레임워크 초안(draft 단계 심사위원 피드백)
        results = load_results(session_id)
        source = "results"
        if not results:
            results = load_framework_draft(session_id)
            source = "framework"
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

        # 전략 평가는 마지막 단일 장기 호출이라 침묵 구간이 길다 → keepalive로 프록시 연결 유지
        strategic_count = 0
        async for kind, payload in _run_with_keepalive(loop, _strategic, 180.0):
            if kind == "ping":
                yield _KEEPALIVE
            elif kind == "done":
                strategic_count = payload
            elif kind == "error":
                logger.error("[전략 평가 실패] %s: %s", session_id, payload)
            elif kind == "timeout":
                logger.error("[전략 평가 타임아웃] %s", session_id)

        ordered = [results_map[r.section_id] for r in results if r.section_id in results_map]
        if source == "framework":
            save_framework_draft(session_id, ordered)
        else:
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

        # 생성 모드 (DRAFT_PARALLEL, 기본 켬)
        # - 병렬(기본): 9개 섹션 전체 동시 생성 — 누적 컨텍스트 대신 역할 경계 노트 +
        #   company_context 앵커로 중복·수치 불일치 차단. 소요 = 최장 섹션 1개분.
        # - 순차(DRAFT_PARALLEL=0, 롤백용): 기존 하이브리드 그대로
        #   Phase 1: 1-1~3-2 누적 컨텍스트 순차 / Phase 2: 4-1 병렬
        parallel_mode = os.getenv("DRAFT_PARALLEL", "1") != "0"
        if parallel_mode:
            seq_sections = []
            par_sections = list(FRAMEWORK_SECTIONS)
        else:
            seq_sections = [s for s in FRAMEWORK_SECTIONS if s["id"] in _SEQUENTIAL_IDS]
            par_sections = [s for s in FRAMEWORK_SECTIONS if s["id"] in _PARALLEL_IDS]

        seq_results: list[SectionResult] = []  # 누적 컨텍스트용
        all_results: list[SectionResult] = []

        # Phase 1: 순차 생성
        for sec in seq_sections:
            prior_ctx = _build_full_prior_context(seq_results)
            cancel_event = threading.Event()
            # 섹션 내부 마감(165s). 라우터 안전망(180s)보다 먼저 검수·재생성을 끊어
            # 1차 초안이 통째로 버려지는 것을 막는다.
            deadline = time.monotonic() + _SECTION_INNER_DEADLINE_S

            def _gen_seq(s=sec, pc=prior_ctx, ce=cancel_event, dl=deadline):
                return generate_one_framework_section(
                    s, questions, session.answers, skills, company_context,
                    prior_context=pc, cancel_event=ce, deadline=dl,
                )

            r: SectionResult | None = None
            async for kind, payload in _run_with_keepalive(loop, _gen_seq, 180.0):
                if kind == "ping":
                    yield _KEEPALIVE
                elif kind == "done":
                    r, _ = payload
                elif kind == "error":
                    logger.error("[섹션 생성 실패] %s: %s", sec["id"], payload)
                    r = SectionResult(
                        section_id=sec["id"],
                        section_title=sec["title"],
                        content="",
                        confidence_level="red",
                        reasoning=f"생성 실패: {payload}",
                        missing_info=["섹션 생성 오류 — 재시도 필요"],
                        completion_score=0,
                    )
                elif kind == "timeout":
                    cancel_event.set()
                    logger.error("[섹션 타임아웃] %s", sec["id"])
                    r = SectionResult(
                        section_id=sec["id"],
                        section_title=sec["title"],
                        content="",
                        confidence_level="red",
                        reasoning="타임아웃: 섹션 생성 180초 초과",
                        missing_info=["생성 타임아웃 — 재시도 필요"],
                        completion_score=0,
                    )

            seq_results.append(r)
            all_results.append(r)
            yield _sse("section_done", {
                "section_id": r.section_id,
                "section_title": r.section_title,
                "confidence_level": r.confidence_level,
                "completion_score": r.effective_completion_score(),
            })

        # Phase 2: 병렬 생성 (Team — 4-1만)
        if par_sections:
            par_queue: asyncio.Queue = asyncio.Queue()

            async def _run_par(sec: dict) -> None:
                cancel_event = threading.Event()
                deadline = time.monotonic() + _SECTION_INNER_DEADLINE_S

                def _gen():
                    return generate_one_framework_section(
                        sec, questions, session.answers, skills, company_context,
                        # 병렬 모드: 역할 경계 노트로 섹션 간 중복·침범 차단
                        prior_context=build_parallel_prior_note(sec) if parallel_mode else "",
                        cancel_event=cancel_event, deadline=deadline,
                    )
                r = SectionResult(
                    section_id=sec["id"],
                    section_title=sec["title"],
                    content="",
                    confidence_level="red",
                    reasoning="초기화 — 생성 시작 전",
                    missing_info=["섹션 생성 오류 — 재시도 필요"],
                    completion_score=0,
                )
                try:
                    r, _ = await asyncio.wait_for(
                        loop.run_in_executor(_executor, _gen),
                        timeout=180.0,
                    )
                except asyncio.TimeoutError:
                    cancel_event.set()
                    logger.error("[병렬 섹션 타임아웃] %s", sec["id"])
                    r = SectionResult(
                        section_id=sec["id"],
                        section_title=sec["title"],
                        content="",
                        confidence_level="red",
                        reasoning="타임아웃: 섹션 생성 180초 초과",
                        missing_info=["생성 타임아웃 — 재시도 필요"],
                        completion_score=0,
                    )
                except BaseException as e:
                    cancel_event.set()
                    logger.error("[병렬 섹션 생성 실패] %s: %s", sec["id"], e)
                    r = SectionResult(
                        section_id=sec["id"],
                        section_title=sec["title"],
                        content="",
                        confidence_level="red",
                        reasoning=f"생성 실패: {e}",
                        missing_info=["섹션 생성 오류 — 재시도 필요"],
                        completion_score=0,
                    )
                await par_queue.put(r)

            par_tasks = [asyncio.create_task(_run_par(s)) for s in par_sections]
            collected = 0
            while collected < len(par_sections):
                try:
                    r = await asyncio.wait_for(par_queue.get(), timeout=_KEEPALIVE_INTERVAL_S)
                except asyncio.TimeoutError:
                    yield _KEEPALIVE
                    continue
                collected += 1
                all_results.append(r)
                yield _sse("section_done", {
                    "section_id": r.section_id,
                    "section_title": r.section_title,
                    "confidence_level": r.confidence_level,
                    "completion_score": r.effective_completion_score(),
                })
            await asyncio.gather(*par_tasks, return_exceptions=True)

        # FRAMEWORK_SECTIONS 순서로 정렬 후 저장
        section_order = {s["id"]: i for i, s in enumerate(FRAMEWORK_SECTIONS)}
        results = sorted(all_results, key=lambda r: section_order.get(r.section_id, 999))

        save_framework_draft(session_id, results)

        from core.judgment import calculate_overall_completion
        overall = calculate_overall_completion(results)
        yield _sse("all_done", {
            "overall_completion": overall,
            "total_sections": len(results),
        })

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/forms/{program_code}/gap_questions")
async def get_gap_questions(program_code: str):
    """양식 변환 전 갭 보완 인터뷰 고정 질문 조회 (양식 YAML gap_questions)."""
    try:
        form = load_form(program_code)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Form not found")
    except Exception as e:  # noqa: BLE001
        logger.error("[gap_questions 로드 실패] %s: %s", program_code, e)
        raise HTTPException(status_code=500, detail="양식 로드 실패")
    return {"program_code": program_code, "questions": form.gap_questions}


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

        # 진행 이벤트 큐 — executor 스레드에서 발생하는 단계·섹션 완료를 실시간 SSE로 중계
        # (기존에는 전체 변환이 끝난 뒤 section_done을 한꺼번에 쏴서 진행도가 0에 머물렀음)
        progress_q: asyncio.Queue = asyncio.Queue()

        def _emit(kind: str, payload: dict) -> None:
            loop.call_soon_threadsafe(progress_q.put_nowait, (kind, payload))

        def _convert_all():
            # v3 파이프라인: (1-a) 초안 분석(캐시) → (1-b) 양식 매핑 → 소스별 변환
            _emit("stage", {"stage": "analyzing"})
            draft_hash = compute_draft_hash(framework_results)
            analysis = load_draft_analysis(session_id)
            if not analysis or analysis.get("_draft_hash") != draft_hash:
                # 초안이 새로 생성/수정됨 → 재분석 (그 외에는 캐시 재사용 — 재변환 시 중복 분석 없음)
                analysis = analyze_framework_draft(framework_results)
                analysis["_draft_hash"] = draft_hash
                save_draft_analysis(session_id, analysis)
            _emit("stage", {"stage": "mapping"})
            mapping = map_analysis_to_form(analysis, form)
            sess = get_session(session_id)
            _emit("stage", {"stage": "converting"})
            return convert_to_form(
                framework_results, form, skills,
                voucher_options=body.voucher_options,
                section_sources=mapping,
                company_context=(sess.company_context if sess else None),
                draft_analysis=analysis,
                progress_cb=_emit,
                gap_answers=body.gap_answers,
            )

        task = loop.run_in_executor(_executor, _convert_all)
        deadline = loop.time() + 240.0
        results = None
        errored = False
        while not (task.done() and progress_q.empty()):
            remaining = deadline - loop.time()
            if remaining <= 0:
                yield _sse("error", {"message": "양식 변환 시간 초과 (240초). 다시 시도해주세요."})
                errored = True
                break
            wait_s = 0.5 if task.done() else min(_KEEPALIVE_INTERVAL_S, remaining)
            try:
                kind, payload = await asyncio.wait_for(progress_q.get(), timeout=wait_s)
            except asyncio.TimeoutError:
                if not task.done():
                    yield _KEEPALIVE
                continue
            yield _sse("section_done" if kind == "section" else "stage", payload)

        if not errored:
            try:
                results = await task
            except Exception as e:  # noqa: BLE001
                logger.error("[양식 변환 실패] %s: %s", session_id, e)
                yield _sse("error", {"message": f"양식 변환 중 오류: {e}"})
                errored = True
        if errored:
            return

        save_results(session_id, results)
        # 변환 성공 → 세션 program_code를 선택 양식으로 갱신
        # (결과 화면 양식명 표시·DOCX export의 load_form이 이 값을 사용)
        update_program_code(session_id, body.program_code)

        from core.judgment import calculate_overall_completion
        overall = calculate_overall_completion(results)
        yield _sse("all_done", {
            "overall_completion": overall,
            "total_sections": len(results),
        })

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/sessions/{session_id}/convert_to_form_v2")
async def convert_to_form_v2_endpoint(session_id: str, body: ConvertToFormRequest):
    """양식 변환 v2 — 재배치 방식 (LLM 1회 매핑 결정 + 내용 복붙). 성능 테스트용."""
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
        except Exception as e:
            yield _sse("error", {"message": f"양식 로드 실패: {e}"})
            return

        total = len(form.sections)
        yield _sse("init", {
            "sections": [{"id": s.id, "title": s.title, "order": s.order} for s in form.sections],
            "total": total,
        })

        def _convert_all():
            return convert_to_form_v2(framework_results, form)

        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(_executor, _convert_all),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            yield _sse("error", {"message": "양식 변환 시간 초과 (60초). 다시 시도해주세요."})
            return
        except Exception as e:
            logger.error("[양식 변환 v2 실패] %s: %s", session_id, e)
            yield _sse("error", {"message": f"양식 변환 중 오류: {e}"})
            return

        save_results(session_id, results)
        update_program_code(session_id, body.program_code)

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
    overall = calculate_overall_completion(results)
    return {
        "overall_completion": overall,
        "sections": [
            {
                "section_id": r.section_id,
                "section_title": r.section_title,
                "content": r.display_content(),
                "confidence_level": r.confidence_level,
                "completion_score": r.effective_completion_score(),
                "effective_completion_score": r.effective_completion_score(),
                "resolved_memo_count": r.resolved_memo_count(),
                "reasoning": r.reasoning,
                "used_answer_ids": r.used_answer_ids,
                "missing_info": r.missing_info,
                "user_edited_content": r.user_edited_content,
                "rubric_check": r.rubric_check,
                "llm_meta": r.llm_meta,
                "completion_reasoning": r.completion_reasoning,
                "category": "",
                "truncated": bool(r.llm_meta.get("truncated", False)),
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
