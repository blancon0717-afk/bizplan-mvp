"""통합 회귀분석 — 두 가지 모드 지원.

사용법:
  python regression_summary.py --mode all     # 전체 사업유형 풀링 (기본값)
  python regression_summary.py --mode 7types  # 7개 주요 유형만

모드별 출력:
  all    → data/rubric_weights_all.json    / reports/regression_summary_*_all.md
  7types → data/rubric_weights_7types.json / reports/regression_summary_*_7types.md

전략: Lasso(방향+선택) × 단변량 p-값(유의성) 교차 판정
  - Lasso L1 정규화로 피처 선택 및 방향 결정
  - Fisher/MWU 단변량으로 각 피처의 독립 유의성 검정
  - LGBM feature importance로 보조 검증
  - 사업유형 더미: n >= 5인 유형은 개별 더미, 미만은 기타 그룹
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)

TYPE_DUMMY_MIN_N = 5  # 이 기준 미만 유형은 '기타'로 묶음

MAJOR_7_TYPES = [
    "예비창업패키지", "초기창업패키지", "창업도약패키지", "청년창업사관학교",
    "창업중심대학-예창", "창업중심대학-초창", "창업중심대학-창도",
]

META_COLS = {
    "page_id", "아이템명", "업체명", "연도", "사업분야", "산업군_1차", "산업군_2차",
    "산업군_태그", "주관기관", "계약구분", "서류합격", "최종합격", "문서유형",
    "pdf_url", "pdf_name", "text_chars", "status", "skip_reason", "error",
    "특이성_키워드",
}


def load(mode: str) -> pd.DataFrame:
    f = DATA / "rubric_results_FINAL.csv"
    df = pd.read_csv(f, encoding="utf-8-sig")
    df = df[df["status"] == "ok"].copy()
    df = df[df["사업분야"].notna()].copy()
    if mode == "7types":
        df = df[df["사업분야"].isin(MAJOR_7_TYPES)].copy()
        print(f"[load] {len(df)}행 (7개 주요 유형만)")
    else:
        vc = df["사업분야"].value_counts()
        print(f"[load] {len(df)}행 (전체 사업유형, n>=5: {(vc >= TYPE_DUMMY_MIN_N).sum()}개 개별더미)")
    return df


def feature_columns(df: pd.DataFrame) -> tuple[list[str], list[str]]:
    feat_cols = [c for c in df.columns if c not in META_COLS]
    yn_cols, num_cols = [], []
    for c in feat_cols:
        sample = df[c].dropna().astype(str).str.upper()
        if sample.isin(["Y", "N", "O"]).mean() > 0.7:
            yn_cols.append(c)
        else:
            try:
                pd.to_numeric(df[c], errors="coerce")
                num_cols.append(c)
            except Exception:
                pass
    return yn_cols, num_cols


def y_to_int(v) -> int:
    if isinstance(v, str) and v.strip().upper() in ("Y", "O"):
        return 1
    return 0


def parse_num(v) -> float:
    if pd.isna(v):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").strip()
    m = re.search(r"-?\d+(\.\d+)?", s)
    return float(m.group()) if m else 0.0


def build_matrix(df: pd.DataFrame, label_col: str,
                 yn_cols: list[str], num_cols: list[str]) -> tuple[np.ndarray, np.ndarray, list[str], int]:
    df = df[df[label_col].isin(["합격", "불합격"])].copy()
    y = (df[label_col] == "합격").astype(int).values

    feats: dict[str, np.ndarray] = {}
    for c in yn_cols:
        v = df[c].map(y_to_int).values
        if 0.04 <= v.mean() <= 0.96 and v.std() > 0:
            feats[c] = v.astype(float)
    for c in num_cols:
        v = df[c].map(parse_num).values
        if v.std() > 0:
            feats[c] = v

    # 동적 사업유형 더미: n >= TYPE_DUMMY_MIN_N 유형은 개별 더미, 나머지는 '기타' 그룹
    vc = df["사업분야"].value_counts()
    major_types = vc[vc >= TYPE_DUMMY_MIN_N].index.tolist()
    ref_type = major_types[0]  # 가장 많은 유형을 기준(baseline)으로 제외
    n_type_dummies = 0
    for t in major_types[1:]:
        feats[f"_유형_{t}"] = (df["사업분야"] == t).astype(float).values
        n_type_dummies += 1

    col_names = list(feats.keys())
    X = np.column_stack([feats[c] for c in col_names])
    return X, y, col_names, n_type_dummies


def lasso_run(X, y, names: list[str]) -> dict:
    from sklearn.linear_model import LogisticRegressionCV
    from sklearn.preprocessing import StandardScaler

    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    model = LogisticRegressionCV(
        Cs=30, cv=5, penalty="l1", solver="saga", max_iter=3000,
        scoring="roc_auc", class_weight="balanced", n_jobs=-1, random_state=42,
    )
    model.fit(Xs, y)
    coefs = model.coef_[0]
    selected = [
        {"feature": n, "coef_std": float(c), "direction": "+" if c > 0 else "-",
         "strength": float(abs(c))}
        for n, c in zip(names, coefs) if abs(c) > 0.001
    ]
    try:
        auc = model.scores_[1].mean(axis=0).max()
    except Exception:
        auc = None
    return {
        "selected": sorted(selected, key=lambda x: -x["strength"]),
        "n_total": X.shape[1],
        "n_selected": len(selected),
        "auc_cv": float(auc) if auc else None,
        "C": float(model.C_[0]),
    }


def univariate_sweep(df_sub: pd.DataFrame, label_col: str,
                     yn_cols: list[str], num_cols: list[str]) -> pd.DataFrame:
    from scipy.stats import fisher_exact, mannwhitneyu
    pos = df_sub[df_sub[label_col] == "합격"]
    neg = df_sub[df_sub[label_col] == "불합격"]
    n_pos, n_neg = len(pos), len(neg)

    rows = []
    for c in yn_cols:
        py = int(pos[c].map(y_to_int).sum())
        ny = int(neg[c].map(y_to_int).sum())
        share = (py + ny) / (n_pos + n_neg)
        if share < 0.04 or share > 0.96:
            continue
        tbl = [[py, n_pos - py], [ny, n_neg - ny]]
        try:
            or_, p = fisher_exact(tbl)
            or_ = float(or_)
            p = float(p)
        except Exception:
            or_, p = 1.0, 1.0
        # 방향: OR>1 → 있으면 합격 가능성↑
        direction = "+" if or_ >= 1.0 else "-"
        rows.append({
            "피처": c, "유형": "이진",
            "합격Y율": round(py / n_pos, 3) if n_pos else 0,
            "불합격Y율": round(ny / n_neg, 3) if n_neg else 0,
            "OR": round(or_, 3), "p": round(p, 4), "uni_direction": direction,
        })
    for c in num_cols:
        pv = pos[c].map(parse_num)
        nv = neg[c].map(parse_num)
        if pv.std() == 0 and nv.std() == 0:
            continue
        try:
            _, p = mannwhitneyu(pv, nv, alternative="two-sided")
            p = float(p)
        except Exception:
            p = 1.0
        direction = "+" if float(pv.mean()) >= float(nv.mean()) else "-"
        rows.append({
            "피처": c, "유형": "수치",
            "합격평균": round(float(pv.mean()), 2),
            "불합격평균": round(float(nv.mean()), 2),
            "OR": None, "p": round(p, 4), "uni_direction": direction,
        })
    df_out = pd.DataFrame(rows)
    return df_out


def lgbm_run(X, y, names: list[str]) -> dict:
    from lightgbm import LGBMClassifier
    from sklearn.model_selection import cross_val_score
    m = LGBMClassifier(n_estimators=300, max_depth=4, learning_rate=0.05,
                       class_weight="balanced", verbose=-1, random_state=42)
    try:
        auc = cross_val_score(m, X, y, cv=5, scoring="roc_auc").mean()
    except Exception:
        auc = None
    m.fit(X, y)
    imp = {n: int(i) for n, i in zip(names, m.feature_importances_)
           if i > 0 and not n.startswith("_유형_")}
    return {"auc_cv": float(auc) if auc else None,
            "top": sorted(imp.items(), key=lambda x: -x[1])[:20]}


def classify_features(lasso: dict, unidf: pd.DataFrame) -> tuple[list, list, list]:
    """피처를 유의미 / 잠정 / 무의미 3단계로 분류.

    - 유의미: Lasso 선택 AND 단변량 p < 0.1
    - 잠정: Lasso 선택 AND 단변량 0.1 ≤ p < 0.3
    - 무의미: (Lasso 미선택 OR 단변량 p ≥ 0.3) AND 단변량 p ≥ 0.3
    """
    # 단변량 인덱스 구성
    uni: dict[str, dict] = {}
    for _, row in unidf.iterrows():
        nm = str(row["피처"])
        uni[nm] = {
            "p": float(row["p"]),
            "OR": row.get("OR"),
            "uni_direction": row.get("uni_direction", "+"),
            "유형": row.get("유형", ""),
            "합격Y율": row.get("합격Y율"),
            "불합격Y율": row.get("불합격Y율"),
            "합격평균": row.get("합격평균"),
            "불합격평균": row.get("불합격평균"),
        }

    lasso_set = {s["feature"]: s for s in lasso["selected"]
                 if not s["feature"].startswith("_유형_")}
    all_feat_names = [nm for nm in uni.keys()]

    sig, tentative, null = [], [], []

    for nm in all_feat_names:
        u = uni[nm]
        p = u["p"]
        in_lasso = nm in lasso_set
        lasso_dir = lasso_set[nm]["direction"] if in_lasso else None
        lasso_str = lasso_set[nm]["strength"] if in_lasso else 0.0
        # 최종 방향: Lasso 선택됐으면 Lasso 방향 우선, 아니면 단변량 방향
        direction = lasso_dir if lasso_dir else u["uni_direction"]

        row_data = {
            "feature": nm,
            "direction": direction,
            "p": p,
            "OR": u["OR"],
            "유형": u["유형"],
            "lasso_strength": lasso_str,
            "in_lasso": in_lasso,
            "합격Y율": u.get("합격Y율"),
            "불합격Y율": u.get("불합격Y율"),
            "합격평균": u.get("합격평균"),
            "불합격평균": u.get("불합격평균"),
        }

        if in_lasso and p < 0.1:
            sig.append(row_data)
        elif in_lasso and p < 0.3:
            tentative.append(row_data)
        elif p >= 0.3:
            null.append(row_data)

    sig.sort(key=lambda x: x["p"])
    tentative.sort(key=lambda x: x["p"])
    null.sort(key=lambda x: -x["p"])
    return sig, tentative, null


def write_report(label_name: str, n: int, n_pos: int,
                 lasso: dict, lgbm: dict, unidf: pd.DataFrame,
                 sig: list, tentative: list, null_feats: list,
                 n_type_dummies: int = 0) -> str:
    n_feat_input = lasso["n_total"] - n_type_dummies
    lines = [
        f"# 통합 회귀분석 — {label_name}",
        f"\n**전체 {n}건** (합격 {n_pos} / 불합격 {n - n_pos}, 합격률 {n_pos/n*100:.1f}%)",
        f"- Lasso CV AUC: **{lasso['auc_cv']:.3f}** (C={lasso['C']:.4g})" if lasso.get("auc_cv") else "",
        f"- LightGBM CV AUC: **{lgbm['auc_cv']:.3f}**" if lgbm.get("auc_cv") else "",
        f"- 루브릭 피처 {n_feat_input}개 입력 → Lasso **{lasso['n_selected']}개** 선택\n",
        "> 판정 기준: Lasso 선택(방향) × 단변량 Fisher/MWU p-value(유의성) 교차 판정\n",
    ]

    # ── 유의미 ──
    lines.append("## ✅ 유의미한 피처 (Lasso 선택 + 단변량 p < 0.1)")
    if sig:
        lines.append("| 피처 | 방향 | OR | p | 합격Y/불합격Y (이진) 또는 합격평균/불합격평균 (수치) |")
        lines.append("|---|:--:|--:|--:|---|")
        for r in sig:
            arrow = "↑합격" if r["direction"] == "+" else "↓합격"
            or_str = f"{r['OR']:.2f}" if r["OR"] is not None else "—"
            if r["유형"] == "이진":
                detail = f"Y율 합격{r['합격Y율']*100:.0f}% vs 불합격{r['불합격Y율']*100:.0f}%"
            else:
                detail = f"평균 합격{r['합격평균']:.1f} vs 불합격{r['불합격평균']:.1f}"
            lines.append(f"| {r['feature']} | {arrow} | {or_str} | **{r['p']:.3f}** | {detail} |")
    else:
        lines.append("| (없음) | — | — | — | — |")

    # ── 잠정 ──
    lines.append(f"\n## ⚠️ 잠정 피처 (Lasso 선택 + 0.1 ≤ p < 0.3, {len(tentative)}개)")
    if tentative:
        lines.append("| 피처 | 방향 | OR | p | Lasso 강도 |")
        lines.append("|---|:--:|--:|--:|--:|")
        for r in tentative:
            arrow = "↑합격" if r["direction"] == "+" else "↓합격"
            or_str = f"{r['OR']:.2f}" if r["OR"] is not None else "—"
            lines.append(f"| {r['feature']} | {arrow} | {or_str} | {r['p']:.3f} | {r['lasso_strength']:.3f} |")

    # ── 무의미 ──
    lines.append(f"\n## ❌ 무의미한 피처 (단변량 p ≥ 0.3, {len(null_feats)}개)")
    lines.append("| 피처 | 유형 | p(단변량) | Lasso 선택 |")
    lines.append("|---|:--:|--:|:--:|")
    for r in null_feats:
        lasso_mark = "✓" if r["in_lasso"] else ""
        lines.append(f"| {r['feature']} | {r['유형']} | {r['p']:.3f} | {lasso_mark} |")

    # ── LGBM 보조 ──
    sig_set = {r["feature"] for r in sig}
    lines.append("\n## 🌲 LightGBM 피처 중요도 TOP 20 (보조 검증)")
    lines.append("| 피처 | 중요도 |")
    lines.append("|---|--:|")
    for nm, imp in lgbm["top"]:
        marker = " ✅" if nm in sig_set else ""
        lines.append(f"| {nm}{marker} | {imp} |")

    # ── 전체 정렬 참고 ──
    lines.append("\n## 📋 전체 피처 단변량 p-value 정렬 (참고)")
    lines.append("| 피처 | 유형 | p | 분류 |")
    lines.append("|---|:--:|--:|---|")
    for _, row in unidf.sort_values("p").iterrows():
        nm = row["피처"]
        p = row["p"]
        if nm in {r["feature"] for r in sig}:
            tag = "✅ 유의미"
        elif nm in {r["feature"] for r in tentative}:
            tag = "⚠️ 잠정"
        elif p >= 0.3:
            tag = "❌ 무의미"
        else:
            tag = "△ p<0.3 but Lasso미선택"
        p_str = f"**{p:.3f}**" if p < 0.1 else f"{p:.3f}"
        lines.append(f"| {nm} | {row['유형']} | {p_str} | {tag} |")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["all", "7types"], default="all",
                        help="all: 전체 사업유형 풀링 / 7types: 7개 주요 유형만")
    args = parser.parse_args()
    mode = args.mode
    suffix = mode  # 출력 파일 suffix: all or 7types
    print(f"[mode] {mode}")

    df = load(mode)
    yn_cols, num_cols = feature_columns(df)
    print(f"[features] yn={len(yn_cols)}, num={len(num_cols)}")

    results = {}

    for label_col, label_name, filter_fn in [
        ("서류합격", "서류 모델 (전체 모수)", lambda d: d),
        ("최종합격", "최종 모델 (서류통과자)", lambda d: d[d["서류합격"] == "합격"]),
    ]:
        sub = filter_fn(df)
        sub = sub[sub[label_col].isin(["합격", "불합격"])].copy()
        n = len(sub)
        n_pos = int((sub[label_col] == "합격").sum())
        print(f"\n[{label_name}] n={n}, 합격={n_pos}")
        if n < 50:
            print("  [skip] n<50")
            continue

        X, y, names, n_type_dummies = build_matrix(sub, label_col, yn_cols, num_cols)
        print(f"  행렬: {X.shape}, 합격비율={y.mean():.2f}, 유형더미={n_type_dummies}개")

        print("  Lasso...")
        lasso = lasso_run(X, y, names)
        print(f"  Lasso AUC={lasso['auc_cv']:.3f}, 선택={lasso['n_selected']}개")

        print("  LightGBM...")
        lgbm = lgbm_run(X, y, names)
        print(f"  LGBM AUC={lgbm['auc_cv']:.3f}")

        print("  단변량 스윕...")
        unidf = univariate_sweep(sub, label_col, yn_cols, num_cols)

        sig, tentative, null_feats = classify_features(lasso, unidf)
        print(f"  유의미={len(sig)}, 잠정={len(tentative)}, 무의미={len(null_feats)}")

        fname_key = "서류" if "서류" in label_col else "최종"
        md = write_report(label_name, n, n_pos, lasso, lgbm, unidf, sig, tentative, null_feats, n_type_dummies)
        out = REPORTS / f"regression_summary_{fname_key}_{suffix}.md"
        out.write_text(md, encoding="utf-8")
        print(f"  [saved] {out.name}")

        results[label_col] = {
            "label_name": label_name,
            "n": n, "n_pos": n_pos,
            "auc_lasso": lasso["auc_cv"],
            "auc_lgbm": lgbm["auc_cv"],
            "significant": [
                {"feature": r["feature"], "direction": r["direction"],
                 "or": r["OR"], "p": r["p"], "lasso_strength": r["lasso_strength"]}
                for r in sig
            ],
            "tentative": [
                {"feature": r["feature"], "direction": r["direction"],
                 "or": r["OR"], "p": r["p"], "lasso_strength": r["lasso_strength"]}
                for r in tentative
            ],
            "null_count": len(null_feats),
        }

    # ── 통합 요약 ALL.md ──
    all_lines = ["# 통합 회귀분석 요약 — 유의미 / 무의미 피처\n"]
    all_lines.append("> Lasso L1 선택(방향) × 단변량 Fisher/MWU p-값(유의성) 교차 판정\n")

    for key, res in results.items():
        all_lines.append(
            f"## {res['label_name']}  "
            f"(n={res['n']}, 합격{res['n_pos']}, "
            f"AUC Lasso={res['auc_lasso']:.3f} / LGBM={res['auc_lgbm']:.3f})"
        )
        all_lines.append("\n### ✅ 유의미한 피처")
        if res["significant"]:
            all_lines.append("| 피처 | 방향 | OR | p |")
            all_lines.append("|---|:--:|--:|--:|")
            for f in res["significant"]:
                arrow = "↑합격" if f["direction"] == "+" else "↓합격"
                or_str = f"{f['or']:.2f}" if f["or"] is not None else "—"
                all_lines.append(f"| {f['feature']} | {arrow} | {or_str} | **{f['p']:.3f}** |")
        else:
            all_lines.append("→ 없음")

        all_lines.append(f"\n### ⚠️ 잠정 피처 ({len(res['tentative'])}개)")
        if res["tentative"]:
            all_lines.append("| 피처 | 방향 | OR | p |")
            all_lines.append("|---|:--:|--:|--:|")
            for f in res["tentative"]:
                arrow = "↑합격" if f["direction"] == "+" else "↓합격"
                or_str = f"{f['or']:.2f}" if f["or"] is not None else "—"
                all_lines.append(f"| {f['feature']} | {arrow} | {or_str} | {f['p']:.3f} |")
        else:
            all_lines.append("→ 없음")

        all_lines.append(f"\n### ❌ 무의미한 피처: {res['null_count']}개 (상세는 개별 리포트 참조)\n")

    (REPORTS / f"regression_summary_ALL_{suffix}.md").write_text("\n".join(all_lines), encoding="utf-8")
    weights_path = DATA / f"rubric_weights_{suffix}.json"
    weights_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[완료] reports/regression_summary_서류_{suffix}.md / 최종_{suffix}.md / ALL_{suffix}.md")
    print(f"[완료] {weights_path.name}")


if __name__ == "__main__":
    main()
