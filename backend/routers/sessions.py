import re
import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from services.session_store import create_session, get_session, load_results, save_lead

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
    }
