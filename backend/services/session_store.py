"""세션 및 생성 결과 파일 기반 저장소."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from core.generation import ContentSegment, InlineSuggestion, SectionResult
from core.interview import Answer, Session, load_session

# 계정에 귀속된 문서는 영구 보관되므로 배포 시 초기화되지 않는 경로를 써야 한다.
# Railway에서는 계정 DB(DB_PATH)와 같은 Volume 하위를 지정할 것 — 예: SESSIONS_DIR=/data/sessions
_SESSIONS_DIR = Path(os.getenv("SESSIONS_DIR", "data/sessions"))

# 세션 ID에서 파생된 부속 파일 — 문서 목록 조회 시 세션 본체와 구분한다.
_DERIVED_SUFFIXES = (
    "_results.json",
    "_framework.json",
    "_draft_analysis.json",
    "_form_mapping.json",
)


def _ensure_dir() -> None:
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _raw_path(session_id: str) -> Path:
    return _SESSIONS_DIR / f"{session_id}.json"


def _read_raw(session_id: str) -> Optional[dict]:
    """세션 JSON 원본을 dict로 읽는다. 없거나 손상 시 None."""
    path = _raw_path(session_id)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — 손상 파일은 없는 것으로 취급(로그만)
        logger.error("세션 JSON 손상 %s: %s", session_id, e)
        return None
    return raw if isinstance(raw, dict) else None


def _write_raw(session_id: str, raw: dict) -> None:
    _ensure_dir()
    _raw_path(session_id).write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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
    """답변 1건 저장.

    Session.to_json은 4개 필드(session_id·program_code·answers·company_context)만
    직렬화하므로, save_session으로 저장하면 user_id·created_at·unlocked·usage_count·
    lead_email이 통째로 유실된다. 그래서 raw JSON의 해당 키만 패치한다.
    """
    raw = _read_raw(session_id)
    if raw is None:
        return None
    answer = Answer(qid=qid, text=text, updated_at=datetime.now(timezone.utc).isoformat())
    answers = raw.get("answers")
    if not isinstance(answers, dict):
        answers = {}
    answers[qid] = asdict(answer)
    raw["answers"] = answers
    _write_raw(session_id, raw)
    return answer


def save_company_context(session_id: str, context: dict) -> Optional[Session]:
    """전처리된 기업 컨텍스트를 세션에 저장 (부가 필드 보존 — update_answer 주석 참조)."""
    raw = _read_raw(session_id)
    if raw is None:
        return None
    raw["company_context"] = context
    _write_raw(session_id, raw)
    return get_session(session_id)


def update_program_code(session_id: str, program_code: str) -> bool:
    """세션의 program_code만 갱신 (양식 변환 성공 시 호출).

    save_session은 dataclass 필드만 직렬화해 created_at·usage_count 등
    부가 필드가 유실되므로, raw JSON을 읽어 해당 키만 패치한다.
    """
    path = _SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["program_code"] = program_code
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:  # noqa: BLE001 — 갱신 실패는 치명적이지 않음(로그만)
        logger.error("update_program_code 실패 %s: %s", session_id, e)
        return False


def get_session_owner(session_id: str) -> Optional[int]:
    """문서 소유자 user_id. 익명 문서·없는 문서는 None."""
    raw = _read_raw(session_id)
    if raw is None:
        return None
    owner = raw.get("user_id")
    return owner if isinstance(owner, int) else None


def set_session_owner(session_id: str, user_id: int) -> bool:
    """문서를 계정에 귀속. 이미 다른 계정 소유면 False(가로채기 방지).

    같은 소유자로 다시 호출하는 것은 성공으로 취급한다(로그인마다 호출돼도 무해).
    """
    raw = _read_raw(session_id)
    if raw is None:
        return False
    owner = raw.get("user_id")
    if isinstance(owner, int) and owner != user_id:
        logger.warning("[문서 귀속 거부] session=%s owner=%s 요청=%s", session_id, owner, user_id)
        return False
    raw["user_id"] = user_id
    _write_raw(session_id, raw)
    return True


_TITLE_MAX = 30


def _document_title(raw: dict) -> str:
    """목록 표시용 제목 — 첫 인터뷰 답변의 앞부분. 답변 전이면 대체 문구."""
    answers = raw.get("answers")
    if isinstance(answers, dict):
        for answer in answers.values():
            text = " ".join((answer or {}).get("text", "").split())
            if text:
                return text[:_TITLE_MAX] + ("…" if len(text) > _TITLE_MAX else "")
    return "제목 없는 문서"


def list_sessions_by_owner(user_id: int) -> list[dict]:
    """계정이 소유한 문서 목록을 최신순으로 반환 — 목록 표시에 필요한 요약만.

    ponytail: 세션 디렉토리 전수 스캔. 세션이 수천 개를 넘으면 소유자 인덱스 파일로 교체.
    """
    if not _SESSIONS_DIR.exists():
        return []

    documents: list[dict] = []
    for path in _SESSIONS_DIR.glob("*.json"):
        if path.name.endswith(_DERIVED_SUFFIXES):
            continue
        raw = _read_raw(path.stem)
        if raw is None or raw.get("user_id") != user_id:
            continue
        session_id = raw.get("session_id") or path.stem
        documents.append(
            {
                "session_id": session_id,
                "title": _document_title(raw),
                "program_code": raw.get("program_code") or "none",
                "created_at": raw.get("created_at") or "",
                "unlocked": bool(raw.get("unlocked")),
                "has_framework": (_SESSIONS_DIR / f"{session_id}_framework.json").exists(),
                "has_results": (_SESSIONS_DIR / f"{session_id}_results.json").exists(),
            }
        )

    documents.sort(key=lambda d: d["created_at"], reverse=True)
    return documents


def cleanup_old_sessions(max_age_days: float = 3.0) -> int:
    """3일(기본값) 초과 세션 파일을 삭제하고 삭제 건수를 반환.

    created_at 필드가 있으면 그 기준, 없으면 파일 mtime 기준.
    세션 파일 삭제 시 대응하는 _results.json도 함께 삭제.
    단, 계정에 귀속된 문서(user_id)는 '내 문서보기'에서 계속 열람해야 하므로 삭제하지 않는다.
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
            if raw.get("user_id") is not None:
                continue  # 계정 귀속 문서는 보관
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


def save_benchmark_cache(session_id: str, content_hash: str, features: dict) -> None:
    """벤치마크용 추출 피처를 본문 해시와 함께 캐시 (본문 불변 시 재추출 방지)."""
    raw = _read_raw(session_id)
    if raw is None:
        return
    raw["benchmark_cache"] = {
        "hash": content_hash,
        "features": features,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    _write_raw(session_id, raw)


def load_benchmark_cache(session_id: str) -> Optional[dict]:
    raw = _read_raw(session_id)
    if raw is None:
        return None
    cache = raw.get("benchmark_cache")
    return cache if isinstance(cache, dict) and cache.get("features") else None


def load_action_plan(session_id: str) -> Optional[str]:
    path = _SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw.get("action_plan") or None


def save_lead(session_id: str, email: str) -> None:
    """DOCX 다운로드 전 수집한 리드 이메일 저장.

    세션 JSON에 lead_email 키를 패치하고, 세션이 3일 후 정리돼도 리드가
    보존되도록 data/leads.jsonl에 append한다.
    """
    _ensure_dir()
    path = _SESSIONS_DIR / f"{session_id}.json"
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["lead_email"] = email
            path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001 — 세션 패치 실패해도 leads.jsonl에는 기록
            logger.error("save_lead 세션 패치 실패 %s: %s", session_id, e)
    leads_path = Path("data/leads.jsonl")
    leads_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "session_id": session_id,
        "email": email,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    with leads_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def is_unlocked(session_id: str) -> bool:
    """결제(언락 코드) 완료 여부. 세션 없음/손상 시 False."""
    path = _SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return bool(raw.get("unlocked"))
    except Exception:  # noqa: BLE001 — 판독 불가면 잠금 상태로 취급
        return False


def set_unlocked(session_id: str) -> bool:
    """언락 코드 검증 성공 시 호출 — unlocked=true 저장."""
    path = _SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return False
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["unlocked"] = True
    raw["unlocked_at"] = datetime.now(timezone.utc).isoformat()
    raw.pop("unlock_failures", None)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def record_unlock_failure(session_id: str, max_failures: int, lock_minutes: int) -> tuple[int, Optional[str]]:
    """언락 코드 오입력 기록. (누적 실패 수, 잠금 해제 시각 ISO)을 반환.

    max_failures 도달 시 lock_minutes 동안 시도를 차단한다(무차별 대입 방지).
    """
    path = _SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return 0, None
    raw = json.loads(path.read_text(encoding="utf-8"))
    failures = raw.get("unlock_failures", {"count": 0, "locked_until": None})
    failures["count"] = failures.get("count", 0) + 1
    if failures["count"] >= max_failures:
        locked_until = datetime.now(timezone.utc) + timedelta(minutes=lock_minutes)
        failures["locked_until"] = locked_until.isoformat()
        failures["count"] = 0  # 잠금 만료 후 재시도 카운트는 0부터
    raw["unlock_failures"] = failures
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return failures["count"], failures.get("locked_until")


def get_unlock_locked_until(session_id: str) -> Optional[datetime]:
    """무차별 대입 잠금 상태면 잠금 해제 시각을 반환, 아니면 None."""
    path = _SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        locked_str = (raw.get("unlock_failures") or {}).get("locked_until")
        if not locked_str:
            return None
        locked_until = datetime.fromisoformat(locked_str)
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        return locked_until if locked_until > datetime.now(timezone.utc) else None
    except Exception:  # noqa: BLE001
        return None


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


def save_draft_analysis(session_id: str, analysis: dict) -> None:
    """(변환 v3) 초안 분석 결과 캐시 저장 — {sid}_draft_analysis.json.

    analysis에는 compute_draft_hash 결과를 "_draft_hash" 키로 포함시켜
    초안 수정 시 캐시 무효화 판정에 사용한다.
    """
    _ensure_dir()
    path = _SESSIONS_DIR / f"{session_id}_draft_analysis.json"
    path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")


def load_draft_analysis(session_id: str) -> Optional[dict]:
    """(변환 v3) 초안 분석 캐시 로드. 없거나 손상 시 None."""
    path = _SESSIONS_DIR / f"{session_id}_draft_analysis.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — 손상 캐시는 재분석으로 복구
        logger.warning("draft_analysis 캐시 로드 실패 %s: %s", session_id, e)
        return None


def save_form_mapping(session_id: str, program_code: str, draft_hash: str, mapping: dict) -> None:
    """(변환 v3) 양식별 초안→섹션 매핑 캐시 — {sid}_form_mapping.json.

    갭 인터뷰 질문 필터와 변환이 같은 매핑을 공유하도록(LLM 호출 순증 0회) 저장한다.
    program_code별로 저장하며 draft_hash로 초안 수정 시 무효화한다.
    """
    _ensure_dir()
    path = _SESSIONS_DIR / f"{session_id}_form_mapping.json"
    store = {}
    if path.exists():
        try:
            store = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 손상 캐시는 덮어쓴다
            store = {}
    store[program_code] = {"_draft_hash": draft_hash, "mapping": mapping}
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def load_form_mapping(session_id: str, program_code: str, draft_hash: str) -> Optional[dict]:
    """(변환 v3) 양식 매핑 캐시 로드. draft_hash 불일치·손상·미존재 시 None."""
    path = _SESSIONS_DIR / f"{session_id}_form_mapping.json"
    if not path.exists():
        return None
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("form_mapping 캐시 로드 실패 %s: %s", session_id, e)
        return None
    entry = store.get(program_code)
    if not entry or entry.get("_draft_hash") != draft_hash:
        return None
    return entry.get("mapping")


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


def demo() -> None:
    """소유권·문서 목록·보관 정책 자체 점검.

    실행(프로젝트 루트에서):
      python -c "import sys;sys.path[:0]=['backend','.'];from services.session_store import demo;demo()"
    """
    global _SESSIONS_DIR
    import tempfile

    original_dir = _SESSIONS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        _SESSIONS_DIR = Path(tmp)
        try:
            create_session("anon", "none")
            create_session("mine", "none")

            # 소유권: 귀속 → 가로채기 거부 → 같은 소유자는 멱등
            assert get_session_owner("mine") is None
            assert set_session_owner("mine", 7) is True
            assert get_session_owner("mine") == 7
            assert set_session_owner("mine", 8) is False, "남의 문서를 가로챌 수 없어야 한다"
            assert get_session_owner("mine") == 7
            assert set_session_owner("mine", 7) is True, "같은 소유자면 멱등"
            assert set_session_owner("없는문서", 7) is False

            # 답변 저장이 소유권·결제 언락을 지우지 않아야 한다 (save_session 전체 덮어쓰기 방지)
            set_unlocked("mine")
            update_answer("mine", "q1", "  탄소포집 기술로   산업용 CO2를 줄이는 서비스입니다  ")
            save_company_context("mine", {"지역": "수도권"})
            assert get_session_owner("mine") == 7, "답변 저장 후 소유권이 유지돼야 한다"
            assert is_unlocked("mine") is True, "답변 저장 후 결제 언락이 유지돼야 한다"
            assert get_session("mine").company_context == {"지역": "수도권"}

            # 목록: 내 문서만, 제목은 첫 답변 앞부분(공백 정규화)
            documents = list_sessions_by_owner(7)
            assert [d["session_id"] for d in documents] == ["mine"]
            assert documents[0]["title"] == "탄소포집 기술로 산업용 CO2를 줄이는 서비스입니다"
            assert _document_title({"answers": {"q1": {"text": "가" * 40}}}) == "가" * _TITLE_MAX + "…"
            assert _document_title({"answers": {}}) == "제목 없는 문서"
            assert documents[0]["has_framework"] is False
            assert list_sessions_by_owner(8) == [], "남의 문서가 보이면 안 된다"

            # 부속 파일(_framework 등)이 문서로 잡히면 안 된다
            save_framework_draft("mine", [])
            documents = list_sessions_by_owner(7)
            assert len(documents) == 1, "부속 파일이 문서 목록에 섞였다"
            assert documents[0]["has_framework"] is True

            # 보관 정책: 만료시켜도 계정 문서는 남고 익명 문서만 지워진다
            expired = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
            for session_id in ("anon", "mine"):
                raw = _read_raw(session_id)
                raw["created_at"] = expired
                _write_raw(session_id, raw)
            assert cleanup_old_sessions() == 1
            assert not _raw_path("anon").exists(), "만료된 익명 문서는 삭제돼야 한다"
            assert _raw_path("mine").exists(), "계정 문서는 만료돼도 보관돼야 한다"
        finally:
            _SESSIONS_DIR = original_dir

    print("session_store demo OK")
