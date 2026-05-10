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
    form: Form,
    results: list[SectionResult],
    business_name: str = "(미지정)",
) -> BytesIO:
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
    run = title_para.add_run(form.program_name)
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
