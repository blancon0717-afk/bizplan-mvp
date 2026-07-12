"""생성된 섹션들을 DOCX로 내보내기.

출력 규칙:
- 좌측 사업계획 본문 내용만 출력 (메모/검토영역/신뢰도 배지 미포함)
- user_answer 세그먼트: 기본 검정
- llm_inferred 세그먼트: 회색 (RGB 128,128,128)
- [출처 필요] / [추정값] / [수치 필요] 태그: 빨간색
"""

from __future__ import annotations

import re
from io import BytesIO

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from core.forms import Form
from core.generation import ContentSegment, SectionResult

_FONT = "맑은 고딕"
_COLOR_BLACK = RGBColor(17, 17, 17)
_COLOR_BLUE = RGBColor(59, 130, 246)
_COLOR_BLUE_DARK = RGBColor(29, 78, 216)
_MD_TABLE_ROW = re.compile(r"^\s*\|")
_BULLET_LINE = re.compile(r"^\s*-\s")
_HEADING_LINE = re.compile(r"^■")
_CAPTION_CELL = re.compile(r"^<.*>$")
_SOURCE_CELL = re.compile(r"^출처:")
_DESCRIPTION_CELL = re.compile(r"^\[")
_URL_PATTERN = re.compile(r"https?://\S+")


def _set_font(run, size_pt: float, bold: bool, color: RGBColor) -> None:
    run.font.name = _FONT
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    run.font.color.rgb = color
    rpr = run._r.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:ascii"), _FONT)
    rfonts.set(qn("w:hAnsi"), _FONT)
    rfonts.set(qn("w:eastAsia"), _FONT)


def _set_cell_bg(cell, fill_hex: str) -> None:
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = tcPr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tcPr.append(shd)
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill_hex)


def _add_runs(para, text: str, size_pt: float, bold: bool, base_color: RGBColor) -> None:
    if not text:
        return
    run = para.add_run(text)
    _set_font(run, size_pt, bold, base_color)


def _parse_md_table(lines: list[str]) -> tuple[list[list[str]], list[list[str]]]:
    def parse_row(line: str) -> list[str]:
        cells = [c.strip() for c in line.split("|")]
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        return cells

    rows = [parse_row(l) for l in lines]
    sep_idx = next(
        (i for i, r in enumerate(rows) if r and all(re.match(r"^[-:\s]+$", c) for c in r)),
        -1,
    )
    headers = rows[:sep_idx] if sep_idx >= 0 else []
    body = rows[sep_idx + 1:] if sep_idx >= 0 else rows
    return headers, body


def _add_segment(doc: Document, seg: ContentSegment) -> None:
    base_color = _COLOR_BLACK
    lines = seg.text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # 마크다운 표
        if _MD_TABLE_ROW.match(line):
            table_block: list[str] = []
            while i < len(lines) and _MD_TABLE_ROW.match(lines[i]):
                table_block.append(lines[i])
                i += 1
            headers, body = _parse_md_table(table_block)
            all_rows = headers + body
            if not all_rows:
                continue
            num_cols = max(len(r) for r in all_rows)
            tbl = doc.add_table(rows=len(all_rows), cols=num_cols)
            tbl.style = "Table Grid"
            for ri, row_cells in enumerate(all_rows):
                is_header = ri < len(headers)
                is_caption = not is_header and bool(row_cells) and all(
                    ct.strip() == "" or bool(_CAPTION_CELL.match(ct.strip()))
                    for ct in row_cells
                )
                for ci in range(num_cols):
                    cell = tbl.cell(ri, ci)
                    ct = (row_cells[ci] if ci < len(row_cells) else "").strip()
                    para = cell.paragraphs[0]
                    if is_header:
                        _set_cell_bg(cell, "F8FAFC")
                        _add_runs(para, ct, 9, True, base_color)
                    elif is_caption:
                        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        run = para.add_run(ct)
                        _set_font(run, 8, False, _COLOR_BLACK)
                        run.font.italic = True
                    elif _DESCRIPTION_CELL.match(ct):
                        _set_cell_bg(cell, "DBEAFE")
                        run = para.add_run(ct)
                        _set_font(run, 9, False, _COLOR_BLUE_DARK)
                        run.font.italic = True
                    elif _SOURCE_CELL.match(ct):
                        url_m = _URL_PATTERN.search(ct)
                        if url_m:
                            run_pre = para.add_run(ct[:url_m.start()])
                            _set_font(run_pre, 8, False, _COLOR_BLACK)
                            run_url = para.add_run(url_m.group())
                            _set_font(run_url, 8, False, _COLOR_BLUE)
                            run_url.font.underline = True
                            tail = ct[url_m.end():]
                            if tail:
                                run_tail = para.add_run(tail)
                                _set_font(run_tail, 8, False, _COLOR_BLACK)
                        else:
                            run = para.add_run(ct)
                            _set_font(run, 8, False, _COLOR_BLUE)
                    else:
                        _add_runs(para, ct, 9, False, base_color)
            doc.add_paragraph()

        # 빈 줄
        elif line.strip() == "":
            doc.add_paragraph()
            i += 1

        # ■ 중제목
        elif _HEADING_LINE.match(line):
            para = doc.add_paragraph()
            para.paragraph_format.space_before = Pt(2)
            para.paragraph_format.space_after = Pt(0)
            _add_runs(para, line, 10, True, base_color)
            i += 1

        # 세부항목 ( - )
        elif _BULLET_LINE.match(line):
            bullet_lines: list[str] = []
            while i < len(lines) and _BULLET_LINE.match(lines[i]):
                bullet_lines.append(lines[i])
                i += 1
            for bl in bullet_lines:
                para = doc.add_paragraph()
                para.paragraph_format.space_before = Pt(0)
                para.paragraph_format.space_after = Pt(1)
                para.paragraph_format.left_indent = Cm(0.5)
                _add_runs(para, bl, 10, False, base_color)

        # 일반 단락
        else:
            text_lines: list[str] = []
            while (
                i < len(lines)
                and not _MD_TABLE_ROW.match(lines[i])
                and lines[i].strip() != ""
                and not _HEADING_LINE.match(lines[i])
                and not _BULLET_LINE.match(lines[i])
            ):
                text_lines.append(lines[i])
                i += 1
            joined = "\n".join(text_lines).strip()
            if joined:
                para = doc.add_paragraph()
                para.paragraph_format.space_after = Pt(4)
                _add_runs(para, joined, 10, False, base_color)


def export_to_docx(
    form: Form | None,
    results: list[SectionResult],
    business_name: str = "(미지정)",
    title: str | None = None,
) -> BytesIO:
    """섹션 결과를 DOCX로 변환.

    title이 주어지면 표지 제목으로 사용(양식 없는 초안 export용).
    없으면 form.program_name을 사용한다.
    """
    doc = Document()

    # 여백 설정 (25mm)
    for section in doc.sections:
        section.top_margin = Cm(2.5)
        section.bottom_margin = Cm(2.5)
        section.left_margin = Cm(2.5)
        section.right_margin = Cm(2.5)

    # Normal 스타일 기본 폰트를 맑은 고딕으로 (ASCII + 한글 모두)
    style = doc.styles["Normal"]
    style.font.name = _FONT  # ascii, hAnsi 설정
    style.font.size = Pt(10)
    # style.font.name이 rPr/rFonts를 생성한 뒤 eastAsia 추가
    style_rpr = style.element.find(qn("w:rPr"))
    if style_rpr is not None:
        style_rfonts = style_rpr.find(qn("w:rFonts"))
        if style_rfonts is not None:
            style_rfonts.set(qn("w:eastAsia"), _FONT)

    # 표지 제목
    title_para = doc.add_paragraph()
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_para.paragraph_format.space_after = Pt(6)
    program_name = title or (form.program_name if form else "사업계획서")
    run = title_para.add_run(program_name)
    _set_font(run, 16, True, _COLOR_BLACK)

    # 기업명
    info_para = doc.add_paragraph()
    info_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = info_para.add_run(f"기업명: {business_name}")
    _set_font(run, 10, False, _COLOR_BLACK)

    doc.add_paragraph()  # 제목 아래 여백

    for r in results:
        # 섹션 대제목
        heading_para = doc.add_paragraph()
        heading_para.paragraph_format.space_before = Pt(8)
        heading_para.paragraph_format.space_after = Pt(4)
        run = heading_para.add_run(f"[{r.section_id}] {r.section_title}")
        _set_font(run, 12, True, _COLOR_BLACK)

        if r.user_edited_content is not None:
            _add_segment(doc, ContentSegment(text=r.user_edited_content, source="user_answer"))
        else:
            segments = r.content_segments or [
                ContentSegment(text=r.content or "", source="llm_inferred")
            ]
            for seg in segments:
                if seg.text.strip():
                    _add_segment(doc, seg)

        doc.add_paragraph()  # 섹션 간 여백

    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


# ────────────────────────────────────────────────────────────────────
# 공식 양식 템플릿 렌더링 (docxtpl)
#
# data/templates/{program_code}.docx — 공식 양식 레이아웃을 재현한 템플릿에
# 변환 결과를 서브독으로 주입한다. 템플릿·의존성이 없으면 None을 반환해
# 호출측이 기존 일반 렌더러(export_to_docx)로 폴백한다.
# ────────────────────────────────────────────────────────────────────

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "data" / "templates"

# 템플릿에 공식 빈 표로 내장되어 LLM 콘텐츠 주입을 건너뛰는 섹션
# (scripts/build_form_templates.py의 STATIC_SECTIONS와 반드시 일치)
TEMPLATE_STATIC_SECTIONS: dict[str, frozenset[str]] = {
    "deeptech_academy": frozenset({"0-1", "0-2"}),
    "initial_package": frozenset({"0-1"}),
    "innovation_voucher": frozenset({"1"}),
}

# 정적 표의 과제명/아이템명 셀에 넣을 제안값 추출용
_PROJECT_NAME_RE = re.compile(
    r"\|\s*(?:사업화 과제명|창업아이템명)[^|]*\|\s*([^|\n]+?)\s*\|"
)


def _extract_project_name(results: list[SectionResult]) -> str:
    """0-x 섹션 변환 결과에서 과제명/창업아이템명 제안값 추출. 없으면 빈 문자열."""
    for r in results:
        if not str(r.section_id).startswith("0"):
            continue
        text = r.user_edited_content if r.user_edited_content is not None else (r.content or "")
        m = _PROJECT_NAME_RE.search(text)
        if not m:
            continue
        val = m.group(1).strip().strip("()").strip()
        # 가이드 문구가 그대로 남은 경우 제외
        if not val or "제안" in val or "초안" in val or "기재" in val:
            continue
        return val[:80]
    return ""


def export_to_official_docx(
    form: Form,
    results: list[SectionResult],
    business_name: str = "(미지정)",
) -> BytesIO | None:
    """공식 양식 템플릿에 변환 결과를 채워 DOCX 생성.

    템플릿 파일이 없거나 docxtpl 미설치·렌더 실패 시 None 반환(호출측 폴백).
    """
    try:
        from docxtpl import DocxTemplate
    except ImportError:
        logger.warning("[DOCX] docxtpl 미설치 — 일반 렌더러로 폴백")
        return None

    tpl_path = _TEMPLATES_DIR / f"{form.program_code}.docx"
    if not tpl_path.exists():
        return None

    try:
        tpl = DocxTemplate(str(tpl_path))
        static_ids = TEMPLATE_STATIC_SECTIONS.get(form.program_code, frozenset())

        # 누락 섹션은 빈 문자열로 프리필(Jinja undefined 방지)
        sections: dict[str, object] = {
            s.id: "" for s in form.sections if s.id not in static_ids
        }
        by_id = {r.section_id: r for r in results}
        for sid in list(sections.keys()):
            r = by_id.get(sid)
            if r is None:
                continue
            sd = tpl.new_subdoc()
            if r.user_edited_content is not None:
                segs = [ContentSegment(text=r.user_edited_content, source="user_answer")]
            else:
                segs = r.content_segments or [
                    ContentSegment(text=r.content or "", source="llm_inferred")
                ]
            for seg in segs:
                if seg.text.strip():
                    _add_segment(sd, seg)
            sections[sid] = sd

        tpl.render({
            "business_name": business_name,
            "project_name": _extract_project_name(results),
            "sections": sections,
        })
        buf = BytesIO()
        tpl.save(buf)
        buf.seek(0)
        return buf
    except Exception as e:  # noqa: BLE001 — 템플릿 문제로 다운로드 자체가 막히면 안 됨
        logger.error("[DOCX] 공식 템플릿 렌더 실패(%s): %s — 일반 렌더러 폴백", form.program_code, e)
        return None
