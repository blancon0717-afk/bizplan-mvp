"""
사업계획서 초안 자동 생성 CLI (4단계)

사용:
  python draft_plan.py --item "AI 기반 폐그물 재활용 소재 플랫폼" \\
                       --team "대표 홍길동(소재공학 박사), CTO 김철수(SW 10년)" \\
                       --program 예비창업패키지 \\
                       --target-score 70
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT))

from anthropic import Anthropic

import score_plan as sp
from claude_structurer import Structurer

# ── 상수 ──────────────────────────────────────────────────────────────────────

DRAFTS = ROOT / "drafts"
DRAFTS.mkdir(exist_ok=True)

SONNET_MODEL = "claude-sonnet-4-6"
GUIDE_PATH = ROOT / "delivery" / "guides" / "01_사업계획서_작성가이드.md"

SECTION_NAMES: list[str] = [
    "아이템 개요",
    "문제 정의",
    "솔루션 및 차별성",
    "시장 분석 및 고객",
    "비즈니스 모델 및 수익화",
    "팀 구성",
    "진입장벽 및 경쟁 방어력",
    "실행 계획 및 재무",
]

# 섹션 → 관련 루브릭 피처 (약점 섹션 탐지용)
SECTION_FEATURES: dict[str, list[str]] = {
    "아이템 개요":              ["아이템특이성", "MVP 테스트 실행 여부"],
    "문제 정의":                ["FGI"],
    "솔루션 및 차별성":         ["아이템특이성", "필수 재료 공급처 네트워크 보유", "투자의향서 확보 여부"],
    "시장 분석 및 고객":        ["FGI", "수요처 네트워크 보유", "수요처 확보 여부"],
    "비즈니스 모델 및 수익화":  ["비즈니스 모델 다양화", "누적 투자"],
    "팀 구성":                  ["제조 전문 인력 (기술자) 보유(팀원 기준)"],
    "진입장벽 및 경쟁 방어력":  ["MOU 보유 개수", "필수 재료 공급처 네트워크 보유"],
    "실행 계획 및 재무":        ["수요처 확보 여부", "견적서 보유 개수"],
}

_SEP_START = "<<<SECTION_START:{name}>>>"
_SEP_END   = "<<<SECTION_END:{name}>>>"


# ── 유틸 ──────────────────────────────────────────────────────────────────────

def _load_guide() -> str:
    if GUIDE_PATH.exists():
        return GUIDE_PATH.read_text(encoding="utf-8")[:3500]
    return "(가이드 파일 없음)"


def _default_advice_block() -> str:
    priority = [
        "수요처 확보 여부",
        "FGI",
        "투자의향서 확보 여부",
        "MOU 보유 개수",
        "아이템특이성",
        "필수 재료 공급처 네트워크 보유",
        "비즈니스 모델 다양화",
    ]
    lines = []
    for feat in priority:
        adv = sp.ADVICE.get(feat, "")
        if adv:
            lines.append(f"- {feat}: {adv[:120]}")
    return "\n".join(lines)


def _miss_advice_block(misses: list[dict]) -> str:
    lines = []
    for m in misses:
        if m["tier"] != "유의미":
            continue
        feat = m["feature"]
        if m["direction"] == "+":
            adv = sp.ADVICE.get(feat, "계획서에 관련 내용을 명기하세요.")
            lines.append(f"- ⚠ {feat}: {adv[:120]}")
        else:
            lines.append(
                f"- ⚠ {feat}: 이 피처는 합격과 부(負)상관입니다. 본문에서 과도하게 강조하지 마세요."
            )
    return "\n".join(lines) if lines else "(개선 포인트 없음)"


# ── 생성 ──────────────────────────────────────────────────────────────────────

_BATCH_SIZE = 4  # 한 번 호출에 생성할 최대 섹션 수 (토큰 한도 대비)


def _generate_batch(
    client: Anthropic,
    item: str,
    team: str,
    program: str,
    batch: list[str],
    advice: str,
    guide: str,
) -> dict[str, str]:
    """섹션 배치 하나를 Sonnet으로 생성."""
    sec_list = "\n".join(
        f"섹션 {SECTION_NAMES.index(s) + 1}. {s}"
        for s in batch
        if s in SECTION_NAMES
    )
    fmt_example = "\n".join(
        f"{_SEP_START.format(name=s)}\n(섹션 내용)\n{_SEP_END.format(name=s)}"
        for s in batch[:2]
    )
    system_prompt = f"""당신은 한국 정부지원사업 사업계획서 전문 작성가입니다.
합격 가능성을 높이는 구체적인 사업계획서를 작성하세요.

## 합격률 향상 핵심 요소 (반드시 포함)
{advice}

## 섹션 작성 원칙
- 수치와 근거 필수. 없으면 [수치 필요] / [출처 필요]로 표기.
- 심사위원이 30초 안에 핵심 파악 가능하도록 구조화.
- 주관적 주장 대신 데이터·계약·인터뷰 결과 중심.
- 각 섹션 A4 1~2페이지 분량.
- 경쟁사 비교표, 타깃 고객 페르소나, 월별 마일스톤 등 구체적 내용 포함.

## 출력 형식 (정확히 준수)
각 섹션을 반드시 아래 구분자로 감싸서 출력:
{fmt_example}

## 사업계획서 가이드 (참고)
{guide}
"""
    user_msg = (
        f"지원사업: {program}\n"
        f"아이템: {item}\n"
        f"팀 구성: {team}\n\n"
        f"작성할 섹션:\n{sec_list}\n\n"
        "각 섹션을 구분자 형식에 맞게 작성하세요."
    )
    msg = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=8192,
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = "".join(getattr(b, "text", "") for b in msg.content)
    return _parse_sections(raw, batch)


def generate_sections(
    client: Anthropic,
    item: str,
    team: str,
    program: str,
    target_sections: list[str],
    advice_block: str = "",
) -> dict[str, str]:
    """Claude Sonnet으로 지정 섹션 초안 생성 (배치 분할)."""
    advice = advice_block or _default_advice_block()
    guide  = _load_guide()

    result: dict[str, str] = {}
    for i in range(0, len(target_sections), _BATCH_SIZE):
        batch = target_sections[i : i + _BATCH_SIZE]
        result.update(_generate_batch(client, item, team, program, batch, advice, guide))
    return result


def _parse_sections(raw: str, target_sections: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in target_sections:
        if name not in SECTION_NAMES:
            continue
        start = re.escape(_SEP_START.format(name=name))
        end   = re.escape(_SEP_END.format(name=name))
        m = re.search(start + r"(.+?)" + end, raw, re.S)
        if m:
            result[name] = m.group(1).strip()
            continue

        # 폴백: "섹션 N. 이름" 헤더로 탐지
        idx      = SECTION_NAMES.index(name) + 1
        next_idx = idx + 1
        pattern  = (
            rf"(?:섹션\s*{idx}[.\s]+{re.escape(name)})\s*\n"
            rf"(.+?)"
            rf"(?=섹션\s*{next_idx}[.\s]|\Z)"
        )
        m2 = re.search(pattern, raw, re.S)
        result[name] = m2.group(1).strip() if m2 else f"[섹션 파싱 실패: {name}]"
    return result


# ── 약점 탐지 ─────────────────────────────────────────────────────────────────

def find_weak_sections(misses: list[dict]) -> list[str]:
    miss_features = {m["feature"] for m in misses if m["tier"] == "유의미"}
    seen: set[str] = set()
    weak: list[str] = []
    for sec, feats in SECTION_FEATURES.items():
        if any(f in miss_features for f in feats) and sec not in seen:
            seen.add(sec)
            weak.append(sec)
    return weak


# ── 저장 ──────────────────────────────────────────────────────────────────────

def _draft_to_text(sections: dict[str, str]) -> str:
    lines: list[str] = []
    for i, name in enumerate(SECTION_NAMES, 1):
        content = sections.get(name, "")
        if content:
            lines += [f"## 섹션 {i}. {name}", "", content, ""]
    return "\n".join(lines)


def _save_outputs(
    sections: dict[str, str],
    score_doc: dict,
    score_final: dict | None,
    item: str,
    team: str,
    program: str,
    iterations: int,
    mode: str,
) -> tuple[Path, Path]:
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    prob = score_doc["prob"] * 100

    header = "\n".join([
        "# 사업계획서 초안 (AI 자동 생성)",
        "",
        f"- **아이템**: {item}",
        f"- **팀**: {team}",
        f"- **지원사업**: {program}",
        f"- **생성일시**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- **서류합격 예측**: {prob:.0f}%",
        f"- **재생성 반복**: {iterations}회 / 모델: {mode}",
        "",
        "---",
        "",
    ])
    draft_path = DRAFTS / f"draft_{ts}.md"
    draft_path.write_text(header + _draft_to_text(sections), encoding="utf-8")

    score_lines = [
        "# 채점 결과",
        "",
        f"- 아이템: {item}",
        f"- 지원사업: {program}",
        f"- 서류합격 확률: {prob:.0f}%",
    ]
    if score_final:
        score_lines.append(f"- 최종합격 확률(서류합격 가정): {score_final['prob']*100:.0f}%")

    hits_sig = [h for h in score_doc["hits"] if h["tier"] == "유의미"]
    if hits_sig:
        score_lines += ["", "## 충족 피처 (유의미)", ""]
        for h in hits_sig:
            score_lines.append(f"- ✅ {h['feature']} ({h['direction']}방향)")

    miss_sig = [m for m in score_doc["misses"] if m["tier"] == "유의미"]
    if miss_sig:
        score_lines += ["", "## 미충족 피처 (유의미, 개선 필요)", ""]
        for m in miss_sig:
            adv = sp.ADVICE.get(m["feature"], "")
            score_lines.append(f"- ❌ {m['feature']} ({m['direction']}방향)")
            if adv:
                score_lines.append(f"  → {adv}")

    score_path = DRAFTS / f"score_{ts}.md"
    score_path.write_text("\n".join(score_lines), encoding="utf-8")

    return draft_path, score_path


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="사업계획서 초안 자동 생성 CLI")
    parser.add_argument("--item",         required=True, help="아이템 한 줄 설명")
    parser.add_argument("--team",         required=True, help="팀 구성 설명")
    parser.add_argument("--program",      required=True, help="지원사업명")
    parser.add_argument(
        "--target-score", type=float, default=65.0, dest="target_score",
        help="목표 서류합격 확률 %% (기본 65)",
    )
    parser.add_argument(
        "--max-iter", type=int, default=3, dest="max_iter",
        help="최대 약점 섹션 재생성 반복 횟수 (기본 3)",
    )
    parser.add_argument(
        "--mode", choices=["all", "7types"], default="7types",
        help="회귀모델 버전 (기본 7types)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[오류] ANTHROPIC_API_KEY 환경변수 없음 (.env 파일 확인)", file=sys.stderr)
        sys.exit(1)

    client     = Anthropic(api_key=api_key)
    structurer = Structurer(api_key=api_key)
    weights    = sp.load_weights(args.mode)

    # ── Step 1: 전체 초안 생성 ─────────────────────────────────────────────────
    print(f"\n[1] 전체 초안 생성 중 (Claude Sonnet) ...", flush=True)
    sections = generate_sections(
        client, args.item, args.team, args.program, SECTION_NAMES
    )
    generated = sum(1 for v in sections.values() if not v.startswith("[섹션 파싱 실패"))
    print(f"    {generated}/{len(SECTION_NAMES)} 섹션 생성 완료", flush=True)

    score_doc: dict | None   = None
    score_final: dict | None = None
    iteration = 0

    # ── Step 2~N: 채점 → 약점 재생성 루프 ────────────────────────────────────
    while True:
        full_text = _draft_to_text(sections)
        step = iteration + 2
        print(f"\n[{step}] 루브릭 피처 추출 중 (Claude Haiku) ...", flush=True)

        features    = structurer.structure(full_text)
        score_doc   = sp.compute_score(features, weights, "서류합격")
        score_final = sp.compute_score(features, weights, "최종합격")

        print(f"    {structurer.usage_str()}", flush=True)

        if not score_doc:
            print("[오류] 서류합격 모델 없음. data/rubric_weights_*.json 확인.", file=sys.stderr)
            sys.exit(1)

        prob   = score_doc["prob"] * 100
        n_miss = sum(1 for m in score_doc["misses"] if m["tier"] == "유의미")
        print(
            f"    서류합격 확률: {prob:.0f}%  (목표: {args.target_score}%)  "
            f"| 유의미 미충족: {n_miss}개",
            flush=True,
        )

        if prob >= args.target_score:
            print(f"    ✅ 목표 달성 ({prob:.0f}% ≥ {args.target_score}%)", flush=True)
            break

        if iteration >= args.max_iter:
            print(
                f"    ⚠ 최대 반복 횟수 도달 ({args.max_iter}회). 현재 초안으로 저장합니다.",
                flush=True,
            )
            break

        weak_sections = find_weak_sections(score_doc["misses"])
        if not weak_sections:
            print("    개선 가능한 약점 섹션이 없습니다. 종료합니다.", flush=True)
            break

        advice_block = _miss_advice_block(score_doc["misses"])
        iteration += 1
        print(
            f"\n    재생성 [{iteration}/{args.max_iter}]: {', '.join(weak_sections)}",
            flush=True,
        )
        new_sections = generate_sections(
            client, args.item, args.team, args.program,
            weak_sections, advice_block,
        )
        sections.update(new_sections)

    # ── 저장 ──────────────────────────────────────────────────────────────────
    draft_path, score_path = _save_outputs(
        sections, score_doc, score_final,
        args.item, args.team, args.program,
        iteration, args.mode,
    )

    sep = "=" * 60
    print(f"\n{sep}")
    print(f"최종 서류합격 예측 : {score_doc['prob']*100:.0f}%")
    if score_final:
        print(f"최종합격 예측(가정): {score_final['prob']*100:.0f}%")
    print(f"초안 저장          : {draft_path}")
    print(f"채점 결과 저장     : {score_path}")
    print(f"{sep}\n")


if __name__ == "__main__":
    main()
