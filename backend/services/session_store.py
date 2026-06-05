"""세션 및 생성 결과 파일 기반 저장소."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from core.generation import ContentSegment, InlineSuggestion, SectionResult
from core.interview import Answer, Session, load_session, save_session

_SESSIONS_DIR = Path("data/sessions")


def _ensure_dir() -> None:
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def create_session(session_id: str, program_code: str) -> Session:
    _ensure_dir()
    session = Session(session_id=session_id, program_code=program_code)
    data = json.loads(session.to_json())
    data["created_at"] = datetime.now(timezone.utc).isoformat()
    data["usage_count"] = {
        "generate": 0,
        "feedback": 0,
        "memo": 0,
        "regenerate": 0,
        "edit": 0,
        "action_plan": 0,
        "regenerate_all": 0,
    }
    (_SESSIONS_DIR / f"{session_id}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return session


def get_session(session_id: str) -> Optional[Session]:
    return load_session(session_id, _SESSIONS_DIR)


def update_answer(session_id: str, qid: str, text: str) -> Optional[Answer]:
    from datetime import datetime, timezone
    session = get_session(session_id)
    if session is None:
        return None
    answer = Answer(qid=qid, text=text, updated_at=datetime.now(timezone.utc).isoformat())
    session.answers[qid] = answer
    save_session(session, _SESSIONS_DIR)
    return answer


def save_company_context(session_id: str, context: dict) -> Optional[Session]:
    """전처리된 기업 컨텍스트를 세션에 저장."""
    session = get_session(session_id)
    if session is None:
        return None
    session.company_context = context
    save_session(session, _SESSIONS_DIR)
    return session


def cleanup_old_sessions(max_age_days: float = 3.0) -> int:
    """3일(기본값) 초과 세션 파일을 삭제하고 삭제 건수를 반환.

    created_at 필드가 있으면 그 기준, 없으면 파일 mtime 기준.
    세션 파일 삭제 시 대응하는 _results.json도 함께 삭제.
    """
    if not _SESSIONS_DIR.exists():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    deleted = 0

    for session_file in _SESSIONS_DIR.glob("*.json"):
        if session_file.name.endswith("_results.json"):
            continue
        try:
            raw = json.loads(session_file.read_text(encoding="utf-8"))
            created_str = raw.get("created_at")
            if created_str:
                created_at = datetime.fromisoformat(created_str)
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
            else:
                mtime = session_file.stat().st_mtime
                created_at = datetime.fromtimestamp(mtime, tz=timezone.utc)
        except Exception:
            continue

        if created_at < cutoff:
            session_id = session_file.stem
            session_file.unlink(missing_ok=True)
            results_file = _SESSIONS_DIR / f"{session_id}_results.json"
            results_file.unlink(missing_ok=True)
            deleted += 1

    if deleted:
        logger.info("[세션 정리] %d개 세션 삭제 (기준: %d일 초과)", deleted, int(max_age_days))
    return deleted


def get_usage_count(session_id: str, feature: str) -> int:
    path = _SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return 0
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw.get("usage_count", {}).get(feature, 0)


def increment_usage(session_id: str, feature: str) -> int:
    path = _SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return 0
    raw = json.loads(path.read_text(encoding="utf-8"))
    usage = raw.get("usage_count", {})
    usage[feature] = usage.get(feature, 0) + 1
    raw["usage_count"] = usage
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return usage[feature]


def save_action_plan(session_id: str, text: str) -> None:
    path = _SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["action_plan"] = text
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")


def load_action_plan(session_id: str) -> Optional[str]:
    path = _SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw.get("action_plan") or None


def save_results(session_id: str, results: list[SectionResult]) -> None:
    _ensure_dir()
    path = _SESSIONS_DIR / f"{session_id}_results.json"
    data = [asdict(r) for r in results]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_results(session_id: str) -> Optional[list[SectionResult]]:
    path = _SESSIONS_DIR / f"{session_id}_results.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [_dict_to_result(d) for d in raw]


def save_framework_draft(session_id: str, results: list[SectionResult]) -> None:
    """프레임워크 초안(양식 무관)을 {session_id}_framework.json에 저장."""
    _ensure_dir()
    path = _SESSIONS_DIR / f"{session_id}_framework.json"
    data = [asdict(r) for r in results]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_framework_draft(session_id: str) -> Optional[list[SectionResult]]:
    """저장된 프레임워크 초안을 로드. 없으면 None 반환."""
    path = _SESSIONS_DIR / f"{session_id}_framework.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [_dict_to_result(d) for d in raw]


def _dict_to_result(d: dict) -> SectionResult:
    suggestions = [
        InlineSuggestion(**s) for s in d.get("inline_suggestions", [])
    ]
    segments = [
        ContentSegment(**s) for s in d.get("content_segments", [])
    ]
    return SectionResult(
        section_id=d["section_id"],
        section_title=d["section_title"],
        content=d.get("content", ""),
        confidence_level=d.get("confidence_level", "red"),
        reasoning=d.get("reasoning", ""),
        used_answer_ids=d.get("used_answer_ids", []),
        missing_info=d.get("missing_info", []),
        inline_suggestions=suggestions,
        content_segments=segments,
        user_edited_content=d.get("user_edited_content"),
        rubric_check=d.get("rubric_check", {}),
        llm_meta=d.get("llm_meta", {}),
        completion_score=d.get("completion_score", 0),
        completion_reasoning=d.get("completion_reasoning", ""),
    )
