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
_EVAL_CRITERIA_PATH = Path(__file__).resolve().parent.parent / "skills" / "L2_section" / "S04_item_keyword_strategy.md"
_EVAL_PROMPT_PATH = _PROMPTS_DIR / "section_evaluation.md"
_STRATEGIC_GUIDE_PATH = _PROMPTS_DIR / "strategic_feedback_guide.md"
_STRATEGIC_EVAL_PATH = _PROMPTS_DIR / "strategic_evaluation.md"
_FRAMEWORK_GEN_PATH = _PROMPTS_DIR / "framework_generation.md"
_FORM_CONV_PATH = _PROMPTS_DIR / "form_conversion.md"
_FORM_REARRANGE_PATH = _PROMPTS_DIR / "form_rearrange.md"

# 프레임워크 섹션 정의 (양식 무관 기본 구조)
FRAMEWORK_SECTIONS: list[dict] = [
    {"id": "1-1", "title": "외적 동기",                "parent_title": "1. 개발 동기 및 현황", "category": "Problem",  "tags": ["개발동기"]},
    {"id": "1-2", "title": "내적 동기",                "parent_title": "1. 개발 동기 및 현황", "category": "Problem",  "tags": ["개발동기"]},
    {"id": "1-3", "title": "필요성",                   "parent_title": "1. 개발 동기 및 현황", "category": "Problem",  "tags": ["개발동기"]},
    {"id": "2-1", "title": "시장 분석",                "parent_title": "2. 실현가능성",         "category": "Solution", "tags": ["시장분석"]},
    {"id": "2-2", "title": "아이템 기술 및 고도화 방안", "parent_title": "2. 실현가능성",         "category": "Solution", "tags": ["차별성", "개발준비"]},
    {"id": "2-3", "title": "추진성과",                 "parent_title": "2. 실현가능성",         "category": "Solution", "tags": ["BM"]},
    {"id": "3-1", "title": "추진 전략",                "parent_title": "3. 성장 전략",          "category": "Scale-up", "tags": ["사업화전략"]},
    {"id": "3-2", "title": "자금 계획",                "parent_title": "3. 성장 전략",          "category": "Scale-up", "tags": ["일정자금"]},
    {"id": "4-1", "title": "기업 구성",                "parent_title": "4. 기업 구성",          "category": "Team",     "tags": ["팀역량"]},
]

# 모듈 레벨 파일 캐시 — 프로세스 재시작 전까지 디스크 재독 없음
_cache_system_md: str | None = None
_cache_section_gen: str | None = None
_cache_eval_criteria: str | None = None
_cache_eval_prompt: str | None = None
_cache_strategic_guide: str | None = None
_cache_strategic_eval: str | None = None
_cache_framework_gen: str | None = None
_cache_form_conv: str | None = None
_cache_form_rearrange: str | None = None



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
            sug.anchor_text = ""
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
            sug.anchor_text = ""


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


def evaluate_business_plan(results: list["SectionResult"], company_context: dict | None = None) -> list[dict]:
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

    # (P_judge_feedback_skill은 MASTER_SKILL.md에 통합됨 — 별도 병합 불필요)

    sections_blocks = []
    for r in results:
        # 핵심 주장만 추출 (■ 소제목)
        if r.content_segments:
            headlines = [
                line.strip()
                for seg in r.content_segments
                for line in seg.text.split("\n")
                if line.strip().startswith("■")
            ]
            core_summary = "\n".join(headlines[:5])
        else:
            core_summary = r.content[:300]

        # critical 메모 추출
        critical_notes = [
            f"  - [{s.severity}] {s.note[:80]}"
            for s in r.inline_suggestions
            if s.severity == "critical"
        ][:3]
        critical_block = "\n".join(critical_notes) if critical_notes else ""

        block = f"## [{r.section_id}] {r.section_title} ({r.confidence_level}, {r.completion_score}점)\n{core_summary}"
        if critical_block:
            block += f"\n주요 보완 필요:\n{critical_block}"
        sections_blocks.append(block)

    all_sections_content = "\n\n---\n\n".join(sections_blocks)

    # company_context가 있으면 사업 요약을 앞에 추가
    if company_context:
        ctx_fields = ["문제인식", "솔루션", "시장규모", "비즈니스모델", "팀구성", "경쟁우위"]
        ctx_lines = []
        for f in ctx_fields:
            v = (company_context.get(f) or "").strip()
            if v:
                ctx_lines.append(f"- {f}: {v[:150]}")
        if ctx_lines:
            ctx_block = "## 사업 요약 (인터뷰 기반)\n" + "\n".join(ctx_lines)
            all_sections_content = ctx_block + "\n\n---\n\n" + all_sections_content

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
        if not anchor:
            continue
        result.inline_suggestions.append(
            InlineSuggestion(
                anchor_text=anchor,
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
    llm_suggestions = [
        InlineSuggestion(
            anchor_text=item.get("anchor_text", "").strip(),
            note=item.get("note", "").strip(),
            severity=item.get("severity", "warning"),
        )
        for item in data.get("inline_suggestions", [])
        if isinstance(item, dict) and item.get("anchor_text") and item.get("note")
    ]
    # 답변 없는 메모는 이전 결과에서 그대로 유지
    answered_anchors = {s.anchor_text for s in previous_result.inline_suggestions if s.response.strip()}
    kept_suggestions = [s for s in previous_result.inline_suggestions if not s.response.strip()]
    suggestions = kept_suggestions + [s for s in llm_suggestions if s.anchor_text not in {k.anchor_text for k in kept_suggestions}]

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


# ──────────────────────────────────────────────
# 프레임워크 초안 생성 (양식 무관)
# ──────────────────────────────────────────────

def _load_framework_gen() -> str:
    global _cache_framework_gen
    if _cache_framework_gen is None:
        _cache_framework_gen = _FRAMEWORK_GEN_PATH.read_text(encoding="utf-8")
    return _cache_framework_gen


def generate_framework_section(
    section: dict,
    questions: list[Question],
    answers: dict[str, Answer],
    skills: list[Skill],
    company_context: dict | None = None,
    extra_instruction: str = "",
) -> SectionResult:
    """단일 프레임워크 섹션 생성 (양식 무관).

    Args:
        section: FRAMEWORK_SECTIONS 항목 {"id", "title", "parent_title", "category", "tags"}
        questions: 인터뷰 질문 목록
        answers: 인터뷰 답변 딕셔너리
        skills: 로드된 스킬 목록
        company_context: extract_company_context() 결과
        extra_instruction: feedback_agent 검수 미달 시 재작성 지침 (재생성 호출에서만 사용)
    """
    section_id = section["id"]
    section_title = section["title"]
    parent_title = section["parent_title"]
    category = section["category"]
    tags = section.get("tags", [])

    # 컨텍스트 블록 구성
    if company_context:
        ctx_fields = get_section_context_fields(category, tags)
        answers_block = format_context_block(company_context, ctx_fields)
        primary_qids: list[str] = []
    else:
        primary_qids, supporting_qids = map_by_tags(
            type("_S", (), {"category": category, "tags": tags, "id": section_id})(),
            questions,
            answers,
        )
        answers_block = get_answer_context(primary_qids + supporting_qids, questions, answers)

    if os.getenv("MOCK_MODE", "0") == "1":
        return SectionResult(
            section_id=section_id,
            section_title=section_title,
            content=f"[MOCK] {section_title} 프레임워크 초안",
            confidence_level="yellow",
            reasoning="[MOCK] 프레임워크 생성 더미",
            used_answer_ids=primary_qids[:3],
            completion_score=50,
        )

    # 스킬 블록
    selected = select_skills_for_section(skills, category, tags)
    skills_block = "\n\n---\n\n".join(s.to_prompt_block() for s in selected)
    skills_cache_prefix = f"## 적용할 작성 방법론 (DRAFT_WRITING_GUIDE)\n\n{skills_block}"

    # 날짜 노트 (자금계획 섹션에만)
    today_date_note = _build_today_date_note(tags)

    system = _load_system_md()
    template = _load_framework_gen()
    user = template.format(
        section_id=section_id,
        section_title=section_title,
        parent_title=parent_title,
        skills_block="(→ 위의 캐시 블록에 포함된 DRAFT_WRITING_GUIDE 적용)",
        answers_block=answers_block,
        today_date_note=today_date_note,
    )

    # feedback_agent 검수 미달 → 재생성 시 재작성 지침 첨부
    if extra_instruction:
        user = f"{user}\n\n## ⚠️ 검수 피드백 — 아래 재작성 지침을 반드시 반영할 것\n\n{extra_instruction}"

    text, meta = call_claude(
        system=system,
        user=user,
        model="claude-haiku-4-5-20251001",
        max_tokens=8192,
        temperature=0.3,
        purpose="framework_section_generation",
        metadata={"section_id": section_id},
        use_cache=True,
        cached_user_prefix=skills_cache_prefix,
    )

    if meta.get("stop_reason") == "max_tokens":
        meta["truncated"] = True
    else:
        meta["truncated"] = False

    try:
        data = parse_json_response(text)
    except Exception as e:
        return SectionResult(
            section_id=section_id,
            section_title=section_title,
            content="",
            confidence_level="red",
            reasoning=f"JSON 파싱 실패: {e}",
            missing_info=["LLM 응답 파싱 실패"],
            llm_meta=meta,
            completion_score=0,
        )

    suggestions = [
        InlineSuggestion(
            anchor_text=re.sub(r"\*\*(.+?)\*\*", r"\1", item.get("anchor_text", "").strip()),
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

    try:
        completion_score = max(0, min(100, int(data.get("completion_score", 0))))
    except (TypeError, ValueError):
        completion_score = 0

    result = SectionResult(
        section_id=section_id,
        section_title=section_title,
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
    _resolve_anchor_texts(result)
    if meta.get("truncated") and result.confidence_level == "green":
        result.confidence_level = "yellow"
    return result


def _apply_feedback_gate(
    section: dict,
    result: SectionResult,
    questions: list[Question],
    answers: dict[str, Answer],
    skills: list[Skill],
    company_context: dict | None,
) -> SectionResult:
    """feedback_agent 검수 게이트 — 기준 미달 시 retry_instruction으로 1회 재생성.

    검수/재생성 실패 시 원본 초안을 그대로 반환 (게이트는 품질 보강용, 차단용 아님).
    """
    if os.getenv("MOCK_MODE", "0") == "1" or not result.content:
        return result

    try:
        from feedback_agent import ReviewRequest, review_section
    except ImportError:
        logger.warning("[피드백 게이트] feedback_agent 미설치 — 검수 생략")
        return result

    import json as _gate_json

    try:
        interview_context = (
            _gate_json.dumps(company_context, ensure_ascii=False) if company_context else ""
        )
        review = review_section(ReviewRequest(
            draft_content=result.content,
            section_id=section["id"],
            section_category="",  # 빈 값 → feedback_agent의 SECTION_CATEGORY_MAP이 ID 기반 자동 매핑
            interview_context=interview_context,
        ))
    except Exception as e:
        logger.warning("[피드백 게이트] 검수 실패(%s): %s — 원본 초안 유지", section["id"], e)
        return result

    if review.passed:
        return result

    logger.info(
        "[피드백 게이트] %s 기준 미달 → 재생성 (누락 헤더 %d개, 미충족 기준 %d개)",
        section["id"], len(review.missing_headers), len(review.failed_criteria),
    )
    try:
        retry = generate_framework_section(
            section, questions, answers, skills, company_context,
            extra_instruction=review.retry_instruction,
        )
        if retry.content:
            return retry
    except Exception as e:
        logger.warning("[피드백 게이트] 재생성 실패(%s): %s — 원본 초안 유지", section["id"], e)
    return result


def generate_framework_draft(
    questions: list[Question],
    answers: dict[str, Answer],
    skills: list[Skill],
    company_context: dict | None = None,
) -> list[SectionResult]:
    """모든 프레임워크 섹션 생성 (첫 섹션 단독 → 나머지 병렬).

    각 섹션은 생성 직후 feedback_agent 검수 게이트를 통과 — 기준 미달 시 1회 재생성.

    Returns:
        FRAMEWORK_SECTIONS 순서대로 정렬된 SectionResult 목록
    """
    import concurrent.futures

    results: list[SectionResult | None] = [None] * len(FRAMEWORK_SECTIONS)

    def _gen(idx: int, sec: dict) -> tuple[int, SectionResult]:
        r = generate_framework_section(sec, questions, answers, skills, company_context)
        r = _apply_feedback_gate(sec, r, questions, answers, skills, company_context)
        return idx, r

    # 첫 섹션 단독 생성 (캐시 워밍)
    _, first_result = _gen(0, FRAMEWORK_SECTIONS[0])
    results[0] = first_result

    # 나머지 병렬 생성
    if len(FRAMEWORK_SECTIONS) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(_gen, i, sec): i
                for i, sec in enumerate(FRAMEWORK_SECTIONS[1:], start=1)
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    idx, result = future.result()
                    results[idx] = result
                except Exception as e:
                    idx = futures[future]
                    sec = FRAMEWORK_SECTIONS[idx]
                    logger.error("[프레임워크 섹션 실패] %s: %s", sec["id"], e)
                    results[idx] = SectionResult(
                        section_id=sec["id"],
                        section_title=sec["title"],
                        content="",
                        confidence_level="red",
                        reasoning=f"생성 실패: {e}",
                        missing_info=["섹션 생성 오류 — 재시도 필요"],
                        completion_score=0,
                    )

    return [r for r in results if r is not None]


# ──────────────────────────────────────────────
# 양식 변환 (프레임워크 초안 → 선택 양식 섹션)
# ──────────────────────────────────────────────

def _load_form_conv() -> str:
    global _cache_form_conv
    if _cache_form_conv is None:
        _cache_form_conv = _FORM_CONV_PATH.read_text(encoding="utf-8")
    return _cache_form_conv


def _load_form_rearrange() -> str:
    global _cache_form_rearrange
    if _cache_form_rearrange is None:
        _cache_form_rearrange = _FORM_REARRANGE_PATH.read_text(encoding="utf-8")
    return _cache_form_rearrange


def convert_to_form(
    framework_results: list[SectionResult],
    form: Form,
    skills: list[Skill] | None = None,
) -> list[SectionResult]:
    """프레임워크 초안 → 선택한 양식 섹션 구조로 변환.

    Args:
        framework_results: generate_framework_draft()가 반환한 SectionResult 목록
        form: 변환 대상 Form (load_form()으로 로드)
        skills: 로드된 스킬 목록 (없으면 빈 리스트)
    """
    import concurrent.futures

    if skills is None:
        skills = []

    # 1. 프레임워크 초안 9개 섹션을 단일 텍스트로 이어붙임
    framework_parts = [
        f"## {r.section_title}\n\n{r.display_content()}"
        for r in framework_results
    ]
    framework_context = "\n\n---\n\n".join(framework_parts)

    system = _load_system_md()
    template = _load_form_conv()

    results: list[SectionResult | None] = [None] * len(form.sections)

    def _convert_one(idx: int, section: FormSection) -> tuple[int, SectionResult]:
        if os.getenv("MOCK_MODE", "0") == "1":
            return idx, _mock_section_result(section, [], [])

        selected = select_skills_for_section(skills, section.category, section.tags) if skills else []
        skills_block = "\n\n---\n\n".join(s.to_prompt_block() for s in selected)
        skills_cache_prefix = f"## 적용할 작성 방법론 (Skills)\n\n{skills_block}" if skills_block else ""

        today_date_note = _build_today_date_note(section.tags)

        user = template.format(
            framework_context=framework_context,
            section_id=section.id,
            section_title=section.title,
            section_instructions=section.instructions or "(별도 지시 없음)",
            skills_block="(→ 위의 캐시 블록에 포함된 Skills 적용)" if skills_block else "(Skills 없음)",
            today_date_note=today_date_note,
        )

        call_kwargs: dict = dict(
            system=system,
            user=user,
            model="claude-haiku-4-5-20251001",
            max_tokens=8192,
            temperature=0.3,
            purpose="form_conversion",
            metadata={"program_code": form.program_code, "section_id": section.id},
            use_cache=True,
        )
        if skills_cache_prefix:
            call_kwargs["cached_user_prefix"] = skills_cache_prefix

        text, meta = call_claude(**call_kwargs)

        meta["truncated"] = meta.get("stop_reason") == "max_tokens"
        if meta["truncated"]:
            logger.warning("[TRUNCATED] 양식 변환 섹션 %s 응답이 max_tokens로 잘림.", section.id)

        try:
            data = parse_json_response(text)
        except Exception as e:
            return idx, SectionResult(
                section_id=section.id,
                section_title=section.title,
                content="",
                confidence_level="red",
                reasoning=f"JSON 파싱 실패: {e}",
                missing_info=["LLM 응답 파싱 실패"],
                llm_meta=meta,
                completion_score=0,
            )

        suggestions = [
            InlineSuggestion(
                anchor_text=re.sub(r"\*\*(.+?)\*\*", r"\1", item.get("anchor_text", "").strip()),
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

        try:
            completion_score = max(0, min(100, int(data.get("completion_score", 0))))
        except (TypeError, ValueError):
            completion_score = 0

        result = SectionResult(
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
        _resolve_anchor_texts(result)
        if meta.get("truncated") and result.confidence_level == "green":
            result.confidence_level = "yellow"
        return idx, result

    # 첫 섹션 단독 생성 (캐시 워밍) → 나머지 병렬
    _, first = _convert_one(0, form.sections[0])
    results[0] = first

    if len(form.sections) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(_convert_one, i, sec): i
                for i, sec in enumerate(form.sections[1:], start=1)
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    idx, result = future.result()
                    results[idx] = result
                except Exception as e:
                    idx = futures[future]
                    sec = form.sections[idx]
                    logger.error("[양식 변환 섹션 실패] %s: %s", sec.id, e)
                    results[idx] = SectionResult(
                        section_id=sec.id,
                        section_title=sec.title,
                        content="",
                        confidence_level="red",
                        reasoning=f"변환 실패: {e}",
                        missing_info=["섹션 변환 오류 — 재시도 필요"],
                        completion_score=0,
                    )

    return [r for r in results if r is not None]


def convert_to_form_v2(
    framework_results: list[SectionResult],
    form: Form,
) -> list[SectionResult]:
    """재배치 방식: LLM 1회 매핑 결정 → 프레임워크 초안 내용 복붙.

    v1(convert_to_form)과 달리 섹션별 LLM 재호출 없음.
    동일 입력으로 v1과 결과물을 비교하기 위한 성능 테스트용 함수.
    """
    import json as _json

    # 1. 프레임워크 섹션 목록 정리
    framework_meta = [
        {
            "id": r.section_id,
            "title": r.section_title,
            "category": next(
                (s["category"] for s in FRAMEWORK_SECTIONS if s["id"] == r.section_id), ""
            ),
            "tags": next(
                (s["tags"] for s in FRAMEWORK_SECTIONS if s["id"] == r.section_id), []
            ),
        }
        for r in framework_results
    ]

    # 2. 양식 섹션 목록 정리
    form_meta = [
        {
            "id": s.id,
            "title": s.title,
            "category": s.category,
            "tags": s.tags,
        }
        for s in form.sections
    ]

    # 3. LLM 1회 호출 — 매핑 결정
    template = _load_form_rearrange()
    user_prompt = template.format(
        framework_sections=_json.dumps(framework_meta, ensure_ascii=False, indent=2),
        form_sections=_json.dumps(form_meta, ensure_ascii=False, indent=2),
    )

    mapping_text, mapping_meta = call_claude(
        system=_load_system_md(),
        user=user_prompt,
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        temperature=0.1,
        purpose="form_rearrange_mapping",
        metadata={"program_code": form.program_code},
    )

    try:
        mapping: dict[str, list[str]] = parse_json_response(mapping_text)
    except Exception as e:
        logger.error("[재배치 매핑 파싱 실패] %s", e)
        mapping = {}

    # 4. 프레임워크 섹션 ID → SectionResult 조회 맵
    framework_map = {r.section_id: r for r in framework_results}

    # 5. 매핑대로 내용 복붙 → SectionResult 생성
    results: list[SectionResult] = []
    for section in form.sections:
        assigned_ids: list[str] = mapping.get(section.id, [])
        assigned_results = [framework_map[fid] for fid in assigned_ids if fid in framework_map]

        if assigned_results:
            parts = []
            all_segments: list[ContentSegment] = []
            for fw in assigned_results:
                parts.append(fw.display_content())
                all_segments.extend(fw.content_segments)

            content = "\n\n".join(parts)
            confidence = "green"
            completion = 70
        else:
            content = ""
            all_segments = []
            confidence = "red"
            completion = 0

        result = SectionResult(
            section_id=section.id,
            section_title=section.title,
            content=content,
            confidence_level=confidence,
            reasoning=f"재배치: {assigned_ids}" if assigned_ids else "매핑된 섹션 없음",
            used_answer_ids=[],
            missing_info=[] if content else ["매핑된 프레임워크 섹션 없음"],
            inline_suggestions=[],
            content_segments=all_segments,
            llm_meta={"method": "rearrange", "mapped_from": assigned_ids, "mapping_meta": mapping_meta},
            completion_score=completion,
        )
        results.append(result)

    return results
