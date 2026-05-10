"""루브릭 재구성 — 통계적으로 무의미한 피처 제거, 유의미+잠정만 유지.

사용법:
  python rubric_rebuild.py --mode all     # 전체 사업유형 기반 (기본값)
  python rubric_rebuild.py --mode 7types  # 7개 주요 유형 기반

입력: data/rubric_weights_{mode}.json
출력: reports/rubric_{mode}.md          (사람이 읽는 루브릭)
       data/rubric_{mode}_clean.json     (시스템용 가중치 JSON)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"


def p_to_weight(p: float) -> int:
    if p < 0.01:  return 5
    if p < 0.05:  return 4
    if p < 0.1:   return 3
    if p < 0.2:   return 2
    return 1   # 0.2 ≤ p < 0.3


def load_weights(mode: str) -> dict:
    raw = (DATA / f"rubric_weights_{mode}.json").read_text(encoding="utf-8")
    raw = raw.replace(": NaN", ": null").replace(":NaN", ":null")
    return json.loads(raw)


def build_feature_table(weights: dict) -> list[dict]:
    """서류+최종 피처 통합, 중복이면 낮은 p 기준 유지."""
    seen: dict[str, dict] = {}

    for model_key, model_data in weights.items():
        model_label = "서류" if "서류" in model_key else "최종"
        for tier, items in [("유의미", model_data["significant"]),
                            ("잠정",   model_data["tentative"])]:
            for feat in items:
                nm = feat["feature"]
                entry = {
                    "feature": nm,
                    "tier": tier,
                    "direction": feat["direction"],
                    "p": feat["p"],
                    "or": feat["or"],
                    "weight": p_to_weight(feat["p"]),
                    "lasso_strength": feat["lasso_strength"],
                    "models": {model_label},
                }
                if nm in seen:
                    existing = seen[nm]
                    existing["models"].add(model_label)
                    if tier == "유의미":
                        existing["tier"] = "유의미"
                    if feat["p"] < existing["p"]:
                        existing.update({
                            "p": feat["p"],
                            "weight": p_to_weight(feat["p"]),
                            "direction": feat["direction"],
                            "or": feat["or"],
                        })
                else:
                    seen[nm] = entry

    rows = list(seen.values())
    for v in rows:
        v["models"] = sorted(v["models"])
    rows.sort(key=lambda x: (0 if x["tier"] == "유의미" else 1, x["p"]))
    return rows


def collect_removed(rows: list[dict], mode: str) -> list[str]:
    """무의미 피처 목록 — regression_summary md에서 파싱."""
    kept = {r["feature"] for r in rows}
    removed: set[str] = set()
    for fname in [f"regression_summary_서류_{mode}.md", f"regression_summary_최종_{mode}.md"]:
        fpath = REPORTS / fname
        if not fpath.exists():
            continue
        in_null = False
        for line in fpath.read_text(encoding="utf-8").splitlines():
            if "❌ 무의미한 피처" in line:
                in_null = True
                continue
            if in_null and line.startswith("##"):
                in_null = False
            if in_null and line.startswith("| ") and "유형" not in line and "피처" not in line:
                parts = line.split("|")
                if len(parts) >= 2:
                    nm = parts[1].strip()
                    if nm and nm not in kept:
                        removed.add(nm)
    return sorted(removed)


def score_note(feat: dict) -> str:
    d = feat["direction"]
    or_val = feat["or"]
    w = feat["weight"]
    if d == "+":
        if or_val and or_val > 1:
            return f"보유/해당 시 +{w}점 (합격 가능성 {or_val:.1f}배)"
        return f"수치↑ 시 +{w}점"
    else:
        if or_val and or_val < 1:
            return f"보유/해당 시 -{w}점 (합격 가능성 {or_val:.1f}배↓)"
        return f"수치↑ 시 -{w}점 (반직관적 — confound 의심)"


def write_rubric_md(rows: list[dict], weights: dict, removed: list[str]) -> str:
    sig  = [r for r in rows if r["tier"] == "유의미"]
    tent = [r for r in rows if r["tier"] == "잠정"]

    서류_n   = weights["서류합격"]["n"]
    최종_n   = weights["최종합격"]["n"]
    서류_auc = weights["서류합격"]["auc_lasso"]
    최종_auc = weights["최종합격"]["auc_lasso"]
    서류_del = weights["서류합격"]["null_count"]
    최종_del = weights["최종합격"]["null_count"]

    L = [
        "# 재구성 루브릭 v2",
        "**통계적으로 유의미한 피처만 추출 — 무의미 피처 제거**",
        "",
        f"> 서류 모델 n={서류_n}, AUC={서류_auc:.3f} / 최종 모델 n={최종_n}, AUC={최종_auc:.3f}",
        f"> 제거: 서류 {서류_del}개 + 최종 {최종_del}개 (중복 포함)",
        "",
        "---",
        "",
        "## 배점 기준표",
        "",
        "| 등급 | p-value 기준 | 배점 |",
        "|---|---|---|",
        "| ✅ 강한 유의 | p < 0.01 | **5점** |",
        "| ✅ 유의 | 0.01 ≤ p < 0.05 | **4점** |",
        "| ✅ 약한 유의 | 0.05 ≤ p < 0.1 | **3점** |",
        "| ⚠️ 잠정 (강) | 0.1 ≤ p < 0.2 | **2점** |",
        "| ⚠️ 잠정 (약) | 0.2 ≤ p < 0.3 | **1점** |",
        "| ❌ 제거 | p ≥ 0.3 | 0점 |",
        "",
        "---",
        "",
        "## ✅ 유의미한 피처",
        f"**{len(sig)}개** — 루브릭에 반드시 포함, 주요 합불 판단 근거",
        "",
        "| # | 피처 | 방향 | p | OR | 배점 | 적용 | 채점 기준 |",
        "|---|---|:--:|--:|--:|:--:|:--:|---|",
    ]

    for i, r in enumerate(sig, 1):
        arrow = "↑합격" if r["direction"] == "+" else "↓합격"
        or_s  = f"{r['or']:.2f}" if r["or"] else "—"
        models = "+".join(r["models"])
        note = score_note(r)
        L.append(f"| {i} | **{r['feature']}** | {arrow} | **{r['p']:.3f}** | {or_s} | {r['weight']}점 | {models} | {note} |")

    # 반직관적 신호 경고
    neg_sig = [r for r in sig if r["direction"] == "-"]
    if neg_sig:
        L += [
            "",
            "> **⚠️ 반직관적 신호 (↓합격)**  ",
            "> 아래 피처는 '보유 시 합격률 오히려 하락'. 프로그램 유형별 confound 가능성 있음.  ",
            "> 단독 감점 기준 적용 전 유형별 검증 권장.",
        ]
        for r in neg_sig:
            or_s = f"OR={r['or']:.2f}" if r["or"] else ""
            L.append(f"> - **{r['feature']}** {or_s} p={r['p']:.3f}")

    # 유의미 스코어카드 요약
    pos_total = sum(r["weight"] for r in sig if r["direction"] == "+")
    neg_total = sum(r["weight"] for r in sig if r["direction"] == "-")
    L += [
        "",
        f"> 유의미 피처 긍정 합산 최대 **+{pos_total}점**, 부정 합산 최대 **-{neg_total}점**",
        "",
        "---",
        "",
        "## ⚠️ 잠정 피처",
        f"**{len(tent)}개** — 보조 지표로 활용, 합불 단독 판단 금지",
        "",
        "| # | 피처 | 방향 | p | OR | 배점 | 적용 |",
        "|---|---|:--:|--:|--:|:--:|:--:|",
    ]

    for i, r in enumerate(tent, 1):
        arrow = "↑합격" if r["direction"] == "+" else "↓합격"
        or_s  = f"{r['or']:.2f}" if r["or"] else "—"
        models = "+".join(r["models"])
        L.append(f"| {i} | {r['feature']} | {arrow} | {r['p']:.3f} | {or_s} | {r['weight']}점 | {models} |")

    # 제거 피처
    L += [
        "",
        "---",
        "",
        f"## ❌ 제거된 피처 — {len(removed)}개",
        "",
        "아래 피처는 합격/불합격 예측에 통계적으로 무의미 (p ≥ 0.3). **루브릭에서 삭제 권장.**",
        "",
    ]
    # 3열로 출력
    for j in range(0, len(removed), 3):
        chunk = removed[j:j+3]
        L.append("- " + " / ".join(f"`{nm}`" for nm in chunk))

    # 실사용 체크리스트
    L += [
        "",
        "---",
        "",
        "## 📋 실사용 체크리스트",
        "",
        "### 서류 심사 (유의미 피처)",
        "",
        "| 항목 | 점수 | 방향 | 체크 |",
        "|---|:--:|:--:|:--:|",
    ]
    for r in sig:
        if "서류" in r["models"]:
            sym = "➕" if r["direction"] == "+" else "➖"
            L.append(f"| {r['feature']} | {r['weight']}점 | {sym} | ☐ |")

    L += [
        "",
        "### 최종 심사 추가 항목 (최종 유의미 피처)",
        "",
        "| 항목 | 점수 | 방향 | 체크 |",
        "|---|:--:|:--:|:--:|",
    ]
    for r in sig:
        if "최종" in r["models"]:
            sym = "➕" if r["direction"] == "+" else "➖"
            L.append(f"| {r['feature']} | {r['weight']}점 | {sym} | ☐ |")

    # 해석 주의사항
    L += [
        "",
        "---",
        "",
        "## 📝 해석 유의사항",
        "",
        "1. **OR(Odds Ratio)**: 이진(Y/N) 피처에만 유효. 수치형은 '—'.",
        "2. **↓합격 방향**: 보유 시 오히려 불합격률 상승 — 프로그램 유형별 혼재 가능. "
        "특히 *영업·마케팅 관련 인력/네트워크*는 기술 중심 프로그램(예창 등)에서 패널티 작용 의심.",
        "3. **잠정 피처**: 단독 합불 판정 금지. 부가점수(+1~2점) 수준으로만 활용.",
        f"4. **AUC 한계**: 서류 {서류_auc:.3f} / 최종 {최종_auc:.3f}. "
        "최종 단계는 기존 50피처로 변별력이 낮음 → 발표/면접 요소 보강 필요.",
        "5. **다음 단계**: LLM으로 기존 루브릭에 없던 신규 합격 요인 탐색 (로드맵 2단계).",
    ]

    return "\n".join(L)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["all", "7types"], default="all",
                        help="all: 전체 사업유형 / 7types: 7개 주요 유형")
    args = parser.parse_args()
    mode = args.mode
    print(f"[mode] {mode}")

    weights = load_weights(mode)
    rows = build_feature_table(weights)
    removed = collect_removed(rows, mode)

    sig  = [r for r in rows if r["tier"] == "유의미"]
    tent = [r for r in rows if r["tier"] == "잠정"]

    print(f"[재구성] 유의미 {len(sig)}개 / 잠정 {len(tent)}개 / 제거 {len(removed)}개")

    md = write_rubric_md(rows, weights, removed)
    out_md = REPORTS / f"rubric_{mode}.md"
    out_md.write_text(md, encoding="utf-8")
    print(f"[saved] {out_md.name}")

    clean_json = {
        "version": mode,
        "features": [
            {
                "feature": r["feature"],
                "tier": r["tier"],
                "direction": r["direction"],
                "weight": r["weight"],
                "p": r["p"],
                "or": r["or"],
                "models": r["models"],
                "type": "binary" if r["or"] is not None else "numeric",
            }
            for r in rows
        ],
        "removed": removed,
    }
    out_json = DATA / f"rubric_{mode}_clean.json"
    out_json.write_text(json.dumps(clean_json, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[saved] {out_json.name}")

    print("\n=== 유의미 피처 ===")
    for r in sig:
        arrow = "↑" if r["direction"] == "+" else "↓"
        print(f"  {arrow} [{r['weight']}pt] {r['feature']:<45} p={r['p']:.3f}  {'+'.join(r['models'])}")
    print("\n=== 잠정 피처 ===")
    for r in tent:
        arrow = "↑" if r["direction"] == "+" else "↓"
        print(f"  {arrow} [{r['weight']}pt] {r['feature']:<45} p={r['p']:.3f}  {'+'.join(r['models'])}")
    print(f"\n=== 제거 피처 ({len(removed)}개) ===")
    for nm in removed:
        print(f"  ✗ {nm}")


if __name__ == "__main__":
    main()
