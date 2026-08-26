"""벤치마크 엔진 — 추출 피처를 프로그램별 합격작 통계(benchmark_v1.json)와 대조.

LLM 호출 없음(순수 함수). 확률(%) 표기는 benchmark_v1.json의 display_mode가
'empirical_rate'인 프로그램(밴드 간 실측 리프트가 있는 경우)에서만 허용된다.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_BENCH_PATH = Path(__file__).resolve().parent.parent / "data" / "reference" / "benchmark_v1.json"

# program_code → (합격률·점수 분포 기준 프로그램, 항목 비교용 그룹)
_PROGRAM_MAP: dict[str, tuple[str, str]] = {
    "initial_package": ("초기창업패키지", "초창그룹"),
    "initial_package_deeptech": ("초기창업패키지", "초창그룹"),
    "preliminary_package": ("예비창업패키지", "예창그룹"),
    "youth_academy": ("청년창업사관학교", "청창사"),
    "deeptech_academy": ("청년창업사관학교", "청창사"),
    "changjungdae": ("창업중심대학-초창", "초창그룹"),
}
# 합격작/불합격작 보유율 차이가 이 값(pp) 이상이고, 합격작 최소 1/4이 갖춘 항목만 노출
# (합격작 10%만 가진 희소 항목은 "합격작이 갖춘 것"으로 제시하기엔 근거 약함)
_MIN_DELTA_PP = 5.0
_MIN_PASS_PREVALENCE = 0.25
_MAX_GAPS = 8
_MAX_STRENGTHS = 5


def _present(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().upper() in ("Y", "O")
    try:
        return float(v) > 0
    except (TypeError, ValueError):
        return False


def short_label(name: str) -> str:
    """긴 피처명을 화면용 짧은 라벨로 (괄호·화살표 이후 제거)."""
    s = re.split(r"\s*(?:\(|->)", name, maxsplit=1)[0].strip()
    return s or name


@lru_cache(maxsize=1)
def load_benchmark() -> dict:
    return json.loads(_BENCH_PATH.read_text(encoding="utf-8"))


def program_key(program_code: str) -> tuple[str, str] | None:
    return _PROGRAM_MAP.get(program_code)


def evaluate(features: dict, program_code: str) -> dict:
    """추출 피처(dict) + 프로그램 코드 → 화면·액션플랜용 벤치마크 결과."""
    keys = program_key(program_code)
    if keys is None:
        return {"available": False, "reason": "no_benchmark_for_program"}
    bm = load_benchmark()
    prog_name, group_name = keys
    prog = bm["programs"].get(prog_name)
    if not prog:
        return {"available": False, "reason": "program_missing_in_benchmark"}
    group = bm["groups"].get(group_name) or {}

    items: list[str] = bm["score_items"]
    score = sum(1 for k in items if _present(features.get(k)))
    ck = prog["checklist"]

    band = next((b for b in prog["score_bands"] if b["score_min"] <= score <= b["score_max"]), None)
    if band is None and prog["score_bands"]:
        band = min(prog["score_bands"], key=lambda b: min(abs(score - b["score_min"]), abs(score - b["score_max"])))
    mode = prog["display_mode"]
    rate_pct = round(band["pass_rate"] * 100) if (band and mode == "empirical_rate") else None

    # 항목 벤치마크는 표본이 큰 그룹 통계 우선(없으면 프로그램 통계)
    feats = group.get("features") or prog["features"]
    ranked = sorted(
        (f for f in feats
         if f["delta_pp"] >= _MIN_DELTA_PP and f["pass_prevalence"] >= _MIN_PASS_PREVALENCE),
        key=lambda f: -f["delta_pp"],
    )

    def _row(f: dict) -> dict:
        return {
            "feature": f["feature"],
            "label": short_label(f["feature"]),
            "pass_pct": round(f["pass_prevalence"] * 100),
            "fail_pct": round(f["fail_prevalence"] * 100),
            "delta_pp": f["delta_pp"],
            "present": _present(features.get(f["feature"])),
        }

    rows = [_row(f) for f in ranked]
    gaps = [r for r in rows if not r["present"]][:_MAX_GAPS]
    strengths = [r for r in rows if r["present"]][:_MAX_STRENGTHS]

    return {
        "available": True,
        "program": prog_name,
        "group": group_name,
        "display_mode": mode,
        "score": score,
        "score_max": len(items),
        "pass_mean": ck["pass_mean"],
        "pass_median": ck["pass_median"],
        "fail_mean": ck["fail_mean"],
        "band": band and {"band": band["band"], "score_min": band["score_min"],
                          "score_max": band["score_max"], "n": band["n"]},
        "empirical_pass_rate_pct": rate_pct,
        "n_docs": prog["n"],
        "n_pass_docs": prog["n_pass"],
        "gaps": gaps,
        "strengths": strengths,
    }


def gaps_for_prompt(result: dict) -> str:
    """액션플랜 프롬프트용 '합격작 대비 부족 항목' 블록 텍스트."""
    if not result.get("available") or not result.get("gaps"):
        return "없음 (벤치마크 미산출 또는 부족 항목 없음)"
    lines = [
        f"- {g['label']}: 합격작 {g['pass_pct']}%가 언급 (불합격작 {g['fail_pct']}%) — 이 문서엔 없음"
        for g in result["gaps"]
    ]
    head = f"(실제 {result['program']} 서류심사 {result['n_docs']}건 통계, 합격작 {result['n_pass_docs']}건 기준)"
    return head + "\n" + "\n".join(lines)
