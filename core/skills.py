"""Skill 파일 로더.

skills/ 폴더 하위의 Markdown 파일을 읽어 구조화된 Skill 객체로 반환.
각 파일은 YAML frontmatter + 본문 형식.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Skill:
    skill_id: str
    layer: int
    scope: str
    title: str
    body: str
    metadata: dict

    def to_prompt_block(self) -> str:
        """프롬프트에 삽입할 수 있는 형태로 변환."""
        return f"## [Skill: {self.title}] (Layer {self.layer})\n\n{self.body.strip()}\n"


def parse_skill_file(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"Skill file missing frontmatter: {path}")
    _, fm, body = text.split("---", 2)
    metadata = yaml.safe_load(fm) or {}
    return Skill(
        skill_id=metadata.get("skill_id", path.stem),
        layer=int(metadata.get("layer", 0)),
        scope=metadata.get("scope", ""),
        title=metadata.get("title", path.stem),
        body=body.strip(),
        metadata=metadata,
    )


_skills_cache: dict[str, list[Skill]] = {}


def load_skills(skills_dir: str | Path = "skills") -> list[Skill]:
    """모든 Skill 파일을 layer 순으로 정렬해 반환."""
    key = str(Path(skills_dir).resolve())
    if key in _skills_cache:
        return _skills_cache[key]
    skills: list[Skill] = []
    root = Path(skills_dir)
    for md in root.rglob("*.md"):
        try:
            skills.append(parse_skill_file(md))
        except Exception as e:
            print(f"[skill load failed] {md}: {e}")
    skills.sort(key=lambda s: (s.layer, s.skill_id))
    _skills_cache[key] = skills
    return skills


def select_skills_for_section(
    skills: list[Skill],
    section_category: str,
    section_tags: list[str],
) -> list[Skill]:
    """섹션 생성 시 적용할 Skill을 선택.

    현재 로직:
      - Layer 1 (Universal): 전부 적용
      - Layer 2 (Section): scope에 카테고리·태그 키워드가 있으면 적용
      - Layer 3 (Program): P_common은 항상 적용
      - Layer 4 (Industry): 자동 판별 Skill 항상 적용 (LLM이 내부에서 사용)
    """
    selected: list[Skill] = []
    keywords = [section_category, *section_tags]

    for s in skills:
        if s.layer == 1:
            selected.append(s)
        elif s.layer == 2:
            if s.scope.strip().lower() == "all":
                selected.append(s)
            elif any(k in s.scope for k in keywords) or any(k in s.body[:500] for k in keywords):
                selected.append(s)
        elif s.layer == 3:
            selected.append(s)
        elif s.layer == 4:
            selected.append(s)

    return selected


if __name__ == "__main__":
    ss = load_skills()
    for s in ss:
        print(f"  L{s.layer} {s.skill_id}: {s.title}")
    print(f"\nTotal: {len(ss)} skills")
