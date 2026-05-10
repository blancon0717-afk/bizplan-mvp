"""
사업계획서 자동 평가 CLI
사용:
  python score_plan.py plan.pdf --program 예비창업패키지
  python score_plan.py plan.pdf --program 초기창업패키지 --mode 7types
  python score_plan.py --text "아이템 설명..." --program 청년창업사관학교
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)

sys.stdout.reconfigure(encoding="utf-8")


# ── 피처 → 피드백 어드바이스 매핑 ─────────────────────────────────────────────

ADVICE: dict[str, str] = {
    "FGI": (
        "FGI(심층 인터뷰) 결과가 없습니다. "
        "타깃 고객 5명 이상 인터뷰 후 결과를 수치로 정리해 계획서에 명기하세요."
    ),
    "수요처 확보 여부": (
        "수요처가 없습니다. "
        "구매의향서 1건이라도 확보하면 합격 가능성이 유의미하게 오릅니다."
    ),
    "투자의향서 확보 여부": (
        "투자의향서(LOI)가 없습니다. "
        "엔젤·VC에 접촉해 1건이라도 확보하세요 (OR≈1.56)."
    ),
    "MOU 보유 개수": (
        "MOU가 없습니다. "
        "파트너사 협약서 최소 1건을 확보하면 최종합격에 유리합니다."
    ),
    "필수 재료 공급처 네트워크 보유": (
        "원재료 공급처가 명시되지 않았습니다. "
        "Supplier 이름과 계약 여부를 계획서에 추가하세요 (OR≈1.49)."
    ),
    "아이템특이성": (
        "차별점이 불명확합니다. "
        "'기존 대비 XX% 개선', '국내 유일 YY 기술' 형태로 명기하세요."
    ),
    "누적 투자": (
        "투자 이력이 없습니다. "
        "없는 경우 매출·인증·수상 이력으로 신뢰도를 보완하세요."
    ),
    "비즈니스 모델 다양화": (
        "BM이 과다하게 다양하면 오히려 감점입니다. "
        "핵심 수익 모델 1~2개에 집중하세요."
    ),
    "소재지+주관기관 일치 여부": (
        "회사 소재지와 주관기관 지역이 다릅니다. "
        "지역 일치 여부를 확인하세요."
    ),
    "제조 전문 인력 (기술자) 보유(팀원 기준)": (
        "제조 기술 인력이 없습니다. "
        "팀 구성에 기술자를 추가하거나 외부 협력 기술자를 계획서에 명기하세요."
    ),
    "실제 실험 사진 보유 여부": (
        "실험 사진이 있지만 합격과 부(負)상관입니다. "
        "실험 데이터보다 시장 검증 결과를 계획서 앞쪽에 배치하세요."
    ),
    "아이템 관련 경력 증빙": (
        "경력 증빙이 있지만 합격과 부상관입니다. "
        "경력 나열보다 현재 시장 수요 증거를 강화하세요."
    ),
    "자체 생산 시설 보유": (
        "자체 생산 시설이 없습니다. "
        "OEM·위탁 생산 계획과 공급처 계약 여부를 명기하세요 (최종합격 OR≈2.12)."
    ),
    "수요처 네트워크 보유": (
        "잠재 수요처 명단이 없습니다. "
        "고객사 로고·명칭을 1개라도 명기하면 최종합격에 유리합니다 (OR≈1.72)."
    ),
    "견적서 보유 개수": (
        "견적서가 없습니다. "
        "파트너·고객사에서 견적서 1건이라도 확보하세요."
    ),
}

# 피처 → 심사위원 피드백 태그 매핑 (feedback_raw.json 용)
FEATURE_TO_TAG: dict[str, str] = {
    "FGI": "시장/고객",
    "MVP 테스트 실행 여부": "시장/고객",
    "수요처 네트워크 보유": "시장/고객",
    "수요처 확보 여부": "시장/고객",
    "투자의향서 확보 여부": "투자/재무",
    "MOU 보유 개수": "파트너십/네트워크",
    "필수 재료 공급처 네트워크 보유": "파트너십/네트워크",
    "비즈니스 모델 다양화": "비즈니스모델/수익구조",
    "아이템특이성": "차별성/경쟁사분석",
    "제조 전문 인력 (기술자) 보유(팀원 기준)": "팀구성",
    "자체 생산 시설 보유": "기술/제품완성도",
}

# 피처 → 발표 Q&A 태그 매핑 (presentation_qa_matched.json 용)
FEATURE_TO_QA_TAG: dict[str, list[str]] = {
    "FGI":                                      ["시장", "고객", "검증"],
    "MVP 테스트 실행 여부":                      ["검증", "제품/서비스 경쟁력"],
    "수요처 확보 여부":                          ["고객", "수익/비용구조"],
    "수요처 네트워크 보유":                      ["고객", "supply chain"],
    "투자의향서 확보 여부":                      ["자금조달"],
    "MOU 보유 개수":                             ["supply chain"],
    "필수 재료 공급처 네트워크 보유":            ["supply chain"],
    "아이템특이성":                              ["제품/서비스 경쟁력"],
    "비즈니스 모델 다양화":                      ["수익/비용구조"],
    "누적 투자":                                 ["자금조달"],
    "제조 전문 인력 (기술자) 보유(팀원 기준)":   ["팀", "기술"],
    "자체 생산 시설 보유":                       ["supply chain", "제품/서비스 경쟁력"],
    "견적서 보유 개수":                          ["수익/비용구조"],
    "실제 실험 사진 보유 여부":                  ["기술", "검증"],
}


# ── 유틸 ──────────────────────────────────────────────────────────────────────

def sigmoid(x: float) -> float:
    x = max(-30.0, min(30.0, x))
    return 1.0 / (1.0 + math.exp(-x))


def _load_json_with_nan(path: Path) -> dict:
    """NaN 리터럴이 포함된 비표준 JSON도 읽는다."""
    raw = path.read_text(encoding="utf-8")
    raw = re.sub(r"\bNaN\b", "null", raw)
    return json.loads(raw)


def load_weights(mode: str = "7types") -> dict:
    return _load_json_with_nan(DATA / f"rubric_weights_{mode}.json")


def load_feedback_db() -> list[dict]:
    p = DATA / "feedback_raw.json"
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_qa_db() -> list[dict]:
    p = DATA / "presentation_qa_matched.json"
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return json.load(f).get("rows", [])


def get_qa_questions(tags: list[str], db: list[dict], n: int = 3) -> list[dict]:
    """태그 목록에 매칭되는 발표 질문을 최대 n개 반환 (중복 질문 제외)."""
    seen: set[str] = set()
    out: list[dict] = []
    for tag in tags:
        for row in db:
            if row.get("태그") != tag:
                continue
            q = (row.get("질문") or "").strip()
            if not q or q in seen:
                continue
            seen.add(q)
            out.append({
                "태그": tag,
                "질문": q,
                "답변": (row.get("답변") or "").strip() or None,
            })
            if len(out) >= n:
                return out
    return out


def is_active(val) -> bool:
    if isinstance(val, str):
        return val.strip().upper() in ("Y", "O")
    if isinstance(val, (int, float)):
        return float(val) > 0
    return False


# ── 점수 계산 ──────────────────────────────────────────────────────────────────

def compute_score(features: dict, weights: dict, target: str) -> dict | None:
    if target not in weights:
        return None
    model = weights[target]
    n, n_pos = model["n"], model["n_pos"]
    base_rate = n_pos / n
    intercept = math.log(base_rate / (1.0 - base_rate))

    log_odds = intercept
    hits: list[dict] = []
    misses: list[dict] = []

    def _process(fw_list: list[dict], tier: str) -> None:
        nonlocal log_odds
        for fw in fw_list:
            feat = fw["feature"]
            direction = fw["direction"]
            lasso = fw.get("lasso_strength") or 0.0
            coef = lasso * (1.0 if direction == "+" else -1.0)

            val = features.get(feat)
            active = is_active(val) if val is not None else False
            log_odds += coef * (1.0 if active else 0.0)

            entry = {**fw, "tier": tier, "active": active}
            # 유리한 신호: (+있음) 또는 (-없음)
            if (direction == "+" and active) or (direction == "-" and not active):
                hits.append(entry)
            else:
                misses.append(entry)

    _process(model.get("significant", []), "유의미")
    _process(model.get("tentative", []), "잠정")

    return {
        "prob": sigmoid(log_odds),
        "base_rate": base_rate,
        "auc": model.get("auc_lasso"),
        "hits": hits,
        "misses": misses,
    }


# ── 피드백 예시 조회 ──────────────────────────────────────────────────────────

def get_examples(tag: str, db: list[dict], n: int = 1) -> list[str]:
    out = []
    for item in db:
        tags = item.get("태그") or []
        if tag in tags and item.get("피드백_내용"):
            out.append(item["피드백_내용"])
        if len(out) >= n:
            break
    return out


# ── 리포트 ────────────────────────────────────────────────────────────────────

def _or_str(fw: dict) -> str:
    v = fw.get("or")
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return f"p={fw.get('p', '?'):.3f}"
    return f"OR={v:.2f}, p={fw.get('p', '?'):.3f}"


def build_report(
    program: str,
    features: dict,
    score_doc: dict,
    score_final: dict | None,
    mode: str,
    source: str,
    feedback_db: list[dict],
    qa_db: list[dict] | None = None,
) -> str:
    lines: list[str] = []
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    prob_d = score_doc["prob"]
    n_stars = round(prob_d * 5)
    stars = "★" * n_stars + "☆" * (5 - n_stars)

    lines += [
        "# 사업계획서 자동 평가 리포트",
        "",
        f"- **분석일시**: {ts}",
        f"- **지원사업**: {program or '(미지정)'}",
        f"- **입력**: {source}",
        f"- **모델**: {mode} (AUC≈{score_doc['auc']:.3f})",
        "",
        "---",
        "",
        "## 📊 합격 가능성 예측",
        "",
        "| 단계 | 예측 확률 | 신호 강도 |",
        "|------|----------|---------|",
        f"| **서류합격** | **{prob_d*100:.0f}%** | {stars} |",
    ]
    if score_final:
        pf = score_final["prob"]
        nf = round(pf * 5)
        sf = "★" * nf + "☆" * (5 - nf)
        lines.append(f"| 최종합격 (서류합격 가정) | {pf*100:.0f}% | {sf} |")

    base_pct = score_doc["base_rate"] * 100
    lines += [
        "",
        f"> 전체 평균 서류합격률 {base_pct:.0f}% 대비 상대 예측값.",
        f"> 모델 AUC≈{score_doc['auc']:.3f} — 참고지표로만 사용하세요.",
        "",
    ]

    # 강점
    strong_hits = [h for h in score_doc["hits"] if h["tier"] == "유의미"]
    if strong_hits:
        lines += ["## ✅ 강점 (유의미 피처 충족)", ""]
        for h in strong_hits:
            label = "합격 신호 보유" if h["direction"] == "+" else "탈락 위험 없음"
            lines.append(f"- **{h['feature']}** — {label} ({_or_str(h)})")
        lines.append("")

    # 핵심 개선 항목
    sig_misses = [m for m in score_doc["misses"] if m["tier"] == "유의미"]
    if sig_misses:
        lines += ["## ⚠️ 핵심 개선 항목 (유의미 피처 미충족)", ""]
        for m in sig_misses:
            if m["direction"] == "+":
                status = "미보유 — 합격 신호 누락"
            else:
                status = "보유 중 — 탈락 위험 신호 발동"
            lines += [
                f"### {m['feature']} ({status})",
                f"> {ADVICE.get(m['feature'], '계획서에 관련 내용을 명기하세요.')}",
                f"*통계: {_or_str(m)}*",
                "",
            ]
            tag = FEATURE_TO_TAG.get(m["feature"])
            if tag and feedback_db:
                examples = get_examples(tag, feedback_db)
                if examples:
                    snippet = examples[0][:120].replace("\n", " ")
                    lines += [
                        f"**심사위원 실제 피드백 예시** ({tag}):",
                        f'> "{snippet}..."',
                        "",
                    ]

    # 추가 보강
    tent_misses = [m for m in score_doc["misses"] if m["tier"] == "잠정"]
    if tent_misses:
        lines += ["## 💡 추가 보강 포인트 (잠정 피처, 상위 5개)", ""]
        for m in tent_misses[:5]:
            adv = ADVICE.get(m["feature"], "보강 고려")
            lines.append(f"- **{m['feature']}** — {adv}")
        lines.append("")

    # 최종합격 준비 항목
    if score_final:
        fin_misses = [m for m in score_final["misses"] if m["tier"] == "유의미"]
        if fin_misses:
            lines += ["## 🎯 최종합격 추가 준비 항목", ""]
            for m in fin_misses:
                adv = ADVICE.get(m["feature"], "")
                lines.append(f"- **{m['feature']}** ({_or_str(m)})")
                if adv:
                    lines.append(f"  > {adv}")
            lines.append("")

    # 아이템 키워드
    kw = features.get("특이성_키워드") or []
    if kw:
        lines += [
            "## 🔑 아이템 강조 키워드 (AI 추출)",
            "",
            ", ".join(str(k) for k in kw),
            "",
        ]

    # 발표 예상 질문 (약점 피처 기반)
    if qa_db:
        all_misses = score_doc["misses"]
        if score_final:
            all_misses = all_misses + [
                m for m in score_final["misses"]
                if m["feature"] not in {x["feature"] for x in all_misses}
            ]

        # 약점 피처에서 Q&A 태그 수집 (유의미 → 잠정 순, 중복 제거)
        qa_tags: list[str] = []
        seen_tags: set[str] = set()
        for m in sorted(all_misses, key=lambda x: 0 if x["tier"] == "유의미" else 1):
            for t in FEATURE_TO_QA_TAG.get(m["feature"], []):
                if t not in seen_tags:
                    seen_tags.add(t)
                    qa_tags.append(t)

        if qa_tags:
            questions = get_qa_questions(qa_tags, qa_db, n=5)
            if questions:
                lines += [
                    "---",
                    "",
                    "## 🎤 발표 심사 예상 질문 (약점 기반)",
                    "",
                    "> 아래 질문들은 귀하의 계획서 약점 피처와 매칭된 실제 심사 질문입니다.",
                    "> 발표 전 각 질문에 대한 답변을 준비하세요.",
                    "",
                ]
                for i, q in enumerate(questions, 1):
                    lines += [f"**Q{i}. [{q['태그']}] {q['질문']}**"]
                    if q["답변"]:
                        snippet = q["답변"][:100].replace("\n", " ")
                        lines += [f"> 참고 답변 예시: _{snippet}{'...' if len(q['답변']) > 100 else ''}_"]
                    lines.append("")

    # 공통 피드백 레퍼런스
    lines += [
        "---",
        "",
        "## 📋 심사위원 피드백 상위 3개 유형 (전체 공통)",
        "",
        "1. **시장/고객** (21%) — 타깃 고객 정의 불명확 → TAM/SAM/SOM 수치 + 페르소나",
        "2. **차별성/경쟁사분석** (17%) — 차별점 미약 → 경쟁사 비교표 + 핵심 차별점 3가지 수치화",
        "3. **진입장벽/보안** (14%) — 기술적 진입장벽 부재 → 특허·독점계약·네트워크효과 명기",
        "",
    ]

    return "\n".join(lines)


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="사업계획서 자동 평가 CLI")
    parser.add_argument("pdf", nargs="?", help="PDF 파일 경로")
    parser.add_argument("--text", help="사업계획서 텍스트 직접 입력")
    parser.add_argument("--program", default="", help="지원사업명 (예: 예비창업패키지)")
    parser.add_argument(
        "--mode", choices=["all", "7types"], default="7types",
        help="회귀모델 버전 (default: 7types, AUC 더 높음)",
    )
    parser.add_argument("--organizer", default="", help="주관기관 (피처 추출 정확도 향상)")
    parser.add_argument("--no-save", action="store_true", help="리포트 파일 저장 안 함")
    args = parser.parse_args()

    # 텍스트 준비
    text = ""
    source = "(텍스트 직접 입력)"

    if args.text:
        text = args.text
    elif args.pdf:
        pdf_path = Path(args.pdf)
        if not pdf_path.exists():
            print(f"[오류] 파일 없음: {pdf_path}", file=sys.stderr)
            sys.exit(1)
        from pdf_extractor import extract_text
        print(f"[1/3] PDF 텍스트 추출: {pdf_path.name} ...", flush=True)
        text = extract_text(pdf_path.read_bytes())
        source = pdf_path.name
    else:
        parser.print_help()
        sys.exit(0)

    if not text.strip():
        print("[오류] 텍스트가 비어 있습니다.", file=sys.stderr)
        sys.exit(1)

    # 피처 추출
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[오류] ANTHROPIC_API_KEY 환경변수 없음", file=sys.stderr)
        sys.exit(1)

    from claude_structurer import Structurer
    print("[2/3] 루브릭 피처 추출 중 (Claude Haiku) ...", flush=True)
    structurer = Structurer(api_key=api_key)
    features = structurer.structure(text, 주관기관=args.organizer or None)
    print(f"      {structurer.usage_str()}", flush=True)

    # 점수 계산
    print("[3/3] 점수 계산 중 ...", flush=True)
    weights = load_weights(args.mode)
    score_doc = compute_score(features, weights, "서류합격")
    score_final = compute_score(features, weights, "최종합격")

    if not score_doc:
        print("[오류] 서류합격 모델 데이터 없음", file=sys.stderr)
        sys.exit(1)

    # 리포트 출력
    feedback_db = load_feedback_db()
    qa_db = load_qa_db()
    report = build_report(
        program=args.program,
        features=features,
        score_doc=score_doc,
        score_final=score_final,
        mode=args.mode,
        source=source,
        feedback_db=feedback_db,
        qa_db=qa_db,
    )

    sep = "=" * 62
    print(f"\n{sep}")
    print(report)
    print(sep)

    if not args.no_save:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = REPORTS / f"score_{ts}.md"
        out.write_text(report, encoding="utf-8")
        print(f"\n[저장됨] {out}")


if __name__ == "__main__":
    main()
