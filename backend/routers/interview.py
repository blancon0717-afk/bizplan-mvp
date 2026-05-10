import json
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services.session_store import (
    get_session,
    save_company_context,
    update_answer,
)
from core.context_extraction import CONTEXT_FIELDS, extract_company_context
from core.interview import load_initial_questions

logger = logging.getLogger(__name__)
router = APIRouter(tags=["interview"])

_INITIAL_Q_PATH = Path("data/interview/initial_questions.json")


def _all_initial_answered(session) -> bool:
    """초기 인터뷰 10문항 전체에 비어있지 않은 답변이 존재하는지."""
    try:
        questions = load_initial_questions(_INITIAL_Q_PATH)
    except Exception:
        return False
    for q in questions:
        a = session.answers.get(q.qid)
        if a is None or not (a.text or "").strip():
            return False
    return True


def _maybe_trigger_extraction(session_id: str) -> dict | None:
    """모든 초기 답변이 채워졌고 아직 컨텍스트가 없으면 추출 1회 실행."""
    session = get_session(session_id)
    if session is None:
        return None
    if session.company_context:
        return None
    if not _all_initial_answered(session):
        return None
    try:
        questions = load_initial_questions(_INITIAL_Q_PATH)
    except Exception as e:
        logger.error(f"_maybe_trigger_extraction: 질문 로드 실패: {e}")
        return None
    logger.info(f"[{session_id}] 인터뷰 완료 감지 → extract_company_context 실행")
    context = extract_company_context(questions, session.answers)
    save_company_context(session_id, context)
    return context


@router.get("/interview/questions")
def get_questions():
    if not _INITIAL_Q_PATH.exists():
        raise HTTPException(status_code=500, detail="Initial questions file not found")
    raw = json.loads(_INITIAL_Q_PATH.read_text(encoding="utf-8"))
    questions = [
        {
            "qid": item["qid"],
            "section": item["section"],
            "category": item["category"],
            "branch": item.get("branch", "공통"),
            "text": item["text"],
            "hint": item.get("hint", ""),
            "tags": item.get("tags", []),
        }
        for item in raw
    ]
    return {"questions": questions}


class AnswerBody(BaseModel):
    text: str


@router.put("/sessions/{session_id}/answers/{qid}")
def save_answer(session_id: str, qid: str, body: AnswerBody):
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    answer = update_answer(session_id, qid, body.text)
    extracted = _maybe_trigger_extraction(session_id)
    return {
        "qid": answer.qid,
        "text": answer.text,
        "updated_at": answer.updated_at,
        "context_extracted": bool(extracted),
    }


class BulkAnswerBody(BaseModel):
    answers: dict[str, str]


@router.put("/sessions/{session_id}/answers")
def save_answers_bulk(session_id: str, body: BulkAnswerBody):
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    saved = {}
    for qid, text in body.answers.items():
        answer = update_answer(session_id, qid, text)
        saved[qid] = {"qid": answer.qid, "text": answer.text, "updated_at": answer.updated_at}
    extracted = _maybe_trigger_extraction(session_id)
    return {
        "updated": len(saved),
        "answers": saved,
        "context_extracted": bool(extracted),
    }


@router.post("/sessions/{session_id}/extract-context")
def extract_context_endpoint(session_id: str):
    """전처리 수동 트리거 — 답변 갱신 후 강제 재추출 시 사용."""
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        questions = load_initial_questions(_INITIAL_Q_PATH)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"질문 로드 실패: {e}")
    context = extract_company_context(questions, session.answers)
    save_company_context(session_id, context)
    meta = context.get("_meta", {}) if isinstance(context, dict) else {}
    filled = sum(1 for f in CONTEXT_FIELDS if (context.get(f) or "").strip())
    return {
        "session_id": session_id,
        "filled": filled,
        "total": len(CONTEXT_FIELDS),
        "input_tokens": meta.get("input_tokens"),
        "output_tokens": meta.get("output_tokens"),
        "duration_ms": meta.get("duration_ms"),
        "context": {f: context.get(f, "") for f in CONTEXT_FIELDS},
    }


@router.get("/sessions/{session_id}/context")
def get_context_endpoint(session_id: str):
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.company_context:
        return {"session_id": session_id, "extracted": False, "context": None}
    return {
        "session_id": session_id,
        "extracted": True,
        "context": session.company_context,
    }
