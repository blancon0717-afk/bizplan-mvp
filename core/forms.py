"""양식 YAML 로드 모듈."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class FormSection:
    id: str
    title: str
    category: str
    order: int
    tags: list[str]
    instructions: str


@dataclass
class Form:
    program_code: str
    program_name: str
    target: str
    max_funding: str
    page_limit: int
    notes: Optional[str]
    sections: list[FormSection]
    # 양식 변환 전 갭 보완 인터뷰용 고정 질문 (YAML gap_questions 키, 없으면 빈 리스트)
    # 항목: {id, question, hint, target_sections: [섹션id]}
    gap_questions: list[dict] = field(default_factory=list)

    def get_section(self, section_id: str) -> Optional[FormSection]:
        for s in self.sections:
            if s.id == section_id:
                return s
        return None


_DEFAULT_FORMS_DIR = Path(__file__).resolve().parent.parent / "data" / "forms"
_form_cache: dict[str, Form] = {}


def load_form(program_code: str, forms_dir: str | Path = _DEFAULT_FORMS_DIR) -> Form:
    cache_key = f"{forms_dir}/{program_code}"
    if cache_key in _form_cache:
        return _form_cache[cache_key]
    path = Path(forms_dir) / f"{program_code}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    sections = [
        FormSection(
            id=str(s["id"]),
            title=s["title"],
            category=s["category"],
            order=s["order"],
            tags=list(s.get("tags", [])),
            instructions=s.get("instructions", "").strip(),
        )
        for s in data["sections"]
    ]
    sections.sort(key=lambda s: s.order)
    form = Form(
        program_code=data["program_code"],
        program_name=data["program_name"],
        target=data["target"],
        max_funding=str(data["max_funding"]),
        page_limit=data.get("page_limit", 10),
        notes=data.get("notes"),
        sections=sections,
        gap_questions=list(data.get("gap_questions", []) or []),
    )
    _form_cache[cache_key] = form
    return form


def list_programs(forms_dir: str | Path = "data/forms") -> list[tuple[str, str]]:
    """사용 가능한 양식 리스트 반환: [(program_code, program_name)]"""
    result = []
    for f in Path(forms_dir).glob("*.yaml"):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        result.append((data["program_code"], data["program_name"]))
    return sorted(result)


if __name__ == "__main__":
    import sys

    for code, name in list_programs():
        print(f"  {code}: {name}")
        f = load_form(code)
        for s in f.sections:
            print(f"    [{s.id}] {s.title} ({s.category}, tags={s.tags})")
