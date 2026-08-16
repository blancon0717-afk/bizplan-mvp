import hashlib
import hmac
import logging
import os
import re
import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from services.session_store import (
    create_session,
    get_session,
    get_unlock_locked_until,
    is_unlocked,
    load_results,
    record_unlock_failure,
    save_lead,
    set_unlocked,
)

logger = logging.getLogger(__name__)

# 언락 코드 무차별 대입 방지: 5회 실패 → 10분 잠금 (8자리 코드 + 잠금이면 실질 대입 불가)
_UNLOCK_MAX_FAILURES = 5
_UNLOCK_LOCK_MINUTES = 10


def expected_unlock_code(session_id: str) -> str:
    """세션별 언락 코드 = HMAC-SHA256(UNLOCK_SECRET, session_id) 앞 8자리(대문자).

    별도 저장 없이 세션 ID에서 유도 — 운영자는 scripts/issue_unlock_code.py로 발급.
    """
    secret = os.getenv("UNLOCK_SECRET", "")
    if not secret:
        raise RuntimeError("UNLOCK_SECRET not configured")
    digest = hmac.new(secret.encode(), session_id.encode(), hashlib.sha256).hexdigest()
    return digest[:8].upper()

# ponytail: 단순 형식 검증만 — MX 조회 등 실검증은 리드 볼륨이 생기면
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

router = APIRouter(tags=["sessions"])


class CreateSessionBody(BaseModel):
    program_code: str


@router.post("/sessions", status_code=201)
def create_new_session(body: CreateSessionBody):
    session_id = str(uuid.uuid4())[:8]
    session = create_session(session_id, body.program_code)
    return {
        "session_id": session.session_id,
        "program_code": session.program_code,
    }


class LeadBody(BaseModel):
    email: str


@router.post("/sessions/{session_id}/lead")
def submit_lead(session_id: str, body: LeadBody):
    """DOCX 다운로드 전 리드 이메일 수집."""
    email = body.email.strip()
    if len(email) > 254 or not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="올바른 이메일 형식이 아닙니다.")
    if get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    save_lead(session_id, email)
    return {"ok": True}


class UnlockBody(BaseModel):
    code: str


@router.post("/sessions/{session_id}/unlock")
def unlock_session(session_id: str, body: UnlockBody):
    """언락 코드 검증 — 결제(입금) 확인 후 운영자가 발급한 코드로 전문 열람 해제."""
    if get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if is_unlocked(session_id):
        return {"ok": True, "unlocked": True}

    locked_until = get_unlock_locked_until(session_id)
    if locked_until is not None:
        raise HTTPException(
            status_code=429,
            detail="시도 횟수를 초과했습니다. 10분 후 다시 시도해주세요.",
        )

    code = body.code.strip().upper()
    if not re.fullmatch(r"[0-9A-F]{8}", code):
        raise HTTPException(status_code=422, detail="코드는 8자리 영문·숫자입니다.")

    try:
        expected = expected_unlock_code(session_id)
    except RuntimeError:
        logger.error("UNLOCK_SECRET 미설정 — 언락 불가")
        raise HTTPException(status_code=503, detail="잠시 후 다시 시도해주세요.")

    if not hmac.compare_digest(code, expected):
        count, until = record_unlock_failure(session_id, _UNLOCK_MAX_FAILURES, _UNLOCK_LOCK_MINUTES)
        if until:
            raise HTTPException(status_code=429, detail="시도 횟수를 초과했습니다. 10분 후 다시 시도해주세요.")
        remaining = _UNLOCK_MAX_FAILURES - count
        raise HTTPException(status_code=403, detail=f"코드가 올바르지 않습니다. (남은 시도 {remaining}회)")

    set_unlocked(session_id)
    logger.info("[언락] session=%s 전문 열람 해제", session_id)
    return {"ok": True, "unlocked": True}


@router.get("/sessions/{session_id}")
def get_session_info(session_id: str):
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    results = load_results(session_id)
    return {
        "session_id": session.session_id,
        "program_code": session.program_code,
        "answers": {
            qid: {"qid": a.qid, "text": a.text, "updated_at": a.updated_at}
            for qid, a in session.answers.items()
        },
        "has_results": results is not None,
        "unlocked": is_unlocked(session_id),
    }
