"""초안 생성 라우터 — SSE 스트리밍으로 섹션별 진행 현황 전달."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.session_store import (
    get_session,
    get_usage_count,
    increment_usage,
    is_unlocked,
    load_results,
    save_company_context,
    save_results,
    save_framework_draft,
    load_framework_draft,
    update_program_code,
    save_draft_analysis,
    load_draft_analysis,
    save_form_mapping,
    load_form_mapping,
)
from core.context_extraction import extract_company_context
from core.forms import load_form
from core.generation import (
    evaluate_section, apply_eval_result,
    evaluate_business_plan, attach_strategic_feedbacks,
    generate_section,
    generate_framework_draft,
    generate_one_framework_section,
    generate_framework_section,
    SectionResult,
    convert_to_form,
    convert_to_form_v2,
    analyze_framework_draft,
    map_analysis_to_form,
    compute_draft_hash,
    filter_gap_questions,
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
from core.benchmark import insight_note as benchmark_insight_note

router = APIRouter(tags=["generation"])
logger = logging.getLogger(__name__)

_SEVERITY_ORDER: dict[str, int] = {"critical": 0, "warning": 1, "info": 2}

# 빈 섹션 자동 재시도 설정 (초안 생성 배치 종료 직후 1회)
# 상한을 두는 이유: 다수가 비면 개별 섹션 문제가 아니라 API 장애이므로,
# 재시도에 매달리기보다 초안을 확정하고 사용자에게 단건 재생성 경로를 남긴다.
_MAX_AUTO_RETRY = 3
# 재시도는 프롬프트 캐시가 이미 따뜻해 실측 16~45초. 마지막 기회이므로 상한을 넉넉히 준다.
_AUTO_RETRY_TIMEOUT_S = 120.0


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

        # 미결제 세션은 잠긴 섹션의 피드백을 스트림에 싣지 않는다
        # (anchor_text가 원문을 인용하므로 그대로 내리면 잠금이 뚫림).
        # 평가·저장은 정상 수행 → 결제 후 재조회 시 피드백까지 함께 열림.
        hidden_ids = (
            _locked_section_ids()
            if source == "framework" and not is_unlocked(session_id)
            else set()
        )

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
            hidden = result.section_id in hidden_ids
            yield _sse("section_feedback_done", {
                "section_id": result.section_id,
                "confidence_level": result.confidence_level,
                "completion_score": result.effective_completion_score(),
                "locked": hidden,
                "inline_suggestions": [] if hidden else [_to_suggestion(s) for s in result.inline_suggestions],
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
                    "inline_suggestions": (
                        [] if r.section_id in hidden_ids
                        else [_to_suggestion(s) for s in r.inline_suggestions]
                    ),
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

        # 1차 결과 선저장 — 아래 재시도 도중 사용자가 이탈해도 여기까지는 보존된다.
        save_framework_draft(session_id, results)

        # 빈 섹션 자동 재시도 — 타임아웃·일시 장애로 비어버린 섹션만 1회씩 다시 생성.
        # 검수 게이트는 건너뛰고(시간 초과 재발 방지) 생성 자체에만 예산을 쓴다.
        empty_idx = [i for i, r in enumerate(results) if not (r.content or "").strip()]
        if len(empty_idx) > _MAX_AUTO_RETRY:
            # 다수가 비었다면 개별 섹션 문제가 아니라 API 장애 — 재시도는 낭비이므로 생략
            logger.error(
                "[초안 자동 재시도] 빈 섹션 %d개 > 상한 %d개 — API 장애로 판단해 재시도 생략",
                len(empty_idx), _MAX_AUTO_RETRY,
            )
        elif empty_idx:
            logger.info("[초안 자동 재시도] 빈 섹션 %d개 재생성 시작", len(empty_idx))
            for i in empty_idx:
                sec = next((s for s in FRAMEWORK_SECTIONS if s["id"] == results[i].section_id), None)
                if sec is None:
                    continue
                yield _sse("section_retrying", {
                    "section_id": results[i].section_id,
                    "section_title": results[i].section_title,
                })

                def _retry(s=sec):
                    return generate_framework_section(
                        s, questions, session.answers, skills, company_context,
                        prior_context=build_parallel_prior_note(s) if parallel_mode else "",
                        timeout_s=_AUTO_RETRY_TIMEOUT_S,
                        retries=0,
                    )

                retried: SectionResult | None = None
                async for kind, payload in _run_with_keepalive(
                    loop, _retry, _AUTO_RETRY_TIMEOUT_S + 15.0
                ):
                    if kind == "ping":
                        yield _KEEPALIVE
                    elif kind == "done":
                        retried = payload
                    elif kind == "error":
                        logger.error("[초안 자동 재시도 실패] %s: %s", sec["id"], payload)
                    elif kind == "timeout":
                        logger.error("[초안 자동 재시도 타임아웃] %s", sec["id"])

                # 재시도 결과가 비어 있으면 원본(실패 사유 보존)을 그대로 둔다
                if retried is not None and (retried.content or "").strip():
                    results[i] = retried
                    logger.info("[초안 자동 재시도] %s 복구 성공", sec["id"])
                    yield _sse("section_done", {
                        "section_id": retried.section_id,
                        "section_title": retried.section_title,
                        "confidence_level": retried.confidence_level,
                        "completion_score": retried.effective_completion_score(),
                    })

            save_framework_draft(session_id, results)

        from core.judgment import calculate_overall_completion
        overall = calculate_overall_completion(results)
        yield _sse("all_done", {
            "overall_completion": overall,
            "total_sections": len(results),
        })

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _get_draft_analysis(session_id: str, framework_results, draft_hash: str) -> dict:
    """초안 분석 로드 or 재분석(초안 수정 시). draft_hash를 결과에 태깅."""
    analysis = load_draft_analysis(session_id)
    if not analysis or analysis.get("_draft_hash") != draft_hash:
        analysis = analyze_framework_draft(framework_results)
        analysis["_draft_hash"] = draft_hash
        save_draft_analysis(session_id, analysis)
    return analysis


def _get_form_mapping(session_id: str, framework_results, form, draft_hash: str) -> dict:
    """양식 매핑 로드 or 생성 후 캐시. 갭 질문 필터와 변환이 공유(LLM 호출 순증 0회)."""
    mapping = load_form_mapping(session_id, form.program_code, draft_hash)
    if mapping is not None:
        return mapping
    analysis = _get_draft_analysis(session_id, framework_results, draft_hash)
    mapping = map_analysis_to_form(analysis, form)
    save_form_mapping(session_id, form.program_code, draft_hash, mapping)
    return mapping

# ── 벤치마크 부족 항목 → 갭 인터뷰 질문 (초안 기준 피처, 세션 캐시) ─────────────
def _benchmark_gap_questions(session_id: str, program_code: str, framework_results, form) -> list[dict]:
    """초안 전문의 피처를 추출(Sonnet 5, 본문 해시 캐시)해 합격작 대비 부족 항목 질문을 만든다.
    실패 시 빈 목록(갭 인터뷰를 막지 않음).
    """
    try:
        from core.benchmark import evaluate as _bm_eval, gap_questions as _bm_questions
        from core.rubric_scorer import extract_features as _extract
        from services.session_store import load_benchmark_cache as _load_c, save_benchmark_cache as _save_c
        text = "\n\n".join(f"## {r.section_title}\n{r.display_content()}" for r in framework_results)
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        cache = _load_c(session_id, key="benchmark_cache_framework")
        if cache and cache.get("hash") == h:
            features = cache["features"]
        else:
            features = _extract(text)
            if features is None:
                return []
            _save_c(session_id, h, features, key="benchmark_cache_framework")
        return _bm_questions(_bm_eval(features, program_code), form.sections)
    except Exception as e:  # noqa: BLE001
        logger.warning("[benchmark gap questions 실패] %s/%s: %s", session_id, program_code, e)
        return []


@router.get("/forms/{program_code}/gap_questions")
async def get_gap_questions(program_code: str, session_id: str | None = None):
    """양식 변환 전 갭 보완 인터뷰 질문 조회.

    session_id가 주어지고 초안이 있으면 방안 A 필터를 적용해 '초안이 이미 커버하는'
    질문은 제외한다(초안↔양식 매핑 재사용, LLM 호출 순증 0). session_id 없거나 초안·
    분석 실패 시 고정 5문항 전체를 반환(안전 폴백).
    """
    try:
        form = load_form(program_code)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Form not found")
    except Exception as e:  # noqa: BLE001
        logger.error("[gap_questions 로드 실패] %s: %s", program_code, e)
        raise HTTPException(status_code=500, detail="양식 로드 실패")

    questions = form.gap_questions
    if session_id:
        framework_results = load_framework_draft(session_id)
        if framework_results:
            try:
                draft_hash = compute_draft_hash(framework_results)
                mapping = await run_in_threadpool(
                    _get_form_mapping, session_id, framework_results, form, draft_hash
                )
                questions = filter_gap_questions(form.gap_questions, mapping)
            except Exception as e:  # noqa: BLE001 — 필터 실패는 변환을 막지 않음(전체 질문 반환)
                logger.warning("[gap_questions 필터 실패] %s/%s: %s", session_id, program_code, e)
            extra = await run_in_threadpool(_benchmark_gap_questions, session_id, program_code, framework_results, form)
            questions = list(questions) + extra
    return {"program_code": program_code, "questions": questions}


@router.post("/sessions/{session_id}/convert_to_form")
async def convert_to_form_endpoint(session_id: str, body: ConvertToFormRequest):
    """양식 변환 — 프레임워크 초안을 선택한 양식 섹션 구조로 변환 (SSE 스트리밍)."""
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not is_unlocked(session_id):
        raise HTTPException(status_code=403, detail="결제 후 이용할 수 있습니다.")

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
            # v3 파이프라인: (1-a) 초안 분석(캐시) → (1-b) 양식 매핑(캐시) → 소스별 변환
            # 매핑·분석은 갭 질문 조회 시 이미 캐시됐으면 재사용 → LLM 호출 순증 없음
            _emit("stage", {"stage": "analyzing"})
            draft_hash = compute_draft_hash(framework_results)
            analysis = _get_draft_analysis(session_id, framework_results, draft_hash)
            _emit("stage", {"stage": "mapping"})
            mapping = _get_form_mapping(session_id, framework_results, form, draft_hash)
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
                extra_gap_questions=_benchmark_gap_questions(session_id, body.program_code, framework_results, form),
                benchmark_note=benchmark_insight_note(body.program_code),
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
    if not is_unlocked(session_id):
        raise HTTPException(status_code=403, detail="결제 후 이용할 수 있습니다.")

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


# 무료 열람 게이트: PSST 중 Problem·Solution만 공개, Scale-up·Team은 결제 후 열람.
# CSS 블러가 아니라 서버에서 원문 자체를 내려주지 않는다(F12 열람 차단).
_FREE_CATEGORIES = {"Problem", "Solution"}
_LOCKED_PREVIEW_CHARS = 120


def _locked_section_ids() -> set[str]:
    """미결제 세션에서 잠글 섹션 ID (Scale-up·Team 카테고리)."""
    return {s["id"] for s in FRAMEWORK_SECTIONS if s.get("category") not in _FREE_CATEGORIES}


_SECTION_CATEGORY: dict[str, str] = {s["id"]: s.get("category", "") for s in FRAMEWORK_SECTIONS}


@router.get("/sessions/{session_id}/framework")
def get_framework_draft(session_id: str):
    """저장된 프레임워크 초안 반환.

    미결제(unlocked=false) 세션은 Scale-up·Team 섹션의 원문을 제거하고
    preview(앞 120자)+locked=true만 내려준다 — 결제 유도 블러 화면용.
    """
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    results = load_framework_draft(session_id)
    if not results:
        raise HTTPException(status_code=404, detail="프레임워크 초안이 없습니다. 먼저 생성해주세요.")
    overall = calculate_overall_completion(results)
    unlocked = is_unlocked(session_id)
    locked_ids = set() if unlocked else _locked_section_ids()

    return {
        "overall_completion": overall,
        "unlocked": unlocked,
        "sections": [
            _framework_section_payload(r, r.section_id in locked_ids) for r in results
        ],
    }


def _framework_section_payload(r, locked: bool) -> dict:
    """프레임워크 섹션 1개의 API 응답 형태. 목록 조회와 단건 재생성이 공유한다."""
    base = {
        "section_id": r.section_id,
        "section_title": r.section_title,
        "confidence_level": r.confidence_level,
        "completion_score": r.effective_completion_score(),
        "effective_completion_score": r.effective_completion_score(),
        "resolved_memo_count": r.resolved_memo_count(),
        "category": _SECTION_CATEGORY.get(r.section_id, ""),
        "truncated": bool(r.llm_meta.get("truncated", False)),
        "locked": locked,
    }
    if locked:
        # 원문·세그먼트·피드백 전부 미포함 — 티저만
        return {
            **base,
            "content": "",
            "preview": r.display_content()[:_LOCKED_PREVIEW_CHARS],
            "reasoning": "",
            "used_answer_ids": [],
            "missing_info": [],
            "user_edited_content": None,
            "rubric_check": {},
            "llm_meta": {},
            "completion_reasoning": "",
            "content_segments": [],
            "inline_suggestions": [],
        }
    return {
        **base,
        "content": r.display_content(),
        "preview": "",
        "reasoning": r.reasoning,
        "used_answer_ids": r.used_answer_ids,
        "missing_info": r.missing_info,
        "user_edited_content": r.user_edited_content,
        "rubric_check": r.rubric_check,
        "llm_meta": r.llm_meta,
        "completion_reasoning": r.completion_reasoning,
        "content_segments": [
            {"text": s.text, "source": s.source, "source_qids": s.source_qids}
            for s in r.content_segments
        ],
        "inline_suggestions": [
            {"anchor_text": s.anchor_text, "note": s.note, "severity": s.severity, "response": s.response}
            for s in r.inline_suggestions
        ],
    }


@router.post("/sessions/{session_id}/framework/regenerate/{section_id}")
async def regenerate_empty_framework_section(session_id: str, section_id: str):
    """생성 실패(빈 내용) 섹션 단건 재생성 — 최후 복구 경로.

    내용이 있는 섹션은 400으로 거부한다. 실패 복구 전용이므로 사용량(usage)을 차감하지
    않으며, 정상 섹션을 무한 재생성하는 우회 경로로 쓰일 수 없다.
    """
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    results = load_framework_draft(session_id)
    if not results:
        raise HTTPException(status_code=404, detail="프레임워크 초안이 없습니다.")

    idx = next((i for i, r in enumerate(results) if r.section_id == section_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail=f"Section '{section_id}' not found")
    if (results[idx].content or "").strip():
        raise HTTPException(status_code=400, detail="생성에 실패한 섹션만 재생성할 수 있습니다.")

    sec = next((s for s in FRAMEWORK_SECTIONS if s["id"] == section_id), None)
    if sec is None:
        raise HTTPException(status_code=404, detail=f"Section '{section_id}' not in framework")

    try:
        questions = load_initial_questions(_INITIAL_Q_PATH)
        skills = load_skills(_SKILLS_DIR) if _SKILLS_DIR.exists() else []
    except Exception as e:
        logger.error("[단건 재생성] 초기화 실패 %s: %s", section_id, e)
        raise HTTPException(status_code=500, detail="재생성 초기화에 실패했습니다.")

    company_context = session.company_context

    def _regen():
        return generate_framework_section(
            sec, questions, session.answers, skills, company_context,
            prior_context=build_parallel_prior_note(sec),
            timeout_s=_AUTO_RETRY_TIMEOUT_S,
            retries=0,
        )

    # SSE로 응답하는 이유: 재생성은 40~120초가 걸리는데, 그동안 아무 바이트도 흐르지 않는
    # 단일 POST는 Next.js·Railway 프록시가 끊어버린다(실측: 프록시 경유 시 37초에 500).
    # 15초마다 keepalive를 흘려보내 연결을 유지하고, 마지막에 done/error 이벤트를 보낸다.
    async def event_generator():
        loop = asyncio.get_event_loop()
        regenerated: SectionResult | None = None

        async for kind, payload in _run_with_keepalive(
            loop, _regen, _AUTO_RETRY_TIMEOUT_S + 15.0
        ):
            if kind == "ping":
                yield _KEEPALIVE
            elif kind == "done":
                regenerated = payload
            elif kind == "error":
                logger.error("[단건 재생성 실패] %s: %s", section_id, payload)
                yield _sse("error", {"message": "재생성 중 오류가 발생했습니다. 다시 시도해주세요."})
                return
            elif kind == "timeout":
                logger.error("[단건 재생성 타임아웃] %s", section_id)
                yield _sse("error", {"message": "재생성이 시간 내에 완료되지 않았습니다. 다시 시도해주세요."})
                return

        if regenerated is None or not (regenerated.content or "").strip():
            yield _sse("error", {"message": "재생성 결과가 비어 있습니다. 다시 시도해주세요."})
            return

        results[idx] = regenerated
        save_framework_draft(session_id, results)
        logger.info("[단건 재생성] %s 복구 성공", section_id)

        # 잠금 상태 반영 — 미결제 세션의 잠긴 섹션은 원문 대신 티저만 내려간다
        locked_ids = set() if is_unlocked(session_id) else _locked_section_ids()
        yield _sse("done", _framework_section_payload(regenerated, section_id in locked_ids))

    return StreamingResponse(event_generator(), media_type="text/event-stream")
