"""노션 '서류평가 피드백 모음' 표 → 로컬 JSON 캐시 동기화.

심사위원 피드백 생성은 100% 이 캐시(노션 데이터)에만 기반한다.
피드백을 노션에서 갱신하면 이 스크립트를 1회 실행해 캐시를 새로고침한다.

사용법:
    .venv/Scripts/python.exe scripts/sync_notion_feedback.py

필요 환경변수(.env, 프로젝트 루트):
    NOTION_API_KEY            노션 통합(Integration) 토큰 (ntn_... 또는 secret_...)
    NOTION_FEEDBACK_PAGE_ID   표가 들어있는 노션 페이지 ID (32자리)

출력:
    data/feedback/notion_feedback.json
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
# data/feedback/는 .gitignore 대상 → 배포(Railway) 시 누락되므로 추적되는 data/reference/에 저장
_OUTPUT_PATH = _ROOT / "data" / "reference" / "notion_feedback.json"

_NOTION_API = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"
_TIMEOUT = 30.0

# 노션 속성명 → 표준 키 (속성명에 공백/괄호가 섞여 있어도 매칭되도록 부분일치 사용)
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "기업명": ("기업명",),
    "연도": ("연도",),
    "사업명": ("사업명",),
    "태그": ("태그",),
    "피드백내용": ("피드백 내용", "피드백내용"),
    "피드백평가": ("피드백 평가", "피드백평가"),
}


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _plain(prop: dict) -> str | list[str]:
    """노션 속성 객체 → 평문 텍스트(또는 multi_select는 리스트)."""
    ptype = prop.get("type", "")
    if ptype == "title":
        return "".join(t.get("plain_text", "") for t in prop.get("title", []))
    if ptype == "rich_text":
        return "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))
    if ptype == "select":
        sel = prop.get("select")
        return sel.get("name", "") if sel else ""
    if ptype == "multi_select":
        return [s.get("name", "") for s in prop.get("multi_select", [])]
    if ptype == "number":
        n = prop.get("number")
        return str(n) if n is not None else ""
    if ptype == "people":
        return ", ".join(p.get("name", "") for p in prop.get("people", []))
    if ptype == "date":
        d = prop.get("date") or {}
        return d.get("start", "") or ""
    return ""


def _find_database_id(client: httpx.Client, token: str, page_id: str) -> str:
    """페이지 자식 블록 중 child_database 블록의 ID를 찾아 반환."""
    cursor: str | None = None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        resp = client.get(
            f"{_NOTION_API}/blocks/{page_id}/children",
            headers=_headers(token),
            params=params,
            timeout=_TIMEOUT,
        )
        _raise_for_notion(resp, context="페이지 자식 블록 조회")
        data = resp.json()
        for block in data.get("results", []):
            if block.get("type") == "child_database":
                return block["id"]
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    raise SystemExit(
        "[오류]페이지 안에서 표(데이터베이스)를 찾지 못했습니다.\n"
        "   - NOTION_FEEDBACK_PAGE_ID가 '서류평가 탈락 피드백 모음' 페이지 ID가 맞는지 확인하세요.\n"
        "   - 그 페이지가 통합(Integration)에 '연결'되어 있는지 확인하세요."
    )


def _query_database(client: httpx.Client, token: str, db_id: str) -> list[dict]:
    """데이터베이스 전체 행을 페이지네이션으로 수집."""
    rows: list[dict] = []
    cursor: str | None = None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        resp = client.post(
            f"{_NOTION_API}/databases/{db_id}/query",
            headers=_headers(token),
            json=body,
            timeout=_TIMEOUT,
        )
        _raise_for_notion(resp, context="데이터베이스 행 조회")
        data = resp.json()
        rows.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return rows


def _raise_for_notion(resp: httpx.Response, context: str) -> None:
    """노션 API 오류를 사람이 읽을 수 있는 메시지로 변환."""
    if resp.status_code == 200:
        return
    if resp.status_code == 401:
        raise SystemExit("[오류]인증 실패(401): NOTION_API_KEY가 올바른지 확인하세요.")
    if resp.status_code == 404:
        raise SystemExit(
            "[오류]찾을 수 없음(404): 페이지/DB ID가 틀렸거나, 해당 페이지가 "
            "통합(Integration)에 '연결'되어 있지 않습니다."
        )
    raise SystemExit(f"[오류]노션 API 오류({context}): {resp.status_code} {resp.text[:300]}")


def _extract_record(row: dict) -> dict:
    """노션 행(page 객체) → 표준 피드백 레코드."""
    props = row.get("properties", {})
    # 속성명을 부분일치로 표준 키에 매핑
    record: dict = {}
    for std_key, aliases in _FIELD_ALIASES.items():
        value: str | list[str] = ""
        for prop_name, prop in props.items():
            if any(alias in prop_name for alias in aliases):
                value = _plain(prop)
                break
        record[std_key] = value
    # 태그는 항상 리스트로 정규화
    if isinstance(record.get("태그"), str):
        record["태그"] = [record["태그"]] if record["태그"] else []
    return record


def sync() -> None:
    load_dotenv(_ROOT / ".env")
    token = os.getenv("NOTION_API_KEY", "").strip()
    # 변수명 호환: PAGE_ID(권장) 또는 DB_ID 둘 다 허용 (값은 페이지 ID)
    page_id = (
        os.getenv("NOTION_FEEDBACK_PAGE_ID", "")
        or os.getenv("NOTION_FEEDBACK_DB_ID", "")
    ).strip().replace("-", "")

    if not token:
        raise SystemExit("[오류]NOTION_API_KEY가 .env에 없습니다.")
    if not page_id:
        raise SystemExit("[오류]NOTION_FEEDBACK_PAGE_ID가 .env에 없습니다.")

    with httpx.Client() as client:
        print("· 페이지에서 표(데이터베이스) 찾는 중...")
        db_id = _find_database_id(client, token, page_id)
        print(f"· 데이터베이스 발견: {db_id}")
        print("· 행 수집 중...")
        rows = _query_database(client, token, db_id)

    records = [_extract_record(r) for r in rows]
    # 피드백 내용이 비어 있는 행은 제외
    records = [r for r in records if str(r.get("피드백내용", "")).strip()]

    payload = {
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "page_id": page_id,
        "database_id": db_id,
        "count": len(records),
        "records": records,
    }
    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[완료] 동기화 완료: {len(records)}건 → {_OUTPUT_PATH.relative_to(_ROOT)}")


if __name__ == "__main__":
    # 윈도우 콘솔(cp949)에서 이모지·특수문자 출력 시 인코딩 오류 방지
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        sync()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[오류]예기치 못한 오류: {e}", file=sys.stderr)
        sys.exit(1)
