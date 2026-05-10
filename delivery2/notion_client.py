from __future__ import annotations

import logging
import re
from typing import Any, Iterator

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

log = logging.getLogger(__name__)

NOTION_VERSION = "2022-06-28"
BASE = "https://api.notion.com/v1"

EXCLUDE_EXACT_SAUP = {"IR", "제안서", "회사소개서", "서비스 소개서"}
EXCLUDE_SAUP_WORDS = ("바우처", "인증")

TARGET_TYPES = (
    "예비창업패키지",
    "초기창업패키지",
    "창업도약패키지",
    "청년창업사관학교",
    "창업중심대학-예창",
    "창업중심대학-초창",
    "창업중심대학-창도",
)

_EXCLUDE_FN = re.compile(
    r"발표자료|발표\s*자료|pitch|deck|증빙|증명|참고|양식|동의서|서약서|이력서|"
    r"사업자\s*등록|통장|신분증|명함|참가신청|회사소개|회사\s*소개|계산서|견적서|재무제표|vat",
    re.I,
)
_INCLUDE_FN = re.compile(
    r"사업\s*계획서|사업\s*신청서|연구개발\s*계획서|수행\s*계획서|"
    r"[\[\(](예창|초창|창도|창중|청창사|딥테크|에코|기보|R&D|디딤돌|콘텐츠|초기|글로벌|재도전|TIPS|LIPS|"
    r"스프링|강소공|신사업|창중초창|창중예창|창중창도)[\]\)]|"
    r"창업도약|예비창업|초기창업|청년창업|창업중심대학|창업사관학교|딥테크|"
    r"스프링\s*캠프|강소공|신사업창출",
    re.I,
)
# 제외패턴에 걸려도 파일명에 "계획서"가 함께 있으면 사업계획서로 간주
_PLAN_KEYWORD = re.compile(r"계획서", re.I)


def is_plan_filename(name: str) -> bool:
    if not name:
        return False
    if _EXCLUDE_FN.search(name):
        # "참가신청서 및 사업계획서" 처럼 계획서도 포함된 경우 통과
        if _PLAN_KEYWORD.search(name):
            return bool(_INCLUDE_FN.search(name))
        return False
    return bool(_INCLUDE_FN.search(name))


class NotionClient:
    def __init__(self, token: str, db_id: str, timeout: float = 30.0):
        self.db_id = db_id
        self.http = httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self.http.close()

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError,)),
        reraise=True,
    )
    def _post(self, path: str, json: dict) -> dict:
        r = self.http.post(f"{BASE}{path}", json=json)
        r.raise_for_status()
        return r.json()

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError,)),
        reraise=True,
    )
    def _get(self, path: str) -> dict:
        r = self.http.get(f"{BASE}{path}")
        r.raise_for_status()
        return r.json()

    def iter_plan_pages(self, page_size: int = 100) -> Iterator[dict]:
        """업무분야=사업계획서 + 문서유형(PDF or 한글) 필터 적용."""
        filter_body = {
            "and": [
                {"property": "업무분야", "multi_select": {"contains": "사업계획서"}},
                {
                    "or": [
                        {"property": "문서 유형", "multi_select": {"contains": "PDF"}},
                        {"property": "문서 유형", "multi_select": {"contains": "한글"}},
                    ]
                },
            ]
        }
        cursor: str | None = None
        while True:
            body: dict[str, Any] = {"page_size": page_size, "filter": filter_body}
            if cursor:
                body["start_cursor"] = cursor
            data = self._post(f"/databases/{self.db_id}/query", body)
            for p in data.get("results", []):
                yield p
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

    def page_blocks(self, page_id: str) -> list[dict]:
        blocks: list[dict] = []
        cursor: str | None = None
        while True:
            suffix = f"?page_size=100"
            if cursor:
                suffix += f"&start_cursor={cursor}"
            data = self._get(f"/blocks/{page_id}/children" + suffix)
            blocks.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return blocks


def _plain_text(rich: list[dict]) -> str:
    return "".join(r.get("plain_text", "") for r in rich or [])


def extract_row_fields(page: dict) -> dict:
    props = page.get("properties", {})

    def pick(name: str) -> Any:
        p = props.get(name)
        if not p:
            return None
        t = p.get("type")
        v = p.get(t)
        if t in ("title", "rich_text"):
            return _plain_text(v).strip() or None
        if t == "number":
            return v
        if t in ("select", "status"):
            return v.get("name") if v else None
        if t == "multi_select":
            return [x.get("name") for x in v or []]
        if t == "checkbox":
            return bool(v)
        if t == "date":
            return (v or {}).get("start")
        return None

    saup = pick("사업분야")
    return {
        "page_id": page["id"],
        "아이템명": pick("아이템명 (아이템 소개)"),
        "업체명": pick("업체명"),
        "연도": pick("연도"),
        "사업분야": saup,
        "산업군_1차": pick("산업군 1차 분류"),
        "산업군_2차": pick("산업군 2차 분류"),
        "산업군_태그": pick("산업군(콜아웃 규칙 참조)"),
        "주관기관": pick("주관기관"),
        "계약구분": pick("계약 구분"),
        "서류합격": pick("서류 합격"),
        "최종합격": pick("최종 합격"),
        "문서유형": pick("문서 유형"),
    }


def should_exclude_row(row: dict) -> tuple[bool, str]:
    saup = row.get("사업분야")
    if not saup:
        return False, ""
    if saup in EXCLUDE_EXACT_SAUP:
        return True, f"사업분야={saup}"
    for w in EXCLUDE_SAUP_WORDS:
        if w in saup:
            return True, f"사업분야 contains {w!r}"
    return False, ""


def _file_meta(fobj: dict) -> tuple[str | None, str | None]:
    t = fobj.get("type")
    url = None
    if t == "file":
        url = fobj.get("file", {}).get("url")
    elif t == "external":
        url = fobj.get("external", {}).get("url")
    return url, fobj.get("name")


def find_plan_pdf(client: NotionClient, page: dict) -> tuple[str | None, str | None]:
    """페이지 블록에서 '사업계획서' 파일 하나를 고른다. PDF 우선, 없으면 hwp."""
    try:
        blocks = client.page_blocks(page["id"])
    except httpx.HTTPError as e:
        log.warning("page_blocks failed page=%s err=%s", page.get("id"), e)
        return None, None

    candidates: list[tuple[str, str, str]] = []  # (ext, name, url)
    for b in blocks:
        bt = b.get("type")
        if bt in ("file", "pdf"):
            url, name = _file_meta(b.get(bt, {}))
            if not url or not name:
                continue
            if not is_plan_filename(name):
                continue
            ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
            candidates.append((ext, name, url))

    pdfs = [c for c in candidates if c[0] == "pdf"]
    if pdfs:
        return pdfs[0][2], pdfs[0][1]
    hwps = [c for c in candidates if c[0] in ("hwp", "hwpx")]
    if hwps:
        return hwps[0][2], hwps[0][1]
    return None, None
