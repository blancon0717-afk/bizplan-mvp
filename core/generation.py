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
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from core.context_extraction import (
    format_context_block,
    get_section_context_fields,
)
from core.notion_feedback import get_evaluation_examples, get_feedback_examples
from core.forms import Form, FormSection
from core.interview import Answer, Question
from core.llm import call_claude, parse_json_response
from core.mapping import get_answer_context, map_by_tags
from core.rubric_scorer import score_text
from core.skills import Skill, select_skills_for_section

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_SCHEDULE_TAG = "일정자금"
# 로드맵·추진일정·협약기간 등 연도·시점 표기가 들어가는 섹션 태그 전체.
# 일정자금(3-2) 외에 사업화전략(3-1: 로드맵·KPI·추진일정), 차별성·개발준비(2-2: 고도화 로드맵·IP)도 포함.
_DATE_AWARE_TAGS: frozenset[str] = frozenset({"일정자금", "사업화전략", "차별성", "개발준비"})


def _build_today_date_note(tags: list[str]) -> str:
    """날짜 표기가 들어가는 섹션에만 작성 기준일 규칙을 반환."""
    if not (set(tags) & _DATE_AWARE_TAGS):
        return ""
    now = datetime.now()
    today = now.strftime("%Y년 %m월")
    # 협약기간(정부지원사업 수행기간) 연도: 작성 1~4월 → 해당 연도 / 5~12월 → 다음 연도 (5~12월 수행)
    contract_year = now.year if now.month <= 4 else now.year + 1
    return (
        f"\n> ⏰ **작성 기준일 규칙** (사업계획서 작성일: {today})\n"
        f"> - 로드맵·추진일정 등 향후 계획의 연도·시점 표기는 {today} 기준으로 계산\n"
        f"> - 협약기간(정부지원사업 수행기간): **양식 작성 지시사항에 협약기간·월 범위가 명시되어 있으면 "
        f"그 기간을 그대로 따르고**, 명시가 없을 때만 {contract_year}년 5월~12월로 작성\n"
        f"> - 향후 일정에 {today} 이전(작성일 이전) 날짜 사용 금지\n"
        f"> - 단, 이미 일어난 실제 이력(연혁·매출 실적·기등록 특허의 등록일 등)은 실제 과거 날짜를 그대로 사용\n"
        f"> - 본문 내 Good Example의 연도(예: '25, 2025년 등)는 형식 참고용일 뿐, "
        f"실제 연도는 반드시 이 규칙에 따라 재계산할 것\n"
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
# 심사위원 평가 기준·사례는 노션 데이터(core.notion_feedback)에서만 가져온다.
# (구버전 _EVAL_CRITERIA_PATH = skills/L2_section/S04... 는 노션 전환으로 제거됨)
_EVAL_PROMPT_PATH = _PROMPTS_DIR / "section_evaluation.md"
_STRATEGIC_GUIDE_PATH = _PROMPTS_DIR / "strategic_feedback_guide.md"
_STRATEGIC_EVAL_PATH = _PROMPTS_DIR / "strategic_evaluation.md"
_FRAMEWORK_GEN_PATH = _PROMPTS_DIR / "framework_generation.md"
_FORM_CONV_PATH = _PROMPTS_DIR / "form_conversion.md"
_FORM_REARRANGE_PATH = _PROMPTS_DIR / "form_rearrange.md"

# 프레임워크 섹션 정의 (양식 무관 기본 구조)
# role: 병렬 생성 모드에서 섹션 간 중복·침범을 막는 역할 경계 (담당 범위 | 서술 금지 범위)
FRAMEWORK_SECTIONS: list[dict] = [
    {"id": "1-1", "title": "외적 동기",                "parent_title": "1. 개발 동기 및 현황", "category": "Problem",  "tags": ["개발동기"],
     "role": "사회·경제·기술 관점의 시장 문제점과 기회만 다룬다. 대표자 개인 경험·서사(1-2 담당), 시장 규모 산정(2-1 담당), 솔루션 기능 상세(2-2 담당)는 서술 금지."},
    {"id": "1-2", "title": "내적 동기",                "parent_title": "1. 개발 동기 및 현황", "category": "Problem",  "tags": ["개발동기"],
     "role": "대표자·팀의 경험, 역량, 창업 계기 등 내부적 동기만 다룬다. 시장 통계·외부 환경(1-1 담당), 팀 전체 구성 상세(4-1 담당)는 서술 금지."},
    {"id": "1-3", "title": "필요성",                   "parent_title": "1. 개발 동기 및 현황", "category": "Problem",  "tags": ["개발동기"],
     "role": "기존 대안(경쟁 제품·서비스)의 한계와 해결 필요성 논증만 다룬다. 자사 솔루션 기능 상세(2-2 담당), 이미 달성한 성과(2-3 담당)는 서술 금지."},
    {"id": "2-1", "title": "시장 분석",                "parent_title": "2. 실현가능성",         "category": "Solution", "tags": ["시장분석"],
     "role": "TAM/SAM/SOM 등 시장 규모·성장성·타깃 고객 정의만 다룬다. 문제 서사(1-x 담당), 시장 진입 전략(3-1 담당)은 서술 금지."},
    {"id": "2-2", "title": "아이템 기술 및 고도화 방안", "parent_title": "2. 실현가능성",         "category": "Solution", "tags": ["차별성", "개발준비"],
     "role": "제품·서비스의 기능·기술·개발 계획·차별성만 다룬다. 시장 규모 수치(2-1 담당), 매출·자금 계획(3-2 담당)은 서술 금지."},
    {"id": "2-3", "title": "추진성과",                 "parent_title": "2. 실현가능성",         "category": "Solution", "tags": ["BM"],
     "role": "이미 달성한 정량·정성 성과(사용자 지표, MOU, 투자 등)만 다룬다. 향후 계획·전략(3-1, 3-2 담당)은 서술 금지."},
    {"id": "3-1", "title": "추진 전략",                "parent_title": "3. 성장 전략",          "category": "Scale-up", "tags": ["사업화전략"],
     "role": "향후 시장 진입·확장 전략과 수익모델 실행 계획만 다룬다. 과거 성과(2-3 담당), 자금 수치 상세(3-2 담당)는 서술 금지."},
    {"id": "3-2", "title": "자금 계획",                "parent_title": "3. 성장 전략",          "category": "Scale-up", "tags": ["일정자금"],
     "role": "자금 소요·조달·집행 계획과 재무 목표만 다룬다. 마케팅·전략 서사(3-1 담당)는 서술 금지."},
    {"id": "4-1", "title": "기업 구성",                "parent_title": "4. 기업 구성",          "category": "Team",     "tags": ["팀역량"],
     "role": "팀 구성·보유 역량·채용 계획만 다룬다. 대표자의 창업 동기 서사(1-2 담당)는 서술 금지."},
]


def build_parallel_prior_note(section: dict) -> str:
    """병렬 생성 모드용 역할 경계 노트 — prior_context 슬롯에 주입.

    순차 모드의 누적 컨텍스트를 대체해 섹션 간 중복·침범을 구조적으로 차단한다.
    (양식 변환 v3의 소스 분리 기법과 동일한 접근 — 중복 0건 실측 검증됨)
    """
    role = section.get("role", "")
    if not role:
        return ""
    return (
        "(병렬 작성 모드 — 다른 섹션들이 동시에 작성되고 있음)\n"
        f"이 섹션의 담당 범위: {role}\n"
        "서술 금지로 표시된 내용은 다른 섹션이 다루므로, 필요한 경우에도 한 문장 이내의 "
        "연결 언급만 허용하고 상세 서술은 절대 하지 않는다."
    )

# 섹션 생성 순서 분류
# Problem(1-x) + Solution(2-x) + Scale-up(3-x): 전체 누적 컨텍스트로 순차 생성
# Team(4-1): 인터뷰 내용 기반 병렬 생성 (앞 내용과 독립적)
_SEQUENTIAL_IDS: frozenset[str] = frozenset({"1-1", "1-2", "1-3", "2-1", "2-2", "2-3", "3-1", "3-2"})
_PARALLEL_IDS: frozenset[str] = frozenset({"4-1"})

# 혁신바우처(innovation_voucher) 전용 — 사용자가 선택하는 바우처 서비스.
# 선택값은 3·4·5·6번 섹션 변환 프롬프트에 주입되어, 선택 서비스만 작성하도록 제한한다.
_VOUCHER_SERVICES: frozenset[str] = frozenset({"컨설팅", "기술지원", "마케팅"})
_VOUCHER_LINKED_SECTION_IDS: frozenset[str] = frozenset({"3", "4", "5", "6"})

# 분야별 최대 지원금액(정부지원금 기준, 고정값) — 2026년 1차 공고 [일반 바우처]
#   컨설팅  : AX·DX 컨설팅 50백만원 = 5,000만원
#   기술지원: 시제품 제작 30백만원 = 3,000만원
#   마케팅  : 디자인/홍보 20백만원 = 2,000만원
# 기업별 정부지원금 총 한도는 5,000만원(50백만원).
_VOUCHER_SERVICE_MAX_FUNDING: dict[str, str] = {
    "컨설팅": "5,000만원",
    "기술지원": "3,000만원",
    "마케팅": "2,000만원",
}


def _build_voucher_note(voucher_options: list[str] | None) -> str:
    """선택된 바우처 서비스를 변환 프롬프트에 주입할 안내문 생성.

    화이트리스트(_VOUCHER_SERVICES)에 포함된 값만 반영해 프롬프트 인젝션을 차단한다.
    각 서비스의 분야별 최대 지원금액(고정값)을 함께 주입해 현실적인 예산 서술을 돕는다.
    유효한 선택이 없으면 빈 문자열 반환(→ 섹션 지시사항 원본 그대로, 3개 분야 모두 작성).
    """
    if not voucher_options:
        return ""
    selected_set = set(voucher_options)
    order = ("컨설팅", "기술지원", "마케팅")
    selected = [s for s in order if s in selected_set and s in _VOUCHER_SERVICES]
    if not selected:
        return ""
    excluded = [s for s in order if s not in selected]
    selected_with_funding = ", ".join(
        f"{s}(최대 {_VOUCHER_SERVICE_MAX_FUNDING[s]})" for s in selected
    )
    note = (
        f"\n\n【최우선 규칙 — 사용자가 선택한 혁신바우처 서비스】: {selected_with_funding}\n"
        "→ 본문(◦/- 서술)과 표 모두 위 선택된 서비스에 대해서만 작성한다.\n"
        "→ 아래 지시사항·예시 표에 다른 분야가 보여도, 선택되지 않은 분야의 행·문단은 절대 작성하지 말 것."
    )
    if excluded:
        note += f"\n→ 제외 대상(작성 금지): {', '.join(excluded)}"
    note += (
        "\n→ 괄호 안 금액은 각 분야 정부지원금 최대 한도(고정값)이며, 기업별 총 한도는 5,000만원이다. "
        "예산·성과를 서술할 때 이 한도를 초과하지 않도록 현실적으로 작성할 것."
    )
    return note

# 섹션 내부 시간 예산 (초). 라우터의 180초 타임아웃(안전망)보다 항상 먼저 작동해,
# 라우터가 섹션을 강제로 끊으면서 이미 성공한 1차 초안까지 버리는 상황을 막는다.
# deadline = time.monotonic() + _SECTION_INNER_DEADLINE_S 로 각 섹션 시작 시 계산.
_SECTION_INNER_DEADLINE_S: float = 165.0
_GATE_MIN_BUDGET_S: float = 85.0   # 검수 게이트 진입 최소 잔여(검수 최악 ~31s + 재생성 최악 ~45s + 여유)
_REGEN_MIN_BUDGET_S: float = 55.0  # 재생성 진입 최소 잔여(재생성 캡 45s + 여유)
_REGEN_TIMEOUT_S: float = 45.0     # 재생성 단일 호출 타임아웃(재시도 없음)

# 모듈 레벨 파일 캐시 — 프로세스 재시작 전까지 디스크 재독 없음
_cache_system_md: str | None = None
_cache_section_gen: str | None = None
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
    if not _EVAL_PROMPT_PATH.exists():
        return {}

    # 평가 기준·사례는 100% 노션 데이터에서만 가져온다 (없으면 프롬프트 내장 루브릭으로 평가)
    criteria = get_evaluation_examples(section_id, section_title)
    template = _load_eval_prompt()
    content = "\n\n".join(s.text for s in result.content_segments) if result.content_segments else result.content

    # criteria를 system에 고정 → 섹션 간 캐시 히트로 토큰 절감
    system = (
        "당신은 정부지원사업 심사위원입니다. 탈락 근거를 찾는 것이 역할입니다. 반드시 JSON만 반환하세요.\n\n"
        + (
            "## 실제 심사위원 피드백 사례 (노션 데이터)\n\n" + criteria
            if criteria
            else ""
        )
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


def _extract_headlines(result: SectionResult) -> list[str]:
    """SectionResult 본문에서 ■ 소제목 줄만 추출 (누적 중복방지 컨텍스트용)."""
    return [
        line.strip()
        for line in (result.content or "").splitlines()
        if line.strip().startswith("■")
    ]


def _build_prior_context(prior_headlines: list[str]) -> str:
    """누적된 앞 섹션 소제목 목록을 프롬프트 블록 텍스트로 구성."""
    return "\n".join(prior_headlines) if prior_headlines else "(아직 없음 — 첫 번째 섹션)"


def _build_full_prior_context(completed_results: list["SectionResult"]) -> str:
    """앞서 생성된 섹션들의 전체 본문을 누적 컨텍스트로 구성.

    Problem(1-x) + Solution(2-x) 순차 생성 시 사용.
    각 섹션은 이전 섹션들의 전체 내용을 인지한 상태에서 작성되어
    논거·통계·수치 중복을 방지하고 문제→솔루션 논리 흐름을 강화함.
    """
    if not completed_results:
        return "(아직 없음 — 첫 번째 섹션)"
    parts = []
    for r in completed_results:
        content = r.content
        if content:
            parts.append(f"### [{r.section_id}] {r.section_title}\n\n{content}")
    return "\n\n---\n\n".join(parts) if parts else "(아직 없음 — 첫 번째 섹션)"


def generate_framework_section(
    section: dict,
    questions: list[Question],
    answers: dict[str, Answer],
    skills: list[Skill],
    company_context: dict | None = None,
    extra_instruction: str = "",
    prior_context: str = "",
    timeout_s: float = 60.0,
    retries: int = 1,
) -> SectionResult:
    """단일 프레임워크 섹션 생성 (양식 무관).

    Args:
        section: FRAMEWORK_SECTIONS 항목 {"id", "title", "parent_title", "category", "tags"}
        questions: 인터뷰 질문 목록
        answers: 인터뷰 답변 딕셔너리
        skills: 로드된 스킬 목록
        company_context: extract_company_context() 결과
        extra_instruction: feedback_agent 검수 미달 시 재작성 지침 (재생성 호출에서만 사용)
        prior_context: 앞서 생성된 섹션들의 ■ 소제목 누적 (중복 방지용)
        timeout_s: 단일 API 호출 타임아웃(초). 재생성은 45초로 낮춰 총 소요를 확정한다.
        retries: SDK 재시도 횟수. 재생성은 0으로 주어 재시도 없이 1회만 시도한다.
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
        prior_context=prior_context or "(아직 없음 — 첫 번째 섹션)",
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
        timeout_s=timeout_s,
        retries=retries,
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
    prior_context: str = "",
    cancel_event: threading.Event | None = None,
    deadline: float | None = None,
) -> SectionResult:
    """feedback_agent 검수 게이트 — 기준 미달 시 retry_instruction으로 1회 재생성.

    검수/재생성 실패 시 원본 초안을 그대로 반환 (게이트는 품질 보강용, 차단용 아님).
    cancel_event가 설정되면(상위 호출이 타임아웃으로 이미 포기한 상태) 추가 LLM 호출을
    하지 않고 즉시 반환 — 좀비 스레드의 불필요한 API 소비를 막기 위함.
    deadline: time.monotonic() 기준 섹션 마감 시각. 잔여 시간이 부족하면 검수·재생성을
        생략하고 1차 초안을 확정해, 라우터 타임아웃에 걸려 초안이 통째로 버려지는 것을 막는다.
        None이면 시간 예산 검사를 하지 않음(하위 호환).
    """
    if os.getenv("MOCK_MODE", "0") == "1" or not result.content:
        return result
    if cancel_event is not None and cancel_event.is_set():
        return result

    # 검수 진입 전 시간 예산 확인 — 잔여가 부족하면 1차 초안 확정
    if deadline is not None and (deadline - time.monotonic()) < _GATE_MIN_BUDGET_S:
        logger.info(
            "[피드백 게이트] %s 잔여 %.0fs < %.0fs → 검수 생략, 1차 초안 확정",
            section["id"], deadline - time.monotonic(), _GATE_MIN_BUDGET_S,
        )
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
    if cancel_event is not None and cancel_event.is_set():
        return result

    logger.info(
        "[피드백 게이트] %s 기준 미달 → 재생성 (누락 헤더 %d개, 미충족 기준 %d개)",
        section["id"], len(review.missing_headers), len(review.failed_criteria),
    )
    # 진단용 — 어떤 기준에서 미달하는지 로그로 남겨 헛돌이(전 섹션 재생성) 원인 분석에 사용
    if review.missing_headers:
        logger.info("[피드백 게이트] %s 누락 헤더: %s", section["id"], review.missing_headers)
    for issue in review.failed_criteria:
        logger.info("[피드백 게이트] %s 미충족: %s", section["id"], issue.criterion)

    # 재생성 진입 전 시간 예산 확인 — 잔여가 부족하면 1차 초안 확정
    if deadline is not None and (deadline - time.monotonic()) < _REGEN_MIN_BUDGET_S:
        logger.info(
            "[피드백 게이트] %s 잔여 %.0fs < %.0fs → 재생성 생략, 1차 초안 확정",
            section["id"], deadline - time.monotonic(), _REGEN_MIN_BUDGET_S,
        )
        return result

    try:
        # 재생성은 재시도 없이 45초로 캡 — 총 소요 시간을 확정해 라우터 타임아웃 이전에 반드시 종료
        retry = generate_framework_section(
            section, questions, answers, skills, company_context,
            extra_instruction=review.retry_instruction,
            prior_context=prior_context,
            timeout_s=_REGEN_TIMEOUT_S,
            retries=0,
        )
        if retry.content:
            return retry
    except Exception as e:
        logger.warning("[피드백 게이트] 재생성 실패(%s): %s — 원본 초안 유지", section["id"], e)
    return result


def generate_one_framework_section(
    section: dict,
    questions: list[Question],
    answers: dict[str, Answer],
    skills: list[Skill],
    company_context: dict | None = None,
    prior_context: str = "",
    cancel_event: threading.Event | None = None,
    deadline: float | None = None,
) -> tuple[SectionResult, list[str]]:
    """단일 프레임워크 섹션 생성 + feedback_agent 검수 게이트 + 헤드라인 추출.

    라우터에서 섹션별 SSE 스트리밍을 위해 사용.
    cancel_event: 상위(asyncio.wait_for)가 타임아웃으로 이미 포기했음을 알리는 신호.
        설정되면 피드백 게이트의 추가 LLM 호출(검수·재생성)을 생략함.
    deadline: time.monotonic() 기준 섹션 마감 시각. 검수 게이트로 전달돼, 잔여 시간이
        부족하면 검수·재생성을 생략하고 1차 초안을 확정한다. None이면 예산 검사 없음.

    Returns:
        (result, new_prior_lines) — new_prior_lines는 다음 섹션의 prior_context에 추가할 줄 목록
    """
    try:
        r = generate_framework_section(
            section, questions, answers, skills, company_context,
            prior_context=prior_context,
        )
        r = _apply_feedback_gate(
            section, r, questions, answers, skills, company_context,
            prior_context=prior_context,
            cancel_event=cancel_event,
            deadline=deadline,
        )
    except Exception as e:
        logger.error("[generate_one_framework_section 실패] %s: %s", section["id"], e)
        r = SectionResult(
            section_id=section["id"],
            section_title=section["title"],
            content="",
            confidence_level="red",
            reasoning=f"생성 실패: {e}",
            missing_info=["섹션 생성 오류 — 재시도 필요"],
            completion_score=0,
        )
    new_lines: list[str] = []
    headlines = _extract_headlines(r)
    if headlines:
        new_lines.append(f"[{section['parent_title']} > {section['title']}]")
        new_lines.extend(headlines)
    return r, new_lines


def generate_framework_draft(
    questions: list[Question],
    answers: dict[str, Answer],
    skills: list[Skill],
    company_context: dict | None = None,
) -> list[SectionResult]:
    """프레임워크 섹션 하이브리드 생성.

    Phase 1 — Problem + Solution (1-1~2-3): 전체 누적 컨텍스트로 순차 생성.
        각 섹션이 이전 모든 섹션의 본문을 인지하여 논거·수치 중복을 방지하고
        문제→솔루션 논리 흐름을 강화함.

    Phase 2 — Scale-up + Team (3-1~4-1): 인터뷰 내용만으로 병렬 생성.
        전략·자금·팀 섹션은 인터뷰 답변이 핵심 재료이므로 prior_context 없이
        독립 생성하여 속도를 높이고 attention dilution을 방지함.

    Returns:
        FRAMEWORK_SECTIONS 순서대로 정렬된 SectionResult 목록
    """
    import concurrent.futures

    seq_sections = [s for s in FRAMEWORK_SECTIONS if s["id"] in _SEQUENTIAL_IDS]
    par_sections = [s for s in FRAMEWORK_SECTIONS if s["id"] in _PARALLEL_IDS]

    seq_results: list[SectionResult] = []  # 누적 컨텍스트용

    # Phase 1: 순차 생성 (Problem + Solution)
    for sec in seq_sections:
        prior_ctx = _build_full_prior_context(seq_results)
        try:
            r = generate_framework_section(
                sec, questions, answers, skills, company_context,
                prior_context=prior_ctx,
            )
            r = _apply_feedback_gate(
                sec, r, questions, answers, skills, company_context,
                prior_context=prior_ctx,
            )
        except Exception as e:
            logger.error("[프레임워크 순차 섹션 실패] %s: %s", sec["id"], e)
            r = SectionResult(
                section_id=sec["id"],
                section_title=sec["title"],
                content="",
                confidence_level="red",
                reasoning=f"생성 실패: {e}",
                missing_info=["섹션 생성 오류 — 재시도 필요"],
                completion_score=0,
            )
        seq_results.append(r)

    # Phase 2: 병렬 생성 (Scale-up + Team) — prior_context 없음
    par_results_map: dict[str, SectionResult] = {}

    def _gen_par(sec: dict) -> SectionResult:
        try:
            r = generate_framework_section(
                sec, questions, answers, skills, company_context,
                prior_context="",
            )
            r = _apply_feedback_gate(
                sec, r, questions, answers, skills, company_context,
                prior_context="",
            )
        except Exception as e:
            logger.error("[프레임워크 병렬 섹션 실패] %s: %s", sec["id"], e)
            r = SectionResult(
                section_id=sec["id"],
                section_title=sec["title"],
                content="",
                confidence_level="red",
                reasoning=f"생성 실패: {e}",
                missing_info=["섹션 생성 오류 — 재시도 필요"],
                completion_score=0,
            )
        return r

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_gen_par, s): s["id"] for s in par_sections}
        for future in concurrent.futures.as_completed(futures):
            sec_id = futures[future]
            try:
                par_results_map[sec_id] = future.result()
            except Exception as e:
                logger.error("[병렬 섹션 future 실패] %s: %s", sec_id, e)

    # FRAMEWORK_SECTIONS 순서 유지하여 반환
    results: list[SectionResult] = list(seq_results)
    for sec in par_sections:
        if sec["id"] in par_results_map:
            results.append(par_results_map[sec["id"]])

    return results


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


# ────────────────────────────────────────────────────────────────────
# 양식 변환 v3 — (1-a) 초안 분석(양식 무관·세션 캐시) + (1-b) 양식별 매핑
# 프로세스: 초안 분석 → 양식 매핑 → 소스 섹션만으로 렌더링(중복 차단) →
#           대응 소스 없는 섹션은 기업 컨텍스트+초안 요약으로 신규 생성
# ────────────────────────────────────────────────────────────────────


def compute_draft_hash(framework_results: list["SectionResult"]) -> str:
    """초안 내용 해시 — 1-a 분석 캐시 무효화 판정용(초안 수정 시 재분석)."""
    import hashlib
    joined = "\n\x1e\n".join(
        f"{r.section_id}\x1f{r.display_content()}" for r in framework_results
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def analyze_framework_draft(framework_results: list["SectionResult"]) -> dict:
    """(1-a) 초안 섹션별 정보 인벤토리. 양식과 무관 — 세션당 1회 실행 후 캐시.

    Returns: {"sections": [{"id","title","summary","data_assets":[...]}]}
    """
    if os.getenv("MOCK_MODE", "0") == "1":
        return {"sections": [
            {"id": r.section_id, "title": r.section_title,
             "summary": (r.display_content() or "")[:200], "data_assets": []}
            for r in framework_results
        ]}

    body = "\n\n---\n\n".join(
        f"[{r.section_id}] {r.section_title}\n{r.display_content()}"
        for r in framework_results
    )
    prompt = f"""아래 사업계획서 초안의 각 섹션에 대해 정보 인벤토리를 만드세요.

## 초안
{body}

## 출력 규칙
- 각 섹션마다: summary(2~3문장 요약), data_assets(원문에 있는 정량 수치·고유명사·출처·성과 목록)
- 원문에 없는 정보 추가 금지. JSON만 반환.

## 출력 스키마
{{"sections": [{{"id": "1-1", "title": "...", "summary": "...", "data_assets": ["..."]}}]}}
"""
    text, _meta = call_claude(
        system=("당신은 사업계획서 편집 AI입니다. 초안 섹션별 정보 인벤토리를 만듭니다. "
                "원문에 없는 내용을 추가하지 마세요. JSON만 반환합니다."),
        user=prompt,
        model="claude-haiku-4-5-20251001",
        max_tokens=6144,
        temperature=0.1,
        purpose="draft_analysis",
    )
    try:
        data = parse_json_response(text)
    except Exception as e:  # noqa: BLE001
        logger.error("draft_analysis 파싱 실패: %s", e)
        data = {}
    valid = {r.section_id for r in framework_results}
    sections = [s for s in data.get("sections", [])
                if isinstance(s, dict) and s.get("id") in valid]
    if not sections:
        # 파싱 실패 폴백 — 원문 앞부분을 요약으로 대체 (파이프라인 중단 방지)
        sections = [{"id": r.section_id, "title": r.section_title,
                     "summary": (r.display_content() or "")[:300], "data_assets": []}
                    for r in framework_results]
    return {"sections": sections}


def map_analysis_to_form(draft_analysis: dict, form: Form) -> dict:
    """(1-b) 초안 분석 → 양식 섹션 매핑 + 소스 충분성 판정. 양식별 소형 호출 1회.

    Returns: {form_section_id: {"sources": [draft_id...], "sufficiency": "full|partial|none"}}
    """
    draft_ids = [s["id"] for s in draft_analysis.get("sections", [])]
    if os.getenv("MOCK_MODE", "0") == "1":
        return {s.id: {"sources": draft_ids[:1], "sufficiency": "partial"}
                for s in form.sections}

    inv = "\n".join(
        f"- [{s['id']}] {s.get('title', '')}: {s.get('summary', '')}"
        + (f" | 데이터: {', '.join(s.get('data_assets', [])[:6])}" if s.get("data_assets") else "")
        for s in draft_analysis.get("sections", [])
    )
    form_meta = "\n".join(
        f"- [{s.id}] {s.title} (지시 요약: {(s.instructions or '')[:150].replace(chr(10), ' ')})"
        for s in form.sections
    )
    prompt = f"""사업계획서 초안 인벤토리를 양식 섹션에 배치하는 매핑을 결정하세요.

## 초안 인벤토리
{inv}

## 양식 섹션
{form_meta}

## 규칙
- 각 양식 섹션에 의미상 대응하는 초안 섹션 id를 0~3개 지정 (sources)
- sufficiency: full(소스로 충분) / partial(일부만 커버) / none(대응 소스 없음 — 새로 작성 필요)
- 하나의 초안 섹션을 여러 양식 섹션에 배치 가능하나, 남용 금지(같은 내용의 중복 서술 방지)
- id는 대괄호 없이 원문 그대로 사용 (예: "1-1", "[1-1]" 아님)
- JSON만 반환

## 출력 스키마
{{"<양식섹션id>": {{"sources": ["<초안id>"], "sufficiency": "full|partial|none"}}}}
"""
    text, _meta = call_claude(
        system="당신은 사업계획서 편집 AI입니다. JSON만 반환합니다.",
        user=prompt,
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        temperature=0.0,
        purpose="form_mapping",
        metadata={"program_code": form.program_code},
    )
    try:
        data = parse_json_response(text)
        if not isinstance(data, dict):
            raise ValueError(f"expected dict, got {type(data).__name__}")
    except Exception as e:  # noqa: BLE001
        logger.error("form_mapping 파싱 실패: %s — 전체 초안 폴백", e)
        data = {}

    def _norm(x) -> str:
        # LLM이 "[1-1]"처럼 대괄호를 붙여 반환하는 경우 정규화
        return str(x).strip().strip("[]").strip()

    data = {_norm(k): v for k, v in data.items()}
    valid_draft = set(draft_ids)
    result: dict = {}
    for s in form.sections:
        entry = data.get(s.id) if isinstance(data.get(s.id), dict) else None
        if entry is None:
            # LLM 누락 → 안전 폴백: 전체 초안을 소스로(기존 v1 동작과 동일)
            result[s.id] = {"sources": list(draft_ids), "sufficiency": "partial"}
            continue
        sources = [_norm(d) for d in (entry.get("sources") or [])]
        sources = [d for d in sources if d in valid_draft]
        suff = entry.get("sufficiency")
        if suff not in ("full", "partial", "none"):
            suff = "partial" if sources else "none"
        if not sources:
            suff = "none"
        result[s.id] = {"sources": sources, "sufficiency": suff}
    return result


# ── 변환 형식 검수 게이트 — 초안과 동일 수준의 지침 준수 강제 ─────────
# 프로그램적 검증(비용 0) → 위반 섹션만 재작성 지침을 붙여 1회 재생성.

_CONV_TBL_LINE = re.compile(r"^\s*\|")
_CONV_FORBIDDEN_TAG = re.compile(r"\[(출처 필요|추정값|수치 필요)\]")


def _is_skeleton_section(section: FormSection) -> bool:
    ins = section.instructions or ""
    return "공란 유지" in ins or "스켈레톤" in ins


def _validate_form_section(result: "SectionResult", section: FormSection) -> list[str]:
    """양식 변환 결과의 형식 위반 목록 반환(비었으면 통과)."""
    v: list[str] = []
    content = result.content or ""
    if not content.strip():
        return ["본문이 비어 있음 — 소스 컨텍스트 기반으로 작성 필수"]
    skeleton = _is_skeleton_section(section)
    if not skeleton:
        body = "\n".join(l for l in content.split("\n") if not _CONV_TBL_LINE.match(l)).strip()
        if len(body) < 200:
            v.append(f"본문 분량 부족({len(body)}자) — 표 제외 400~900자로 보강")
        elif len(body) > 1500:
            v.append(f"본문 과다({len(body)}자) — 표 제외 400~900자로 압축, 중복 서술 제거")
        if "◦" not in content:
            v.append("◦ 개조식 헤드라인 부재 — '◦ 상위 항목 / - 세부 항목' 구조로 재작성")
    if "▶ 표" in (section.instructions or "") and "|" not in content:
        v.append("지시사항의 표 누락 — 같은 컬럼 구조의 markdown 표를 반드시 포함")
    if re.search(r"^■", content, re.M):
        v.append("■ 소제목 사용 금지 — ◦ 개조식으로 교체")
    if _CONV_FORBIDDEN_TAG.search(content):
        v.append("[출처 필요]·[추정값]·[수치 필요] 태그 삽입 금지 — 태그 제거")
    return v


def _cross_dup_violations(results: list["SectionResult"]) -> dict[str, list[str]]:
    """섹션 간 40자 이상 동일 문장 중복 → 뒤 섹션에 재작성 지침."""
    seen: dict[str, str] = {}
    out: dict[str, list[str]] = {}
    for r in results:
        for line in (r.content or "").split("\n"):
            t = line.strip()
            if len(t) < 40 or _CONV_TBL_LINE.match(t):
                continue
            if t in seen and seen[t] != r.section_id:
                out.setdefault(r.section_id, []).append(
                    f"다음 문장이 [{seen[t]}] 섹션과 중복 — 삭제하거나 이 섹션 관점으로 재서술: \"{t[:40]}...\""
                )
            else:
                seen.setdefault(t, r.section_id)
    return out


def convert_to_form(
    framework_results: list[SectionResult],
    form: Form,
    skills: list[Skill] | None = None,
    voucher_options: list[str] | None = None,
    section_sources: dict | None = None,
    company_context: dict | None = None,
    draft_analysis: dict | None = None,
    progress_cb=None,
    gap_answers: dict[str, str] | None = None,
) -> list[SectionResult]:
    """프레임워크 초안 → 선택한 양식 섹션 구조로 변환.

    progress_cb: 진행 콜백 (kind: str, payload: dict). 섹션 완료 시 ('section', {...}),
        검수 재생성 진입 시 ('stage', {'stage': 'reviewing'}). 실패해도 변환은 계속된다.

    Args:
        framework_results: generate_framework_draft()가 반환한 SectionResult 목록
        form: 변환 대상 Form (load_form()으로 로드)
        skills: 로드된 스킬 목록 (없으면 빈 리스트)
        voucher_options: 혁신바우처 전용 — 사용자가 선택한 바우처 서비스
            (컨설팅/기술지원/마케팅). 3·4·5·6번 섹션에만 주입된다.
            다른 양식에서는 None(무시).
        section_sources: (v3) map_analysis_to_form() 결과. 주어지면 섹션별로
            매핑된 소스 초안만 컨텍스트로 주입(중복 서술 차단). None이면 기존
            동작(전체 초안 주입) 유지 — 하위호환.
        company_context: (v3) 소스 없는(gap) 섹션 신규 생성 시 근거로 사용.
        draft_analysis: (v3) gap 섹션에 초안 전체 요약을 근거로 제공.
        gap_answers: 갭 보완 인터뷰 답변 {질문id: 답변}. form.gap_questions의
            target_sections에 해당하는 섹션 컨텍스트에만 주입. None/빈 답변은 무시.
    """
    import concurrent.futures

    if skills is None:
        skills = []

    def _notify(kind: str, payload: dict) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(kind, payload)
        except Exception:  # noqa: BLE001 — 진행 알림 실패가 변환을 막으면 안 됨
            pass

    def _notify_section(r: "SectionResult") -> None:
        _notify("section", {
            "section_id": r.section_id,
            "section_title": r.section_title,
            "confidence_level": r.confidence_level,
            "completion_score": r.effective_completion_score(),
        })

    voucher_note = _build_voucher_note(voucher_options)

    # 갭 보완 인터뷰 답변 → target_sections별 주입 블록 (섹션id → ["- Q...\n  A...", ...])
    gap_notes_by_section: dict[str, list[str]] = {}
    if gap_answers:
        for q in getattr(form, "gap_questions", []) or []:
            ans = (gap_answers.get(str(q.get("id", ""))) or "").strip()
            if not ans:
                continue
            block = f"- Q. {str(q.get('question', '')).strip()}\n  A. {ans}"
            for sid in q.get("target_sections", []) or []:
                gap_notes_by_section.setdefault(str(sid), []).append(block)

    # 1. 전체 초안 컨텍스트 (매핑 없을 때의 하위호환 + 폴백)
    framework_parts = [
        f"## {r.section_title}\n\n{r.display_content()}"
        for r in framework_results
    ]
    framework_context = "\n\n---\n\n".join(framework_parts)

    # v3: 섹션별 소스 컨텍스트 구성
    _by_id = {r.section_id: r for r in framework_results}

    def _context_for(section: FormSection) -> str:
        if not section_sources or section.id not in section_sources:
            return framework_context
        entry = section_sources[section.id] or {}
        srcs = [_by_id[i] for i in entry.get("sources", []) if i in _by_id]
        if srcs:
            return "\n\n---\n\n".join(
                f"## {r.section_title}\n\n{r.display_content()}" for r in srcs
            )
        # 대응 소스 없음(gap) → 기업 컨텍스트 + 초안 요약을 근거로 신규 작성
        parts = [
            "(이 섹션에 직접 대응하는 초안 내용이 없습니다. 아래 근거만으로 새로 작성하되, "
            "근거 없는 정량 수치는 공란으로 두세요.)"
        ]
        if company_context:
            ctx_lines = [
                f"### {k}\n{str(v).strip()}"
                for k, v in company_context.items()
                if k != "_meta" and str(v or "").strip()
            ]
            if ctx_lines:
                parts.append("## 기업 컨텍스트\n\n" + "\n\n".join(ctx_lines))
        if draft_analysis:
            summ = "\n".join(
                f"- [{s['id']}] {s.get('title', '')}: {s.get('summary', '')}"
                for s in draft_analysis.get("sections", [])
            )
            if summ:
                parts.append("## 초안 전체 요약\n\n" + summ)
        return "\n\n".join(parts)

    system = _load_system_md()
    template = _load_form_conv()

    results: list[SectionResult | None] = [None] * len(form.sections)

    def _convert_one(idx: int, section: FormSection,
                     extra_instruction: str | None = None) -> tuple[int, SectionResult]:
        if os.getenv("MOCK_MODE", "0") == "1":
            return idx, _mock_section_result(section, [], [])

        selected = select_skills_for_section(skills, section.category, section.tags) if skills else []
        skills_block = "\n\n---\n\n".join(s.to_prompt_block() for s in selected)
        skills_cache_prefix = f"## 적용할 작성 방법론 (Skills)\n\n{skills_block}" if skills_block else ""

        today_date_note = _build_today_date_note(section.tags)

        # 혁신바우처 선택 서비스 안내를 링크된 섹션(3·4·5·6) 지시사항에 주입.
        # 캐시된 Form.section을 변형하지 않도록 로컬 문자열로만 조립.
        # ※ 지시문 맨 앞에 배치 — 뒤에 붙이면 모델이 예시 표(3개 분야 행)를 우선해
        #   선택되지 않은 분야까지 작성하는 사례가 실측됨.
        section_instructions = section.instructions or "(별도 지시 없음)"
        if voucher_note and section.id in _VOUCHER_LINKED_SECTION_IDS:
            section_instructions = voucher_note.strip() + "\n\n" + section_instructions

        section_context = _context_for(section)
        gap_notes = gap_notes_by_section.get(section.id)
        if gap_notes:
            section_context += (
                "\n\n---\n\n## 추가 인터뷰 답변 (사용자가 직접 보완한 정보 — 이 섹션 작성에 우선 반영)\n\n"
                + "\n".join(gap_notes)
            )

        user = template.format(
            framework_context=section_context,
            section_id=section.id,
            section_title=section.title,
            section_instructions=section_instructions,
            skills_block="(→ 위의 캐시 블록에 포함된 Skills 적용)" if skills_block else "(Skills 없음)",
            today_date_note=today_date_note,
        )
        if extra_instruction:
            user += ("\n\n## ⚠️ 형식 검수 미달 — 아래 위반 사항을 반드시 해결하여 재작성할 것\n"
                     + extra_instruction)

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
    _notify_section(first)

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
                _notify_section(results[idx])

    # ── 형식 검수 게이트: 위반 섹션만 재작성 지침 첨부 후 1회 재생성 ──
    if os.getenv("MOCK_MODE", "0") != "1":
        idx_by_id = {s.id: i for i, s in enumerate(form.sections)}
        violations: dict[str, list[str]] = {}
        done = [r for r in results if r is not None]
        for r in done:
            sec = form.get_section(r.section_id)
            if sec is None:
                continue
            vs = _validate_form_section(r, sec)
            if vs:
                violations[r.section_id] = vs
        for sid, msgs in _cross_dup_violations(done).items():
            violations.setdefault(sid, []).extend(msgs)
        if violations:
            logger.info("[변환 검수] 위반 %d개 섹션 재생성: %s",
                        len(violations), {k: len(v) for k, v in violations.items()})
            _notify("stage", {"stage": "reviewing", "count": len(violations)})
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as regen_ex:
                futures = {
                    regen_ex.submit(
                        _convert_one, idx_by_id[sid], form.get_section(sid),
                        "\n".join(f"- {m}" for m in msgs),
                    ): sid
                    for sid, msgs in violations.items() if sid in idx_by_id
                }
                for future in concurrent.futures.as_completed(futures):
                    sid = futures[future]
                    try:
                        idx, res = future.result()
                        # 재생성이 오히려 실패(빈 본문)하면 원본 유지
                        if (res.content or "").strip():
                            results[idx] = res
                    except Exception as e:  # noqa: BLE001
                        logger.error("[변환 검수 재생성 실패] %s: %s — 원본 유지", sid, e)

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
