#!/usr/bin/env python3
"""
scripts/generate_form_yaml.py  (v2)

PDF 사업계획서 양식 → data/forms/ YAML 자동 생성/갱신

v2 개선 사항:
  - 본문 섹션 페이지만 처리 (목차·일반현황·개요 페이지 제외)
  - 섹션 헤더: "숫자. 제목 (카테고리)_부제목" 에서 부제목을 title로 사용
  - 섹션 4(Team): 팀구성안·협력기관현황 표만 포함, 일반현황·목차 표 제외
  - max_funding·page_limit: PDF 본문 텍스트에서 실제 수치 추출
  - category "기업 구성" → Team 자동 보정
  - 기존 yaml 직접 덮어쓰기 (충돌 없음)
"""

import re
import sys
from pathlib import Path

import pdfplumber
import yaml

ROOT     = Path(__file__).resolve().parent.parent
PDF_DIR  = ROOT / "docs" / "사업계획서 양식"
FORMS_DIR = ROOT / "data" / "forms"

# ── 파일명 키워드 → program_code ──────────────────────────────────────────────
FILENAME_MAP = [
    ("초기창업패키지(딥테크 특화형)", "initial_package_deeptech"),
    ("초기창업패키지(일반형)",        "initial_package"),
    ("창업도약패키지(딥테크 특화형)", "jumping_package_deeptech"),
    ("창업도약패키지(일반형)",        "jumping_package"),
    ("예비창업패키지(일반형)",        "youth_academy"),
    ("청년창업사관학교",              "changjungdae"),
    ("딥테크창업사관학교",            "deeptech_academy"),
]

# ── 기본 메타 (PDF 추출 실패 시 fallback) ─────────────────────────────────────
DEFAULT_META: dict[str, dict] = {
    "initial_package":          {"target": "창업 3년 이내 초기 창업기업",                  "max_funding": "1억원",   "page_limit": 15},
    "initial_package_deeptech": {"target": "딥테크 분야 창업 3년 이내 초기 창업기업",       "max_funding": "1.5억원", "page_limit": 15},
    "jumping_package":          {"target": "창업 3년 초과 ~ 7년 이내 창업기업",             "max_funding": "2억원",   "page_limit": 20},
    "jumping_package_deeptech": {"target": "딥테크 분야 창업 3년 초과 ~ 7년 이내 창업기업", "max_funding": "3억원",   "page_limit": 20},
    "youth_academy":            {"target": "예비창업자 (창업 전)",                          "max_funding": "1억원",   "page_limit": 10},
    "changjungdae":             {"target": "만 39세 이하 청년 예비창업자 또는 창업 3년 이내","max_funding": "1억원",   "page_limit": 15},
    "deeptech_academy":         {"target": "딥테크 기술 창업 예정자 또는 창업 초기기업",    "max_funding": "2억원",   "page_limit": 15},
}

# ── 카테고리 감지 패턴 ────────────────────────────────────────────────────────
CATEGORY_PATTERNS = [
    (re.compile(r"Problem|문제[\s_]?인식|필요성",                re.I), "Problem"),
    (re.compile(r"Solution|실현[\s_]?가능성|개발[\s_]?계획",     re.I), "Solution"),
    (re.compile(r"Scale.?up|성장전략|사업화[\s_]?추진|사업화[\s_]?전략", re.I), "Scale-up"),
    (re.compile(r"Team|팀[\s_]?구성|대표자|기업[\s_]?구성",      re.I), "Team"),
]

SECTION_TAGS: dict[str, list] = {
    "Problem":  ["개발동기", "시장분석"],
    "Solution": ["개발준비", "차별성", "일정자금"],
    "Scale-up": ["BM", "사업화전략", "투자", "ESG"],
    "Team":     ["팀역량", "대표자"],
}

# 본문 섹션 헤더 패턴
# 형식 A (초기·예비창업패키지): "1. 문제 인식 (Problem)_창업 아이템의 필요성"  → _부제목 포함
# 형식 B (창업도약패키지):       "1. 문제인식 (Problem)"                       → _부제목 없음, 다음 줄에 부제목
BODY_HEADER_RE = re.compile(
    r"^(\d+)\.\s+"          # 번호
    r"([^_()\n]{2,25})"     # 짧은 제목 (괄호·밑줄 이전)
    r"\s*\([^)\n]{2,20}\)"  # (카테고리) 필수
    r"(?:_(.+))?$",         # _부제목 선택적 (형식 A만 존재)
    re.MULTILINE,
)

# 메타 페이지 감지: 일반현황·개요요약·목차 페이지 제외
META_PAGE_RE = re.compile(
    r"□\s*일반현황"
    r"|□\s*창업\s*아이템\s*개요"
    r"|□\s*창업아이템\s*개요"    # 도약패키지 변형
    r"|□\s*신청\s*및"           # 도약패키지 신청·일반현황
    r"|작성\s*목차"
    r"|목차\(안\)",
    re.MULTILINE,
)

# 팀 관련 표 키워드 (팀구성안·협력기관현황 표 감지)
TEAM_KEYWORDS: set[str] = {
    "직위", "담당 업무", "담당업무",
    "파트너명", "협업 방안", "협업방안",
    "구성 상태", "구성상태",
    "협력 시기", "협력시기",
}

# 정부지원 한도 추출 패턴
MAX_FUNDING_RE = re.compile(
    r"정부지원사업비는?\s*최대\s*([\d.]+)억원\s*한도"
)

# 페이지 제한 추출 패턴
PAGE_LIMIT_RE = re.compile(r"(\d+)페이지\s*(내외|이내)")


# ── 표 유틸 ───────────────────────────────────────────────────────────────────

def _cell(v) -> str:
    return str(v).replace("\n", " ").strip() if v else ""


def table_to_markdown(table: list) -> str | None:
    if not table or len(table) < 2:
        return None
    header = [_cell(c) for c in table[0]]
    if not any(header):
        return None
    col = len(header)
    rows = []
    for row in table[1:]:
        padded = list(row) + [None] * max(0, col - len(row))
        cells = [_cell(padded[i]) for i in range(col)]
        if any(cells):
            rows.append(cells)
    if not rows:
        return None
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * col) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def is_team_table(table: list) -> bool:
    """팀구성안 또는 협력기관현황 표인지 확인 (팀 관련 컬럼명 포함 여부)"""
    all_cells: set[str] = set()
    for row in table[:3]:
        for c in row:
            if c:
                all_cells.add(str(c).replace("\n", " ").strip())
    return bool(all_cells & TEAM_KEYWORDS)


# ── PDF 파싱 ──────────────────────────────────────────────────────────────────

def extract_pdf_pages(pdf_path: Path) -> list[dict]:
    pages = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages):
            pages.append({
                "num":    i + 1,
                "text":   page.extract_text() or "",
                "tables": page.extract_tables() or [],
            })
    return pages


def detect_category(text: str) -> str:
    for pat, cat in CATEGORY_PATTERNS:
        if pat.search(text):
            return cat
    return "General"


def parse_body_sections(pages: list[dict]) -> list[dict]:
    """
    본문 섹션 페이지만 처리하여 섹션 목록 반환.
    - META_PAGE_RE 매칭 페이지 건너뜀
    - BODY_HEADER_RE 매칭 줄에서 새 섹션 시작
    - 본문에서 ※ 힌트 수집
    """
    sections: list[dict] = []
    current: dict | None = None

    for pg in pages:
        text = pg["text"]

        # 목차·일반현황·개요 페이지 건너뜀
        if META_PAGE_RE.search(text):
            continue

        m = BODY_HEADER_RE.search(text)
        if m:
            if current:
                sections.append(current)

            num       = m.group(1)
            short     = m.group(2).strip()
            inline_sub = m.group(3).strip() if m.group(3) else None
            cat       = detect_category(m.group(0))

            # 부제목 결정: 형식 A는 inline, 형식 B는 헤더 다음 첫 비어있지 않은 줄
            if inline_sub:
                title = inline_sub
            else:
                # 헤더 줄 이후 텍스트에서 다음 실질적 줄 추출
                after = text[m.end():].lstrip("\n")
                next_line = after.split("\n")[0].strip()
                # 다음 줄이 부제목으로 보이면 사용 (※ 또는 빈 줄이면 short 사용)
                title = next_line if next_line and not next_line.startswith("※") else short

            current = {
                "id":       num,
                "title":    title,
                "category": cat,
                "order":    int(num),
                "tags":     SECTION_TAGS.get(cat, []),
                "pages":    [pg],
                "hints":    [],
            }
        elif current:
            # 현재 섹션의 연속 페이지
            current["pages"].append(pg)

        # ※ 힌트 수집 (어느 경우든)
        if current:
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("※") and len(stripped) > 3:
                    hint = stripped[1:].strip()
                    if hint and hint not in current["hints"]:
                        current["hints"].append(hint)

    if current:
        sections.append(current)

    return sections


def get_section_tables(sec: dict) -> list[str]:
    """
    섹션에 속한 표를 마크다운으로 변환.
    Team 섹션은 팀구성안·협력기관 표만 포함.
    """
    is_team = (sec["category"] == "Team")
    out: list[str] = []
    for pg in sec["pages"]:
        for tbl in pg["tables"]:
            if is_team and not is_team_table(tbl):
                continue
            md = table_to_markdown(tbl)
            if md and md not in out:
                out.append(md)
    return out


def build_instructions(sec: dict) -> str:
    parts: list[str] = []

    # ※ 힌트 문구 (최대 8개)
    for h in sec["hints"][:8]:
        parts.append(h)
    if parts:
        parts.append("")

    # 표 템플릿
    for i, md in enumerate(get_section_tables(sec), 1):
        parts.append(f"▶ 표 {i} — 필수:")
        parts.append(md)
        parts.append("")

    result = "\n".join(parts).strip()
    if not result:
        result = f"{sec['category']} 섹션. 해당 내용을 구체적으로 작성."
    return result + "\n"


# ── 메타값 추출 ───────────────────────────────────────────────────────────────

def extract_meta_values(pages: list[dict], code: str) -> tuple[str, int]:
    all_text = "\n".join(pg["text"] for pg in pages)
    defaults = DEFAULT_META.get(code, {})

    # max_funding: "정부지원사업비는 최대 X억원 한도" 첫 번째 매칭
    m = MAX_FUNDING_RE.search(all_text)
    max_funding = f"{m.group(1)}억원" if m else defaults.get("max_funding", "미정")

    # page_limit: "X페이지 내외/이내" 유효 범위(5~50) 첫 번째 매칭
    page_limit = defaults.get("page_limit", 15)
    for m2 in PAGE_LIMIT_RE.finditer(all_text):
        n = int(m2.group(1))
        if 5 <= n <= 50:
            page_limit = n
            break

    return max_funding, page_limit


# ── YAML 빌드 ─────────────────────────────────────────────────────────────────

def build_yaml_data(code: str, prog_name: str, sections: list[dict], pages: list[dict]) -> dict:
    defaults = DEFAULT_META.get(code, {})
    max_funding, page_limit = extract_meta_values(pages, code)

    sec_list = []
    for sec in sections:
        sec_list.append({
            "id":           sec["id"],
            "title":        sec["title"],
            "category":     sec["category"],
            "order":        sec["order"],
            "tags":         sec["tags"],
            "instructions": build_instructions(sec),
        })

    return {
        "program_code": code,
        "program_name": prog_name,
        "target":       defaults.get("target", "해당없음"),
        "max_funding":  max_funding,
        "page_limit":   page_limit,
        "notes": (
            "개인정보(성명·성별·생년월일·출신학교·소재지 등)는 마스킹(○, * 등) 또는 삭제.\n"
            "파란색 안내 문구 삭제 후 검정 글씨로 작성.\n"
            "표 안의 행은 추가 가능; 해당 없을 시 공란 유지.\n"
        ),
        "sections": sec_list,
    }


# ── YAML 직렬화 ───────────────────────────────────────────────────────────────

class _BlockStr(str):
    pass


def _block_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.add_representer(_BlockStr, _block_representer)


def _walk(obj):
    if isinstance(obj, dict):
        return {k: (_BlockStr(v) if k in ("instructions", "notes") and isinstance(v, str) else _walk(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(i) for i in obj]
    return obj


def to_yaml_str(data: dict) -> str:
    return yaml.dump(_walk(data), allow_unicode=True, default_flow_style=False, sort_keys=False, width=120)


# ── 파일명 → (code, name) ─────────────────────────────────────────────────────

def resolve_program(pdf_stem: str) -> tuple[str, str] | None:
    for keyword, code in FILENAME_MAP:
        if keyword in pdf_stem:
            year_m = re.search(r"(\d{4})년", pdf_stem)
            year   = f"{year_m.group(1)}년도" if year_m else "2026년도"
            return code, f"{year} {keyword}"
    return None


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    sys.stdout.reconfigure(encoding="utf-8")

    pdf_files = sorted(PDF_DIR.glob("*.pdf"))
    if not pdf_files:
        print(f"[오류] PDF 없음: {PDF_DIR}")
        sys.exit(1)

    results: list[dict] = []

    for pdf_path in pdf_files:
        stem     = pdf_path.stem
        resolved = resolve_program(stem)
        if not resolved:
            results.append({"file": stem, "status": "SKIP", "note": "매핑 없음"})
            continue

        code, prog_name = resolved
        out_path    = FORMS_DIR / f"{code}.yaml"
        is_overwrite = out_path.exists()
        mode_label  = "덮어쓰기" if is_overwrite else "신규    "

        print(f"\n처리: {stem}")
        print(f"  → {code}  [{mode_label}]  →  {out_path.name}")

        try:
            pages    = extract_pdf_pages(pdf_path)
            sections = parse_body_sections(pages)

            if not sections:
                results.append({"file": stem, "status": "WARN", "note": "섹션 감지 0개 — 기존 파일 유지"})
                print("  ⚠ 섹션 감지 실패 — 기존 파일 유지")
                continue

            data = build_yaml_data(code, prog_name, sections, pages)
            out_path.write_text(to_yaml_str(data), encoding="utf-8")

            tbl_total = sum(len(get_section_tables(s)) for s in sections)
            sec_summary = " / ".join(
                f"[{s['id']}]{s['category']}({len(get_section_tables(s))}표)" for s in sections
            )
            print(f"  ✓ {sec_summary}")
            print(f"    max_funding={data['max_funding']} | page_limit={data['page_limit']}p | 표 합계={tbl_total}개")

            results.append({
                "file": stem, "status": "OK",
                "output": out_path.name,
                "sections": len(sections),
                "tables": tbl_total,
                "overwrite": is_overwrite,
                "max_funding": data["max_funding"],
                "page_limit": data["page_limit"],
            })

        except Exception as e:
            import traceback
            results.append({"file": stem, "status": "ERROR", "note": str(e)})
            print(f"  ✗ 오류: {e}")
            traceback.print_exc()

    # ── 결과 요약 ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("처리 결과 요약")
    print("=" * 70)
    ok = [r for r in results if r["status"] == "OK"]
    for r in results:
        if r["status"] == "OK":
            flag = "덮어쓰기" if r["overwrite"] else "신규    "
            print(
                f"  [완료/{flag}]  {r['output']:<40s}"
                f"  섹션 {r['sections']}개 / 표 {r['tables']}개"
                f"  [{r['max_funding']} / {r['page_limit']}p]"
            )
        elif r["status"] == "SKIP":
            print(f"  [건너뜀]        {r['file']:<40s}  {r['note']}")
        elif r["status"] == "WARN":
            print(f"  [경고]          {r['file']:<40s}  {r['note']}")
        else:
            print(f"  [오류]          {r['file']:<40s}  {r['note']}")
    print("=" * 70)
    print(f"  총 {len(pdf_files)}개 처리  /  성공 {len(ok)}개  /  건너뜀·경고·오류 {len(results) - len(ok)}개")


if __name__ == "__main__":
    main()
