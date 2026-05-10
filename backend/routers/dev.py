"""개발 전용 헬퍼 엔드포인트.

주의: 프로덕션 배포 시 backend/main.py 에서 이 라우터 등록을 제거하세요.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.session_store import create_session, update_answer

router = APIRouter(prefix="/dev", tags=["dev"])

_TEST_DATA_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "test" / "eporei_answers.json"
)


class LoadTestSessionBody(BaseModel):
    program_code: str = "changjungdae"


@router.post("/load-test-session", status_code=201)
def load_test_session(body: LoadTestSessionBody):
    """이포에이 테스트 답변을 새 세션에 주입하고 세션 ID를 반환합니다."""
    if not _TEST_DATA_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="테스트 데이터 파일을 찾을 수 없습니다 (data/test/eporei_answers.json)",
        )

    answers: dict[str, str] = json.loads(_TEST_DATA_PATH.read_text(encoding="utf-8"))

    session_id = str(uuid.uuid4())[:8]
    create_session(session_id, body.program_code)

    loaded = 0
    for qid, text in answers.items():
        if text:
            update_answer(session_id, qid, text)
            loaded += 1

    return {
        "session_id": session_id,
        "program_code": body.program_code,
        "answers_loaded": loaded,
    }
