import json
import logging
from pathlib import Path
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from services.session_store import (
    get_session,
    save_company_context,
    update_answer,
)
from core.context_extraction import (
    CONTEXT_FIELDS,
    extract_company_context,
    extract_text_from_pdf,
    map_pdf_to_answers,
)
from core.interview import load_initial_questions

# 업로드 PDF 최대 크기(10MB). 메모리에서만 처리하고 디스크 저장하지 않는다.
_MAX_PDF_BYTES = 10 * 1024 * 1024

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


@router.post("/sessions/{session_id}/upload_plan")
async def upload_plan(session_id: str, file: UploadFile = File(...)):
    """기존 사업계획서 PDF 업로드 → 인터뷰 답변 사전 채움.

    - 텍스트 추출 가능한 PDF만 지원. 스캔본 등 추출 불가 시 {ok:false, reason:"no_text"}
      (프론트가 일반 인터뷰로 유도).
    - 추출 텍스트를 LLM으로 초기 10문항에 매핑, 근거 있는 질문만 답변 저장.
    - 반환: filled_qids(채워진 질문), empty_qids(보완 인터뷰 대상).
    """
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # 확장자 화이트리스트(.pdf만). 파일명은 저장하지 않고 검증에만 사용.
    filename = (file.filename or "").lower()
    if not filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF 파일만 업로드할 수 있습니다.")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")
    if len(pdf_bytes) > _MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="파일이 너무 큽니다. (최대 10MB)")

    text = extract_text_from_pdf(pdf_bytes)
    if not text.strip():
        # 스캔 이미지본 등 — 프론트가 일반 인터뷰로 유도
        logger.info(f"[{session_id}] PDF 텍스트 추출 실패(no_text) — 일반 인터뷰 유도")
        return {"ok": False, "reason": "no_text"}

    try:
        questions = load_initial_questions(_INITIAL_Q_PATH)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"질문 로드 실패: {e}")

    mapped = map_pdf_to_answers(text, questions)
    for qid, ans_text in mapped.items():
        update_answer(session_id, qid, ans_text)

    # 모든 문항이 PDF로 채워졌다면 컨텍스트 추출까지 자동 트리거(기존 로직 재사용)
    _maybe_trigger_extraction(session_id)

    filled_qids = [q.qid for q in questions if q.qid in mapped]
    empty_qids = [q.qid for q in questions if q.qid not in mapped]
    logger.info(
        f"[{session_id}] PDF 업로드 매핑 완료: filled={len(filled_qids)}/{len(questions)}, chars={len(text)}"
    )
    return {
        "ok": True,
        "filled_qids": filled_qids,
        "empty_qids": empty_qids,
        "filled": len(filled_qids),
        "total": len(questions),
        "text_chars": len(text),
    }
