"""v1(재생성) vs v2(재배치) 양식 변환 성능 비교 스크립트.

사용법:
    python scripts/compare_conversion.py <session_id> <program_code>

예시:
    python scripts/compare_conversion.py abc12345 initial_package

출력:
    scripts/output/비교_v1_<program_code>.docx
    scripts/output/비교_v2_<program_code>.docx
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from dotenv import load_dotenv
load_dotenv()

from core.forms import load_form
from core.generation import convert_to_form, convert_to_form_v2
from core.skills import load_skills
from core.docx_export import export_to_docx
from services.session_store import load_framework_draft


_ROOT = Path(__file__).parent.parent
_SKILLS_DIR = _ROOT / "skills"
_LOG_PATH = _ROOT / "logs" / "llm_calls.jsonl"
_OUT_DIR = Path(__file__).parent / "output"


def _count_log_lines() -> int:
    if not _LOG_PATH.exists():
        return 0
    with open(_LOG_PATH, encoding="utf-8") as f:
        return sum(1 for _ in f)


def _run_v1(framework_results, form, skills) -> tuple:
    log_before = _count_log_lines()
    t0 = time.time()
    results = convert_to_form(framework_results, form, skills)
    elapsed = time.time() - t0
    llm_calls = _count_log_lines() - log_before
    return results, elapsed, llm_calls


def _run_v2(framework_results, form) -> tuple:
    log_before = _count_log_lines()
    t0 = time.time()
    results = convert_to_form_v2(framework_results, form)
    elapsed = time.time() - t0
    llm_calls = _count_log_lines() - log_before
    return results, elapsed, llm_calls


def _save_docx(form, results, label: str, program_code: str) -> Path:
    _OUT_DIR.mkdir(exist_ok=True)
    buf = export_to_docx(form, results, business_name=f"비교테스트_{label}")
    out_path = _OUT_DIR / f"비교_{label}_{program_code}.docx"
    out_path.write_bytes(buf.getvalue())
    return out_path


def _print_summary(label: str, results, elapsed: float, llm_calls: int) -> None:
    total_sections = len(results)
    green = sum(1 for r in results if r.confidence_level == "green")
    yellow = sum(1 for r in results if r.confidence_level == "yellow")
    red = sum(1 for r in results if r.confidence_level == "red")
    avg_score = sum(r.completion_score for r in results) / total_sections if total_sections else 0

    print(f"\n{'='*50}")
    print(f"  {label}")
    print(f"{'='*50}")
    print(f"  소요 시간    : {elapsed:.1f}초")
    print(f"  LLM 호출 수  : {llm_calls}회")
    print(f"  섹션 수      : {total_sections}개")
    print(f"  신뢰도       : 🟢{green} / 🟡{yellow} / 🔴{red}")
    print(f"  평균 완성도  : {avg_score:.0f}점")
    print()
    for r in results:
        icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(r.confidence_level, "⚪")
        mapped = r.llm_meta.get("mapped_from", "-") if label.startswith("v2") else "-"
        mapped_str = f"  ← {mapped}" if mapped != "-" else ""
        print(f"  {icon} [{r.section_id}] {r.section_title}{mapped_str}")


def main() -> None:
    if len(sys.argv) < 3:
        print("사용법: python scripts/compare_conversion.py <session_id> <program_code>")
        print("예시:   python scripts/compare_conversion.py abc12345 initial_package")
        sys.exit(1)

    session_id = sys.argv[1]
    program_code = sys.argv[2]

    print(f"\n[비교 테스트 시작]")
    print(f"  세션 ID     : {session_id}")
    print(f"  양식 코드   : {program_code}")

    # 프레임워크 초안 로드
    framework_results = load_framework_draft(session_id)
    if not framework_results:
        print(f"\n[오류] 세션 {session_id}에 프레임워크 초안이 없습니다.")
        print("  먼저 /generating/<session_id> 페이지에서 초안을 생성해주세요.")
        sys.exit(1)

    print(f"  프레임워크  : {len(framework_results)}개 섹션 로드 완료")

    # 양식 + 스킬 로드
    form = load_form(program_code)
    skills = load_skills(_SKILLS_DIR) if _SKILLS_DIR.exists() else []
    print(f"  양식        : {form.program_name} ({len(form.sections)}개 섹션)")

    # v1 실행
    print("\n[v1 재생성 방식 실행 중...]")
    v1_results, v1_elapsed, v1_calls = _run_v1(framework_results, form, skills)
    v1_path = _save_docx(form, v1_results, "v1", program_code)

    # v2 실행
    print("[v2 재배치 방식 실행 중...]")
    v2_results, v2_elapsed, v2_calls = _run_v2(framework_results, form)
    v2_path = _save_docx(form, v2_results, "v2", program_code)

    # 결과 출력
    _print_summary("v1(재생성)", v1_results, v1_elapsed, v1_calls)
    _print_summary("v2(재배치)", v2_results, v2_elapsed, v2_calls)

    print(f"\n{'='*50}")
    print(f"  비교 요약")
    print(f"{'='*50}")
    time_diff = v1_elapsed - v2_elapsed
    call_diff = v1_calls - v2_calls
    if time_diff > 0:
        print(f"  속도 차이   : v2가 {time_diff:.1f}초 빠름")
    else:
        print(f"  속도 차이   : v1이 {-time_diff:.1f}초 빠름")
    if call_diff > 0:
        print(f"  호출 차이   : v2가 {call_diff}회 적음")
    else:
        print(f"  호출 차이   : v1이 {-call_diff}회 적음")
    print()
    print(f"  v1 DOCX → {v1_path}")
    print(f"  v2 DOCX → {v2_path}")
    print()
    print("두 파일을 열어 결과물 품질을 직접 비교해주세요.")


if __name__ == "__main__":
    main()
