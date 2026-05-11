"""섹션 생성 파이프라인.

1. 답변 매핑 (tag 기반 기본, 필요 시 LLM으로 2차)
2. Skill 선택
3. 프롬프트 조립
4. Claude 호출 (또는 Mock 모드에선 더미 응답)
5. JSON 파싱 + 결과 객체 반환

Mock 모드: 환경변수 MOCK_MODE=1 설정 시, 실제 Claude 호출 없이
그럴듯한 더미 응답 반환. API 키 없이 UI 전체 흐름 테스트 가능.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from core.context_extraction import (
    format_context_block,
    get_section_context_fields,
)
from core.feedback_rag import get_feedback_examples
from core.forms import Form, FormSection
from core.interview import Answer, Question
from core.llm import call_claude, parse_json_response
from core.mapping import get_answer_context, map_by_tags
from core.rubric_scorer import score_text
from core.skills import Skill, select_skills_for_section

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_SCHEDULE_TAG = "일정자금"


def _build_today_date_note(tags: list[str]) -> str:
    """일정자금 태그가 있는 섹션에만 작성 기준일 규칙을 반환."""
    if _SCHEDULE_TAG not in tags:
        return ""
    today = datetime.now().strftime("%Y년 %m월")
    return (
        f"\n> ⏰ **추진일정 날짜 규칙** (사업계획서 작성일: {today})\n"
        f"> - 시작 시점: {today} 기준으로 작성\n"
        f"> - 1개월 단위로 구성\n"
        f"> - {today} 이전 날짜(과거 날짜) 절대 사용 금지\n"
    )


def _clean_segment_text(text: str) -> str:
    """LLM이 생성한 세그먼트 텍스트에서 금지된 서식 기호를 제거."""
    # ### / ## / # 마크다운 제목 기호 제거 (줄 시작)
    text = re.sub(r"^#{1,3}\s*", "", text, flags=re.MULTILINE)
    # "1. 제목", "1-1. 제목", "2-1-1. 제목" 형태 섹션 번호 제거 (줄 시작) — 양식 목차 침범 방지
    text = re.sub(r"^(\d+(?:-\d+)*)\.\s+", "", text, flags=re.MULTILINE)
    # **강조** → 강조 (별표 쌍 제거)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    # *기울임* 제거
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    # [카테고리명] 레이블 제거 — 불릿 줄 시작의 대괄호 레이블
    text = re.sub(r"^\s*\[([^\]]{2,15})\]\s*", "", text, flags=re.MULTILINE)
    # 금지 플레이스홀더 완전 제거 — [xxx 필요], [xxx 입력], [xxx: xxx?] 포함
    text = re.sub(r"\[[^\]]*(?:필요|입력|추정값)\]", "", text)
    text = re.sub(r"\[[^\]]*\?[^\]]*\]", "", text)
    # (* 출처 필요), (* 확인 필요) 등 주석 형태 완전 제거
    text = re.sub(r"\(\*\s*[^)]+\)", "", text)
    # [30~40%], [약 15~20%] 등 수치 범위 대괄호 → 괄호 제거, 수치 유지
    text = re.sub(r"\[([0-9약\s~\-\.,%\+\/]{2,15})\]", r"\1", text)
    # (추정 필요), (입력 필요), (확인 필요), (미정) 등 소괄호 형태 제거
    text = re.sub(r"\([^)]*(?:추정 필요|입력 필요|확인 필요|미정)[^)]*\)", "", text)
    # [추정 필요], [미정] 등 대괄호 형태 추가 제거 (미정·확인 필요 보강)
    text = re.sub(r"\[[^\]]*(?:추정 필요|입력 필요|확인 필요|미정)[^\]]*\]", "", text)
    # : 추정 필요, : 입력 필요, : 확인 필요, : 미정 등 콜론 뒤 인라인 형태 제거
    text = re.sub(r":\s*(?:추정 필요|입력 필요|확인 필요|미정)\b", "", text)
    # 문장 중간의 플레이스홀더 제거 — [기존 솔루션 1], [지역/산업/고객군], [구체적 수치 또는 기능] 등
    text = re.sub(r"\[[^\]]{2,30}\]", "", text)
    # (N개사/N명), (N%) 등 미입력 수치 플레이스홀더 제거
    text = re.sub(r"\([N][^\)]{0,20}\)", "", text)
    return text.strip()
_EVAL_CRITERIA_PATH = Path(__file__).resolve().parent.parent / "skills" / "L2_section" / "S03_item_keyword_strategy.md"
_EVAL_PROMPT_PATH = _PROMPTS_DIR / "section_evaluation.md"
_STRATEGIC_GUIDE_PATH = _PROMPTS_DIR / "strategic_feedback_guide.md"
_STRATEGIC_EVAL_PATH = _PROMPTS_DIR / "strategic_evaluation.md"
_JUDGE_SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / "L3_program" / "P_judge_feedback_skill.md"

# 모듈 레벨 파일 캐시 — 프로세스 재시작 전까지 디스크 재독 없음
_cache_system_md: str | None = None
_cache_section_gen: str | None = None
_cache_eval_criteria: str | None = None
_cache_eval_prompt: str | None = None
_cache_strategic_guide: str | None = None
_cache_strategic_eval: str | None = None
_cache_judge_skill: str | None = None


def _load_system_md() -> str:
    global _cache_system_md
    if _cache_system_md is None:
        _cache_system_md = (_PROMPTS_DIR / "system.md").read_text(encoding="utf-8")
    return _cache_system_md


def _load_section_gen() -> str:
    global _cache_section_gen
    if _cache_section_gen is None:
        _cache_section_gen = (_PROMPTS_DIR / "section_generation.md").read_text(encoding="utf-8")
    return _cache_section_gen


def _strip_frontmatter(text: str) -> str:
    """YAML frontmatter(--- 블록) 제거 후 본문만 반환."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        return parts[2] if len(parts) >= 3 else text
    return text


def _load_eval_criteria() -> str:
    global _cache_eval_criteria
    if _cache_eval_criteria is None:
        text = _strip_frontmatter(_EVAL_CRITERIA_PATH.read_text(encoding="utf-8"))
        # 핵심 2개 섹션만 추출: 11.4KB → ~3KB (토큰 70% 절감)
        _KEEP = {"합격 클러스터 8가지", "제목 자동 검증 체크리스트"}
        chunks = re.split(r'\n(?=## )', text)
        kept = [c for c in chunks if any(k in c.split('\n')[0] for k in _KEEP)]
        _cache_eval_criteria = "\n\n".join(kept).strip()
    return _cache_eval_criteria


def _load_eval_prompt() -> str:
    global _cache_eval_prompt
    if _cache_eval_prompt is None:
        _cache_eval_prompt = _EVAL_PROMPT_PATH.read_text(encoding="utf-8")
    return _cache_eval_prompt


def _load_strategic_guide() -> str:
    global _cache_strategic_guide
    if _cache_strategic_guide is None:
        _cache_strategic_guide = _STRATEGIC_GUIDE_PATH.read_text(encoding="utf-8")
    return _cache_strategic_guide


def _load_strategic_eval() -> str:
    global _cache_strategic_eval
    if _cache_strategic_eval is None:
        _cache_strategic_eval = _STRATEGIC_EVAL_PATH.read_text(encoding="utf-8")
    return _cache_strategic_eval


def _load_judge_skill() -> str:
    global _cache_judge_skill
    if _cache_judge_skill is None:
        _cache_judge_skill = _strip_frontmatter(
            _JUDGE_SKILL_PATH.read_text(encoding="utf-8")
        ).strip()
    return _cache_judge_skill


@dataclass
class InlineSuggestion:
    anchor_text: str        # 본문에서 정확히 일치하는 텍스트
    note: str               # 개선 조언 (꼬리질문 역할)
    severity: str = "warning"  # critical | warning | info
    response: str = ""       # 사용자가 메모에 단 답변 (고도화 시 사용)


@dataclass
class ContentSegment:
    """섹션 본문을 '출처' 단위로 분절한 세그먼트."""
    text: str
    source: str                # "user_answer" | "llm_inferred"
    source_qids: list[str] = field(default_factory=list)


@dataclass
class SectionResult:
    section_id: str
    section_title: str
    content: str                    # 합쳐진 본문 (하위호환, segments에서 자동 생성)
    confidence_level: str
    reasoning: str
    used_answer_ids: list[str] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    inline_suggestions: list[InlineSuggestion] = field(default_factory=list)
    content_segments: list[ContentSegment] = field(default_factory=list)
    user_edited_content: str | None = None   # 사용자가 직접 편집한 내용 (우선 사용)
    rubric_check: dict = field(default_factory=dict)
    llm_meta: dict = field(default_factory=dict)
    completion_score: int = 0        # 0~100, LLM 자체 평가 (섹션 완성도)
    completion_reasoning: str = ""   # 왜 이 점수인지 LLM 설명

    def resolved_memo_count(self) -> int:
        """사용자가 답변을 작성한 메모 수."""
        return sum(1 for s in self.inline_suggestions if s.response.strip())

    def effective_completion_score(self) -> int:
        """메모 해소율을 반영한 실효 완성도.

        LLM 자체 점수를 베이스로 하되, 아직 재생성되지 않은 상태에서도
        사용자가 메모에 답변을 채우면 그 비율만큼 (100까지) 선형 상향 표시.
        """
        base = max(0, min(100, self.completion_score))
        total_memos = len(self.inline_suggestions)
        if total_memos == 0:
            return base
        resolved_ratio = self.resolved_memo_count() / total_memos
        # 남은 여유분(100 - base)의 resolved_ratio 만큼을 추가 반영
        return round(base + (100 - base) * resolved_ratio)

    def display_content(self) -> str:
        """화면에 보일 본문 (편집본 우선)."""
        return self.user_edited_content if self.user_edited_content is not None else self.content


def _mock_section_result(
    section: FormSection,
    primary_qids: list[str],
    supporting_qids: list[str],
) -> SectionResult:
    """Mock 모드 — API 호출 없이 더미 결과 반환."""
    levels = ["green", "yellow", "red"]
    weights = [0.5, 0.35, 0.15] if primary_qids else [0.1, 0.3, 0.6]
    level = random.choices(levels, weights=weights, k=1)[0]

    content = (
        f"## [Mock 출력] {section.title}\n\n"
        f"이 섹션은 **Mock 모드**로 생성된 더미 텍스트입니다. "
        f"실제 API 연동 시 Claude가 위 Skills와 답변을 바탕으로 "
        f"약 3~5단락, 표 1~2개, 정량 수치 포함한 섹션을 작성합니다.\n\n"
        f"○ **섹션 카테고리**: {section.category}\n"
        f"  - 섹션 태그: {', '.join(section.tags) or '(없음)'}\n"
        f"  - 매핑된 Primary 답변: {len(primary_qids)}개\n"
        f"  - 매핑된 Supporting 답변: {len(supporting_qids)}개\n\n"
        f"○ **작성 지침 (Mock)**\n"
        f"  - {section.instructions[:200]}...\n\n"
        f"### 📌 시각 자료 삽입 가이드 (Mock 예시)\n\n"
        f"| 📊 자료 1 삽입 가이드 | 📰 자료 2 삽입 가이드 |\n"
        f"|---|---|\n"
        f"| [가이드] {section.category} 관련 **정량 데이터 차트**를 삽입하세요. "
        f"예: 시장 규모·점유율·성장률 막대그래프 (2022~2025년, 출처 명기) | "
        f"[가이드] 본 섹션 주장을 뒷받침하는 **3자 근거 자료**(뉴스 기사·업계 보고서·전문가 인용)를 삽입하세요. "
        f"예: 관련 이슈 뉴스 스크린샷 + 본문 인용 |\n"
        f"| **자료 1 제목**: {section.title} — 핵심 수치 트렌드 | "
        f"**자료 2 제목**: {section.title} — 업계·전문가 검증 자료 |\n"
    )

    missing = []
    suggestions = []
    # Mock segments: 일부는 user_answer, 일부는 llm_inferred로 분류
    segments = [
        ContentSegment(
            text=f"## [Mock 출력] {section.title}",
            source="llm_inferred",
        ),
        ContentSegment(
            text=(
                "이 섹션은 **Mock 모드**로 생성된 더미 텍스트입니다. "
                "실제 API 연동 시 Claude가 위 Skills와 답변을 바탕으로 "
                "약 3~5단락, 표 1~2개, 정량 수치 포함한 섹션을 작성합니다."
            ),
            source="llm_inferred",
        ),
        ContentSegment(
            text=(
                f"○ **섹션 카테고리**: {section.category}\n"
                f"  - 섹션 태그: {', '.join(section.tags) or '(없음)'}\n"
                f"  - 매핑된 Primary 답변: {len(primary_qids)}개\n"
                f"  - 매핑된 Supporting 답변: {len(supporting_qids)}개"
            ),
            source="user_answer" if primary_qids else "llm_inferred",
            source_qids=primary_qids[:3],
        ),
        ContentSegment(
            text=f"○ **작성 지침**: {section.instructions[:150]}...",
            source="llm_inferred",
        ),
        ContentSegment(
            text=(
                f"### 📌 시각 자료 삽입 가이드\n\n"
                f"| 📊 자료 1 삽입 가이드 | 📰 자료 2 삽입 가이드 |\n"
                f"|---|---|\n"
                f"| [가이드] {section.category} 관련 정량 데이터 차트 삽입 | "
                f"[가이드] 본문 주장을 뒷받침하는 3자 근거 자료 삽입 |\n"
                f"| **자료 1 제목**: {section.title} — 핵심 수치 트렌드 | "
                f"**자료 2 제목**: {section.title} — 업계·전문가 검증 자료 |"
            ),
            source="llm_inferred",
        ),
    ]

    if level == "yellow":
        missing = ["시장 규모 출처", "경쟁사 구체 비교"]
        suggestions = [
            InlineSuggestion(
                anchor_text="Mock 모드",
                note="실제 API 모드에서는 이 자리에 Claude 생성 섹션이 들어갑니다.",
                severity="info",
            ),
            InlineSuggestion(
                anchor_text=section.title,
                note=f"{section.category} 섹션 — 정량 수치·출처·경쟁사 실명을 추가로 보완해주세요.",
                severity="warning",
            ),
        ]
    elif level == "red":
        missing = ["핵심 답변 부재", "수치·일정 누락", "팀 구성 불명"]
        suggestions = [
            InlineSuggestion(
                anchor_text="더미 텍스트",
                note="답변이 부족해 이 부분 전체가 추론입니다. 핵심 정보 추가 인터뷰 필요.",
                severity="critical",
            ),
            InlineSuggestion(
                anchor_text=section.category,
                note=f"{section.category} 섹션의 핵심 정보가 답변에 없습니다. 관련 인터뷰 답변을 추가해주세요.",
                severity="critical",
            ),
            InlineSuggestion(
                anchor_text="매핑된 Primary 답변",
                note="관련 답변이 0~1개 수준입니다. 인터뷰 보강 후 재생성 권장.",
                severity="warning",
            ),
        ]

    content_str = "\n\n".join(s.text for s in segments)

    mock_score = {"green": 75, "yellow": 50, "red": 30}[level]
    return SectionResult(
        section_id=section.id,
        section_title=section.title,
        content=content_str,
        confidence_level=level,
        reasoning=f"[MOCK] 더미 응답 (level={level})",
        used_answer_ids=primary_qids[:3],
        missing_info=missing,
        inline_suggestions=suggestions,
        content_segments=segments,
        rubric_check={
            "has_sourced_numbers": level == "green",
            "has_category_gap": level in ("green", "yellow"),
            "has_3_step_logic": level == "green",
            "has_founder_story": level in ("green", "yellow"),
        },
        llm_meta={"model": "mock", "input_tokens": 0, "output_tokens": 0, "duration_ms": 50},
        completion_score=mock_score,
        completion_reasoning=f"[MOCK] level={level} 기반 더미 점수",
    )


def _build_followup_guide(
    followup_questions: list[Question] | None,
    section: FormSection | None = None,
) -> str:
    """초안 생성 시 LLM이 메모(꼬리질문)를 만들 때 참고할 후속 질문 목록.

    section이 주어지면 해당 섹션의 태그·카테고리와 관련된 질문만 필터링해서
    프롬프트 크기를 줄인다.
    """
    if not followup_questions:
        return "(후속 질문 가이드 없음 — LLM 재량으로 꼬리질문 작성)"

    if section is not None:
        sec_tags = set(section.tags)
        sec_cat = section.category.lower()

        def _relevant(q: Question) -> bool:
            tag_match = bool(sec_tags & set(q.tags))
            cat_match = (
                sec_cat in q.category.lower() or q.category.lower() in sec_cat
            )
            return tag_match or cat_match

        filtered = [q for q in followup_questions if _relevant(q)]
        # 관련 질문이 너무 적으면 전체에서 최대 5개 fallback
        if len(filtered) < 3:
            filtered = followup_questions[:5]
    else:
        filtered = followup_questions[:5]

    # 섹션당 최대 5개로 제한
    filtered = filtered[:5]

    by_section: dict[str, list[Question]] = {}
    for q in filtered:
        by_section.setdefault(q.section, []).append(q)

    lines: list[str] = []
    for sec, qs in by_section.items():
        lines.append(f"### {sec}")
        for q in qs:
            lines.append(f"- {q.text}")
        lines.append("")
    return "\n".join(lines).strip()


def _resolve_anchor_texts(result: "SectionResult") -> None:
    """anchor_text를 실제 content와 매칭해 정제.

    1순위: 정확한 포함(in), 2순위: SequenceMatcher 유사도 0.8+, 3순위: "" 처리.
    """
    content = "\n\n".join(s.text for s in result.content_segments) if result.content_segments else result.content
    if not content:
        return
    for sug in result.inline_suggestions:
        if not sug.anchor_text:
            continue
        if sug.anchor_text in content:
            continue
        alen = len(sug.anchor_text)
        if alen < 3 or alen > len(content):
            logger.warning(f"anchor_text 매칭 실패(길이초과): '{sug.anchor_text[:30]}'")
            first_line = next((line.strip() for line in content.split('\n') if line.strip() and len(line.strip()) >= 10), "")
            sug.anchor_text = first_line[:20] if first_line else ""
            continue
        best_ratio = 0.0
        best_window = ""
        for i in range(len(content) - alen + 1):
            window = content[i:i + alen]
            ratio = difflib.SequenceMatcher(None, sug.anchor_text, window).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_window = window
                if best_ratio >= 0.95:
                    break
        if best_ratio >= 0.8:
            logger.debug(f"anchor fuzzy: '{sug.anchor_text[:20]}' → '{best_window[:20]}' (ratio={best_ratio:.2f})")
            sug.anchor_text = best_window
        else:
            logger.warning(f"anchor_text 매칭 실패: '{sug.anchor_text[:30]}' (best={best_ratio:.2f})")
            first_line = next((line.strip() for line in content.split('\n') if line.strip() and len(line.strip()) >= 10), "")
            sug.anchor_text = first_line[:20] if first_line else ""


def evaluate_section(result: "SectionResult", section_id: str, section_title: str) -> dict:
    """심사자 시점 독립 평가 — 작성 방법론과 분리된 피드백 기준 적용."""
    if os.getenv("MOCK_MODE", "0") == "1":
        return {}
    if not _EVAL_CRITERIA_PATH.exists() or not _EVAL_PROMPT_PATH.exists():
        return {}

    criteria = _load_eval_criteria()
    template = _load_eval_prompt()
    content = "\n\n".join(s.text for s in result.content_segments) if result.content_segments else result.content

    # criteria를 system에 고정 → 섹션 간 캐시 히트로 ~1,500 tokens 절감
    system = (
        "당신은 정부지원사업 심사위원입니다. 탈락 근거를 찾는 것이 역할입니다. 반드시 JSON만 반환하세요.\n\n"
        "## 평가 기준 (합격/불합격 패턴 분석 데이터)\n\n" + criteria
    )
    prompt = template.format(
        section_id=section_id,
        section_title=section_title,
        section_content=content,
        evaluation_criteria="(시스템 프롬프트의 평가 기준 참조)",
    )

    try:
        t0 = time.perf_counter()
        text, _ = call_claude(
            system=system,
            user=prompt,
            model="claude-haiku-4-5-20251001",
            max_tokens=3072,
            temperature=0.1,
            purpose="section_evaluation",
            metadata={"section_id": section_id},
            use_cache=True,
        )
        elapsed = time.perf_counter() - t0
        logger.info(f"evaluate_section [{section_id}] 완료: {elapsed:.1f}초")
    except Exception as e:
        logger.error(f"evaluate_section [{section_id}] API 호출 실패: {e}")
        return {}

    try:
        return parse_json_response(text)
    except Exception:
        logger.error(f"evaluate_section JSON 파싱 실패: {section_id}")
        return {}


def apply_eval_result(result: "SectionResult", eval_data: dict) -> None:
    """평가 결과를 SectionResult에 반영 (in-place)."""
    if not eval_data:
        return
    if "confidence_level" in eval_data:
        result.confidence_level = eval_data["confidence_level"]
    if "completion_score" in eval_data:
        try:
            result.completion_score = max(0, min(100, int(eval_data["completion_score"])))
        except (TypeError, ValueError):
            pass
    if "completion_reasoning" in eval_data:
        result.completion_reasoning = eval_data["completion_reasoning"]
    if "rubric_check" in eval_data:
        result.rubric_check = eval_data["rubric_check"]
    # 평가 전용 suggestions를 기존 suggestions에 추가 (중복 anchor 제외)
    existing_anchors = {s.anchor_text for s in result.inline_suggestions}
    for item in eval_data.get("eval_suggestions", []):
        anchor = item.get("anchor_text", "").strip()
        note = item.get("note", "").strip()
        if anchor and note and anchor not in existing_anchors:
            result.inline_suggestions.append(
                InlineSuggestion(
                    anchor_text=anchor,
                    note=f"[심사자 피드백] {note}",
                    severity=item.get("severity", "warning"),
                )
            )
            existing_anchors.add(anchor)
    _resolve_anchor_texts(result)


def evaluate_business_plan(results: list["SectionResult"]) -> list[dict]:
    """전체 사업계획서 전략 평가 — 섹션 간 논리 일관성 및 사업 성립 가능성 평가.

    모든 섹션을 한 번에 Claude에 넘겨 사업 논리·전략적 타당성을 검토.
    반환값: strategic_feedbacks[] — 각 피드백이 어느 섹션(target_section_id)에 달릴지 명시.
    """
    if os.getenv("MOCK_MODE", "0") == "1":
        return []
    if not _STRATEGIC_GUIDE_PATH.exists() or not _STRATEGIC_EVAL_PATH.exists():
        return []
    if not results:
        return []

    strategic_guide = _load_strategic_guide()
    template = _load_strategic_eval()

    # P_judge_feedback_skill(심사위원 빈도순 지적 유형)을 평가 가이드에 병합
    if _JUDGE_SKILL_PATH.exists():
        strategic_guide = strategic_guide + "\n\n---\n\n## 심사위원 빈도순 지적 유형\n\n" + _load_judge_skill()

    sections_blocks = []
    for r in results:
        content = "\n\n".join(s.text for s in r.content_segments) if r.content_segments else r.content
        content = content[:1500]
        sections_blocks.append(f"## [{r.section_id}] {r.section_title}\n\n{content}")
    all_sections_content = "\n\n---\n\n".join(sections_blocks)

    prompt = template.format(
        strategic_guide=strategic_guide,
        all_sections_content=all_sections_content,
    )

    try:
        text, _ = call_claude(
            system="당신은 스타트업 투자심사역이자 시장 전문가입니다. 반드시 JSON만 반환하세요.",
            user=prompt,
            model="claude-haiku-4-5-20251001",
            max_tokens=6144,
            temperature=0.1,
            purpose="strategic_evaluation",
            metadata={"section_count": len(results)},
        )
    except Exception as e:
        logger.error(f"evaluate_business_plan API 호출 실패: {e}")
        return []

    try:
        data = parse_json_response(text)
        return data.get("strategic_feedbacks", [])
    except Exception:
        logger.error("evaluate_business_plan JSON 파싱 실패")
        return []


def attach_strategic_feedbacks(results_map: dict, feedbacks: list[dict]) -> None:
    """전략 피드백을 해당 섹션의 inline_suggestions에 추가 (in-place)."""
    modified: set[str] = set()
    for fb in feedbacks:
        target_id = fb.get("target_section_id", "").strip()
        anchor = fb.get("anchor_text", "").strip()
        note = fb.get("note", "").strip()
        if not target_id or not note:
            continue
        result = results_map.get(target_id)
        if result is None:
            continue
        existing_anchors = {s.anchor_text for s in result.inline_suggestions}
        if anchor and anchor in existing_anchors:
            continue
        result.inline_suggestions.append(
            InlineSuggestion(
                anchor_text=anchor or target_id[:15],
                note=f"[전략 피드백] {note}",
                severity=fb.get("severity", "warning"),
            )
        )
        modified.add(target_id)
    for target_id in modified:
        _resolve_anchor_texts(results_map[target_id])


def generate_section(
    form: Form,
    section: FormSection,
    questions: list[Question],
    answers: dict[str, Answer],
    skills: list[Skill],
    followup_questions: list[Question] | None = None,
    company_context: dict | None = None,
) -> SectionResult:
    # 1. 답변 매핑
    #    - company_context가 주어지면: 정제 항목에서 섹션 매핑된 항목만 추출 → answers_block
    #    - 없으면 (레거시 fallback): 태그 기반 Q/A 원문 매핑
    if company_context:
        ctx_fields = get_section_context_fields(section.category, section.tags)
        answers_block = format_context_block(company_context, ctx_fields)
        primary_qids: list[str] = []
        supporting_qids: list[str] = []
        ctx_field_count = sum(1 for f in ctx_fields if (company_context.get(f) or "").strip())
        logger.info(
            "[%s] context-mode fields=%s filled=%d/%d block_chars=%d",
            section.id, ctx_fields, ctx_field_count, len(ctx_fields), len(answers_block),
        )
    else:
        primary_qids, supporting_qids = map_by_tags(section, questions, answers)
        all_qids = primary_qids + supporting_qids
        answers_block = get_answer_context(all_qids, questions, answers)
        logger.info(
            "[%s] legacy-mode primary=%d supporting=%d block_chars=%d",
            section.id, len(primary_qids), len(supporting_qids), len(answers_block),
        )

    # Mock 모드 조기 반환
    if os.getenv("MOCK_MODE", "0") == "1":
        return _mock_section_result(section, primary_qids, supporting_qids)

    # 2. 통계 루브릭 스코어링 (answers_block 기준)
    rubric = score_text(answers_block)

    # 3. Skill 선택 — 고정 블록(cache prefix)과 가변 블록 분리
    selected = select_skills_for_section(skills, section.category, section.tags)
    skills_block = "\n\n---\n\n".join(s.to_prompt_block() for s in selected)
    # Skills 전체를 cached_user_prefix로 분리: 섹션 간 L1 공통 부분에서 캐시 히트 발생
    skills_cache_prefix = f"## 적용할 작성 방법론 (Skills)\n\n{skills_block}"

    # 4. 프롬프트 조립 — skills_block은 캐시 prefix로 분리되므로 참조 마커만 삽입
    system = _load_system_md()
    template = _load_section_gen()
    followup_guide = _build_followup_guide(followup_questions, section=section)
    feedback_examples = get_feedback_examples(form.program_name, section.tags, section_category=section.category)
    user = template.format(
        program_name=form.program_name,
        target=form.target,
        max_funding=form.max_funding,
        page_limit=form.page_limit,
        notes=form.notes or "(특이사항 없음)",
        section_id=section.id,
        section_title=section.title,
        section_category=section.category,
        section_instructions=section.instructions or "(별도 지시 없음)",
        today_date_note=_build_today_date_note(section.tags),
        skills_block="(→ 위의 캐시 블록에 포함된 Skills 적용)",
        answers_block=answers_block,
        followup_guide=followup_guide,
        feedback_examples=feedback_examples,
    )

    # 5. Claude 호출 — system + skills 블록 양쪽 캐시 적용
    text, meta = call_claude(
        system=system,
        user=user,
        model="claude-haiku-4-5-20251001",
        max_tokens=8192,
        temperature=0.3,
        purpose="section_generation",
        metadata={
            "program_code": form.program_code,
            "section_id": section.id,
            "input_mode": "context" if company_context else "legacy",
        },
        use_cache=True,
        cached_user_prefix=skills_cache_prefix,
    )
    meta["rubric_score"] = {"total": rubric.total, "max_possible": rubric.max_possible}
    if meta.get("stop_reason") == "max_tokens":
        logger.warning(
            "[TRUNCATED] 섹션 %s 응답이 max_tokens로 잘림. 결과 품질 저하 가능.",
            section.id,
        )
        meta["truncated"] = True
    else:
        meta["truncated"] = False
    logger.info(
        "[%s] section_generation done: in=%s out=%s dur=%sms mode=%s",
        section.id, meta.get("input_tokens"), meta.get("output_tokens"),
        meta.get("duration_ms"), "context" if company_context else "legacy",
    )

    # 6. JSON 파싱 (실패 시 본문은 비우고 메타 영역으로만 알림)
    try:
        data = parse_json_response(text)
    except Exception as e:
        return SectionResult(
            section_id=section.id,
            section_title=section.title,
            content="",  # 본문은 비움 (사용자가 고도화·편집으로 해결)
            confidence_level="red",
            reasoning=f"JSON 파싱 실패: {e}",
            missing_info=["LLM 응답 파싱 실패 — '이 섹션 고도화' 또는 '직접 편집' 사용"],
            llm_meta=meta,
            completion_score=0,
            completion_reasoning="LLM 응답 파싱 실패",
        )

    suggestions = []
    for item in data.get("inline_suggestions", []):
        if not isinstance(item, dict):
            continue
        anchor = re.sub(r"\*\*(.+?)\*\*", r"\1", item.get("anchor_text", "").strip())
        anchor = re.sub(r"\*(.+?)\*", r"\1", anchor)
        note = item.get("note", "").strip()
        if not anchor or not note:
            continue
        suggestions.append(
            InlineSuggestion(
                anchor_text=anchor,
                note=note,
                severity=item.get("severity", "warning"),
            )
        )

    # content_segments 파싱
    segments: list[ContentSegment] = []
    for seg in data.get("content_segments", []):
        if not isinstance(seg, dict):
            continue
        txt = _clean_segment_text(seg.get("text", "").strip())
        if not txt:
            continue
        segments.append(
            ContentSegment(
                text=txt,
                source=seg.get("source", "llm_inferred"),
                source_qids=list(seg.get("source_qids", []) or []),
            )
        )

    # segments가 있으면 content를 segments에서 조립, 없으면 data.content 사용
    if segments:
        content_str = "\n\n".join(s.text for s in segments)
    else:
        content_str = data.get("content", "")

    raw_score = data.get("completion_score", 0)
    try:
        completion_score = max(0, min(100, int(raw_score)))
    except (TypeError, ValueError):
        completion_score = 0

    section_result = SectionResult(
        section_id=section.id,
        section_title=section.title,
        content=content_str,
        confidence_level=data.get("confidence_level", "red"),
        reasoning=data.get("reasoning", ""),
        used_answer_ids=data.get("used_answer_ids", []),
        missing_info=data.get("missing_info", []),
        inline_suggestions=suggestions,
        content_segments=segments,
        rubric_check=data.get("rubric_check", {}),
        llm_meta=meta,
        completion_score=completion_score,
        completion_reasoning=data.get("completion_reasoning", ""),
    )
    _resolve_anchor_texts(section_result)
    if meta.get("truncated") and section_result.confidence_level == "green":
        section_result.confidence_level = "yellow"
    return section_result


def regenerate_section(
    form: Form,
    section: FormSection,
    questions: list[Question],
    answers: dict[str, Answer],
    skills: list[Skill],
    previous_result: SectionResult,
    followup_questions: list[Question] | None = None,
    company_context: dict | None = None,
) -> SectionResult:
    """고도화: 기존 생성 결과 + 사용자 메모 답변을 반영하여 섹션 재생성."""
    # 1. 답변 매핑 (context 우선 / 레거시 fallback)
    if company_context:
        ctx_fields = get_section_context_fields(section.category, section.tags)
        answers_block = format_context_block(company_context, ctx_fields)
        primary_qids: list[str] = []
        supporting_qids: list[str] = []
    else:
        primary_qids, supporting_qids = map_by_tags(section, questions, answers)
        all_qids = primary_qids + supporting_qids
        answers_block = get_answer_context(all_qids, questions, answers)

    # Mock 조기 반환
    if os.getenv("MOCK_MODE", "0") == "1":
        r = _mock_section_result(section, primary_qids, supporting_qids)
        # Mock이지만 고도화된 티가 나게 레벨 한 단계 상승 연출
        if r.confidence_level == "red":
            r.confidence_level = "yellow"
            r.completion_score = 55
        elif r.confidence_level == "yellow":
            r.confidence_level = "green"
            r.completion_score = 85
        else:
            r.completion_score = min(100, r.completion_score + 10)
        r.reasoning = f"[MOCK 고도화] 이전 {previous_result.confidence_level} → {r.confidence_level}"
        return r

    # 2. 통계 루브릭 스코어링
    rubric = score_text(answers_block)

    # 3. Skill 선택 — 고정 블록(cache prefix)과 가변 블록 분리
    selected = select_skills_for_section(skills, section.category, section.tags)
    skills_block = "\n\n---\n\n".join(s.to_prompt_block() for s in selected)
    skills_cache_prefix = f"## 적용할 작성 방법론 (Skills)\n\n{skills_block}"

    # 4. 프롬프트 조립 (고도화 전용 — 이전 결과 + 메모 답변 포함)
    system = _load_system_md()
    template = _load_section_gen()
    followup_guide = _build_followup_guide(followup_questions, section=section)
    feedback_examples = get_feedback_examples(form.program_name, section.tags, section_category=section.category)
    base_user = template.format(
        program_name=form.program_name,
        target=form.target,
        max_funding=form.max_funding,
        page_limit=form.page_limit,
        notes=form.notes or "(특이사항 없음)",
        section_id=section.id,
        section_title=section.title,
        section_category=section.category,
        section_instructions=section.instructions or "(별도 지시 없음)",
        today_date_note=_build_today_date_note(section.tags),
        skills_block="(→ 위의 캐시 블록에 포함된 Skills 적용)",
        answers_block=answers_block,
        followup_guide=followup_guide,
        feedback_examples=feedback_examples,
    )

    # 고도화 추가 컨텍스트
    memo_responses_block = "\n".join(
        f"- **[메모 {i+1}]** 앵커: \"{s.anchor_text}\"\n"
        f"  - 원 조언: {s.note}\n"
        f"  - **사용자 보완 답변**: {s.response.strip() or '(답변 없음)'}"
        for i, s in enumerate(previous_result.inline_suggestions)
        if s.response.strip()
    )

    regen_user = f"""{base_user}

---

## ⚡ 고도화(재생성) 요청

아래는 **직전에 생성된 섹션 내용**과 **사용자가 메모에 답한 보완 정보**입니다.
사용자 보완 답변을 **반영하여** 섹션을 다시 작성하세요.

### [이전 생성 내용]
{previous_result.content}

### [사용자 메모 답변]
{memo_responses_block or '(메모 답변 없음 — 그래도 기존 내용 개선 시도)'}

### 재생성 원칙
- **핵심 규칙: 최소한의 수정만 허용**
- 사용자 보완 답변이 있는 메모의 anchor_text가 포함된 단락만 수정
- 해당 단락 외 나머지 본문은 글자 하나도 바꾸지 말고 그대로 유지
- 수정 방법: 해당 단락에 사용자 답변 내용을 자연스럽게 녹여서 보강
- content_segments도 수정된 단락만 변경하고 나머지 segments는 이전 내용 그대로 유지
- inline_suggestions는 수정된 단락의 메모만 제거하고 나머지 메모는 모두 유지
- 메모 답변이 없으면 (memo_responses_block이 비어있으면) 본문을 전혀 수정하지 말고 이전 내용 그대로 반환
- confidence_level은 메모 해소 개수에 비례해서 상승 (1개 해소: 유지 또는 한 단계 상승, 전체 해소: 한 단계 이상 상승)

JSON 스키마는 동일. 반드시 JSON만 반환.
"""

    text, meta = call_claude(
        system=system,
        user=regen_user,
        model="claude-haiku-4-5-20251001",
        max_tokens=8192,
        temperature=0.3,
        purpose="section_regeneration",
        metadata={
            "program_code": form.program_code,
            "section_id": section.id,
            "previous_level": previous_result.confidence_level,
        },
        use_cache=True,
        cached_user_prefix=skills_cache_prefix,
    )
    meta["rubric_score"] = {"total": rubric.total, "max_possible": rubric.max_possible}
    if meta.get("stop_reason") == "max_tokens":
        logger.warning(
            "[TRUNCATED] 섹션 %s 재생성 응답이 max_tokens로 잘림. 결과 품질 저하 가능.",
            section.id,
        )
        meta["truncated"] = True
    else:
        meta["truncated"] = False

    try:
        data = parse_json_response(text)
    except Exception as e:
        # 재생성 실패 — 원본 유지하고 reasoning만 업데이트
        fail = SectionResult(
            section_id=section.id,
            section_title=section.title,
            content=previous_result.content,
            confidence_level=previous_result.confidence_level,
            reasoning=f"⚠ 재생성 실패 ({type(e).__name__}) — 원본 유지. 다시 시도해주세요.",
            used_answer_ids=previous_result.used_answer_ids,
            missing_info=["재생성 파싱 실패 — 다시 시도"] + list(previous_result.missing_info),
            inline_suggestions=previous_result.inline_suggestions,
            content_segments=previous_result.content_segments,
            rubric_check=previous_result.rubric_check,
            llm_meta=meta,
            completion_score=previous_result.completion_score,
            completion_reasoning=previous_result.completion_reasoning,
        )
        return fail

    # 파싱 (generate_section 동일 로직 재사용)
    suggestions = [
        InlineSuggestion(
            anchor_text=item.get("anchor_text", "").strip(),
            note=item.get("note", "").strip(),
            severity=item.get("severity", "warning"),
        )
        for item in data.get("inline_suggestions", [])
        if isinstance(item, dict) and item.get("anchor_text") and item.get("note")
    ]

    segments = [
        ContentSegment(
            text=_clean_segment_text(seg.get("text", "").strip()),
            source=seg.get("source", "llm_inferred"),
            source_qids=list(seg.get("source_qids", []) or []),
        )
        for seg in data.get("content_segments", [])
        if isinstance(seg, dict) and seg.get("text")
    ]

    content_str = "\n\n".join(s.text for s in segments) if segments else data.get("content", "")

    raw_score = data.get("completion_score", previous_result.completion_score)
    try:
        completion_score = max(0, min(100, int(raw_score)))
    except (TypeError, ValueError):
        completion_score = previous_result.completion_score

    regen_result = SectionResult(
        section_id=section.id,
        section_title=section.title,
        content=content_str,
        confidence_level=data.get("confidence_level", previous_result.confidence_level),
        reasoning=data.get("reasoning", ""),
        used_answer_ids=data.get("used_answer_ids", []),
        missing_info=data.get("missing_info", []),
        inline_suggestions=suggestions,
        content_segments=segments,
        rubric_check=data.get("rubric_check", {}),
        llm_meta=meta,
        completion_score=completion_score,
        completion_reasoning=data.get("completion_reasoning", ""),
    )
    _resolve_anchor_texts(regen_result)
    if meta.get("truncated") and regen_result.confidence_level == "green":
        regen_result.confidence_level = "yellow"
    return regen_result
