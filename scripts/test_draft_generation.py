#!/usr/bin/env python3
"""
DRAFT_WRITING_GUIDE 기반 사업계획서 초안 생성 — 1회성 테스트용.
프로덕션 코드 미수정, claude-sonnet-4-6 직접 지정.
결과: output/test_draft.docx

Usage:
    cd bizplan-mvp
    python scripts/test_draft_generation.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.docx_export import export_to_docx
from core.forms import Form
from core.generation import ContentSegment, SectionResult
from core.llm import call_claude

# ── 테스트 인터뷰 답변 ─────────────────────────────────────────
INTERVIEW_QA = """
아이템: 반려동물 용품 큐레이션 커머스 플랫폼

Q. 해당 사업을 하게 된 이유는 무엇이며, 대표님의 경력·이력과 어떻게 연관되나요?
A. 그냥... 강아지 키우다 보니까 좋은 제품 찾기 어렵더라고요. 그래서 시작했어요.
   저는 원래 회사 다녔는데 마케팅 쪽이요. 한 3년 정도?

Q. 이 사업으로 핵심적으로 해결하고자 하는 문제는 무엇인가요?
A. 기존에는 쿠팡이나 네이버에서 샀는데, 뭐가 좋은지 모르잖아요. 정보가 너무 많고.
   그게 불편하니까 저희가 큐레이션 해주는 거예요.

Q. 주 고객은 누구이며, 그 중 1차 타깃은 누구인가요?
A. 반려동물 키우는 사람들이요. 주로 2030 여성? 일단 주변에서 좋다고 했어요.
   지인들한테 물어봤는데 다들 쓰고 싶다고.

Q. 목표 시장 규모는 얼마이며, 어떤 기준으로 측정하셨나요?
A. 반려동물 시장이 엄청 크잖아요. 한 4조인가 그렇다고 어디서 봤어요. 계속 크고 있고요.

Q. 협약기간 내 만들려는 아이템/제품은 무엇이며, 핵심 기능은 무엇인가요?
A. 앱이요. 반려동물 정보 입력하면 맞춤 상품 추천해주는 거. 그리고 구매도 되고요.

Q. 그 핵심 기능이 앞서 말씀하신 문제를 어떻게 해결하며, 소비자는 어떤 효용을 얻나요?
A. 뭐 고를지 모르는 사람한테 바로 추천해주니까 편하죠. 시간도 절약되고.

Q. 주요 경쟁사(또는 유사 서비스)는 무엇이며, 자사만의 차별적 강점은 무엇인가요?
A. 핏펫? 그런 데 있는데, 저희는 큐레이션이 더 정확해요. 알고리즘으로 하니까요.

Q. 비즈니스 모델은 어떻게 되나요?
A. 수수료 모델이에요. 판매되면 저희가 일정 % 가져가는 구조요. 몇 %인지는 아직 정확히 안 정했어요.

Q. 향후 5년 예상 매출과 그 근거는 무엇인가요?
A. 1년차에 1억, 3년차에 10억, 5년차에 100억 정도? 시장이 크니까 잘 되면 될 것 같아요.

Q. 대표자 역량·경력은 어떻게 되며, 팀원 구성은 어떻게 되나요?
A. 저 마케팅 3년 했고요, 개발자 친구 한 명 있어요.
   디자인은 프리랜서 쓸 예정이에요. 일단 셋이서 해보려고요.
""".strip()

# ── 섹션 정의 (DRAFT_WRITING_GUIDE 4대 섹션) ──────────────────
SECTIONS = [
    {"id": "1",   "title": "개발 동기 및 현황",         "target": 1500},
    {"id": "2-1", "title": "시장 분석",                  "target": 1000},
    {"id": "2-2", "title": "아이템 기술 및 고도화 방안", "target": 3000},
    {"id": "2-3", "title": "추진성과",                   "target": 700},
    {"id": "3-1", "title": "추진 전략",                  "target": 3500},
    {"id": "3-2", "title": "자금 계획",                  "target": 700},
    {"id": "4-1", "title": "기업 구성",                  "target": 1500},
]

_GUIDE: str | None = None


def _load_guide() -> str:
    global _GUIDE
    if _GUIDE is None:
        path = (
            Path(__file__).resolve().parent.parent
            / "skills" / "L1_universal" / "DRAFT_WRITING_GUIDE.md"
        )
        _GUIDE = path.read_text(encoding="utf-8")
    return _GUIDE


def _generate(section_id: str, section_title: str, target: int, guide: str) -> str:
    system = f"""당신은 정부지원사업 사업계획서 전문 컨설턴트입니다.
아래 DRAFT_WRITING_GUIDE의 작성 지침과 Good Examples를 엄격히 준수하여 섹션을 작성합니다.

{guide}

=== 절대 준수 규칙 ===
- 문장 끝맺음: 명사형 또는 음슴체 (예: ~임. ~함. ~있음.)
- 소제목: 반드시 ▶ 기호 사용
- 세부 내용: 소제목 하단 2~3개 불릿(-), 각 150자 이내
- 수치: 모든 정량적 주장에 출처/계산근거 병기, 추정치는 (추정) 표기
- 목표 분량: 약 {target}자
- 금지: # ## ### 마크다운 헤딩
- 금지: **강조** 문법
- 금지: 숫자 섹션 번호 (1. 2-1. 등)
"""

    user = f"""다음 인터뷰 답변을 바탕으로 [{section_id}. {section_title}] 섹션을 작성하세요.

=== 인터뷰 답변 ===
{INTERVIEW_QA}

=== 지시 ===
DRAFT_WRITING_GUIDE의 [{section_id}] 지침과 Good Examples 형식을 그대로 따르세요.
답변에 없는 수치는 반드시 (추정) 표기. 섹션 제목/번호 없이 본문만 출력.
"""

    text, meta = call_claude(
        system=system,
        user=user,
        model="claude-sonnet-4-6",   # 테스트용 — 프로덕션은 Haiku 유지
        max_tokens=4096,
        temperature=0.3,
        purpose=f"test_sonnet_{section_id}",
    )
    tok_in = meta["input_tokens"]
    tok_out = meta["output_tokens"]
    ms = meta["duration_ms"]
    print(f"  [{section_id}] 완료 — in {tok_in} / out {tok_out} tokens / {ms}ms")
    return text


def main() -> None:
    print("=" * 60)
    print("  DRAFT_WRITING_GUIDE × claude-sonnet-4-6 생성 테스트")
    print("=" * 60)
    print()

    guide = _load_guide()
    print("[OK] DRAFT_WRITING_GUIDE.md 로드")
    print()

    results: list[SectionResult] = []
    for sec in SECTIONS:
        print(f"생성: [{sec['id']}] {sec['title']} ...")
        content = _generate(sec["id"], sec["title"], sec["target"], guide)
        results.append(
            SectionResult(
                section_id=sec["id"],
                section_title=sec["title"],
                content=content,
                content_segments=[ContentSegment(text=content, source="llm_inferred")],
                confidence_level="green",
                reasoning="",
            )
        )
        print()

    form = Form(
        program_code="test",
        program_name="정부지원사업 사업계획서 초안 (Sonnet 4.6 테스트)",
        target="예비창업자",
        max_funding="100,000,000",
        page_limit=20,
        notes=None,
        sections=[],
    )

    buf = export_to_docx(form, results, business_name="반려동물 커머스 플랫폼")

    out = Path(__file__).resolve().parent.parent / "output" / "test_draft_sonnet.docx"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(buf.read())

    print("=" * 60)
    print(f"[OK] DOCX 저장 완료: {out}")
    print("=" * 60)


if __name__ == "__main__":
    main()
