import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.services.session_store import create_session, get_session, load_results

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
