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


# ── 벤치마크 부족 항목 → 갭 인터뷰 질문 ──────────────────────────────────────
# 자산형 항목은 AI가 지어낼 수 없는 '사실'이라, 초안에 반영하려면 사용자에게 물어야 한다.
# (질문, 힌트, 대상 섹션 카테고리)
_GAP_TEMPLATES: dict[str, tuple[str, str, str]] = {
    "대표자 사업 경험 여부 (매출 1억 이상)": ("대표자가 이전에 운영한 사업(또는 현 사업)에서 연 매출 1억 원 이상을 달성한 경험이 있나요?", "예: 2022~2024년 OO 운영, 2024년 매출 2.3억", "Team"),
    "대표자 해당 아이템 산업 경험/기술 보유 여부": ("대표자가 이 아이템과 같은 산업에서 일했거나 관련 기술·학위를 보유했나요?", "예: 식품제조사 R&D 7년, 식품공학 석사", "Team"),
    "아이템 관련 경력 증빙": ("대표자·팀원의 아이템 관련 경력을 회사명·직위·기간으로 알려주세요.", "예: OO전자 SW개발팀 선임 2018~2023", "Team"),
    "고용": ("현재 재직 중인 직원 수와 직무 구성을 알려주세요 (채용 예정 제외).", "예: 정규직 4명 — 개발 2, 영업 1, 디자인 1", "Team"),
    "개발인력(sw) 보유(팀원 기준)": ("팀에 재직 중인 소프트웨어 개발 인력이 있나요? 인원과 담당을 알려주세요.", "예: 백엔드 1명, 앱 1명 (정규직)", "Team"),
    "영업 & 유통 마케팅 인력 보유 (팀원 기준)": ("영업·유통·마케팅을 전담하는 팀원이 있나요?", "예: 영업 1명(B2B 유통사 경력 5년)", "Team"),
    "제조 전문 인력 (기술자) 보유(팀원 기준)": ("제조·생산을 담당하는 기술 인력이 있나요?", "예: 생산기술 2명 (설비 운용 경력)", "Team"),
    "R&D 인력 보유(팀원 기준)": ("연구개발 전담 인력이 있나요?", "예: 연구원 1명 (석사, 특허 2건 발명)", "Team"),
    "대표자/팀원 관련 자격증서": ("대표자·팀원이 보유한 아이템 관련 자격증·면허가 있나요?", "예: 식품기사, 전기기사, 정보처리기사", "Team"),
    "수요처 확보 여부": ("구매의향서·계약·시범납품 등으로 확보한 수요처가 있나요? 기관명과 규모를 알려주세요.", "예: OO마트 구매의향서 1건(월 500개), 시범납품 3곳", "Scale-up"),
    "수요처 네트워크 보유": ("잠재 고객사·수요기관 명단이나 접촉 이력이 있나요?", "예: 지역 요양원 12곳 미팅 완료, 2곳 테스트 진행", "Scale-up"),
    "구매 의향서 보유": ("구매의향서(또는 구매확약서)를 받은 곳이 있나요?", "예: OO기관 구매의향서 2건", "Scale-up"),
    "납품 확정 계약서 보유 여부": ("확정된 납품·공급 계약이 있나요?", "예: OO사 연간 공급계약 (1.2억)", "Scale-up"),
    "MOU 보유 개수": ("체결 완료된 MOU·업무협약·입점 협약이 있나요? 상대와 내용을 알려주세요.", "예: OO대학 산학협력 MOU, OO몰 입점 협약", "Scale-up"),
    "투자의향서 확보 여부": ("투자의향서(LOI)나 투자 유치 확정 건이 있나요?", "예: OO파트너스 시드 3억 의향서", "Scale-up"),
    "누적 투자": ("지금까지 유치한 누적 투자 금액이 있나요?", "예: 시드 2억 (2024.06)", "Scale-up"),
    "견적서 보유 개수": ("고객·거래처로부터 받은 견적서가 있나요?", "예: 견적 요청 5건, 견적서 발행 3건", "Scale-up"),
    "필수 재료 공급처 네트워크 보유": ("핵심 원재료·부품 공급처가 확보돼 있나요?", "예: OO소재 공급계약, 대체 공급처 1곳", "Scale-up"),
    "영업 및 마케팅 네트워크 보유": ("외부 유통·판매·마케팅 협력처(실명)가 있나요?", "예: OO유통 총판 협의, 인플루언서 협업 3건", "Scale-up"),
    "기타 기업 인증 개수": ("벤처기업·이노비즈·메인비즈 등 기업 인증을 보유했나요?", "예: 벤처기업 인증(2025), 여성기업 확인서", "Scale-up"),
    "직전 매출 (수출 제외)": ("최근 회계연도 매출액을 알려주세요.", "예: 2025년 매출 3.2억", "Scale-up"),
    "아이템 관련 특허 보유 개수 (등록 기준)": ("아이템 관련 등록 완료 특허가 있나요? 등록번호를 알려주세요.", "예: 제10-2456273호 (2024 등록)", "Solution"),
    "아이템관련 특허 출원 여부 (출원상태 YNLY)": ("출원 중인 아이템 관련 특허가 있나요?", "예: 출원번호 10-2025-0012345", "Solution"),
    "MVP 테스트 실행 여부": ("MVP·프로토타입을 만들어 테스트한 결과가 있나요?", "예: 베타 테스터 30명, 재사용률 62%", "Solution"),
    "실제 실험 사진 보유 여부": ("실험·테스트 데이터나 사진 자료가 있나요?", "예: 내구성 테스트 500회 결과표, 시제품 사진 6장", "Solution"),
    "실제 제조 OR 서비스 사진 보유 여부": ("실제 제품·서비스 화면이나 실물 사진이 있나요?", "예: 앱 화면 캡처 5장, 시제품 실물 사진", "Solution"),
    "FGI": ("고객 심층 인터뷰(FGI)나 설문을 진행했나요? 규모와 핵심 결과를 알려주세요.", "예: 타깃 고객 15명 인터뷰, 상위 불만 3가지 도출", "Solution"),
    "데이터 보유 여부 (고객 및 아이템 관련) -> AI/빅데이터 가공용 Yr 잠재고객 데이터 등": ("자체 보유한 고객·아이템 데이터가 있나요? 규모를 알려주세요.", "예: 고객 행동 로그 12만 건, 라벨링 데이터 2만 건", "Solution"),
    "R&D 인력 or 자체 학습 데이터 네트워크 보유": ("외부 R&D 협력 기관이나 자체 학습 데이터 네트워크가 있나요?", "예: OO대 연구실 공동연구, 병원 데이터 협약", "Solution"),
    "수상이력": ("공모전·대회 수상 이력이 있나요?", "예: 2025 OO창업경진대회 최우수상", "Solution"),
}
_MAX_GAP_QUESTIONS = 3


def _category_for(feature: str) -> str:
    if feature in _GAP_TEMPLATES:
        return _GAP_TEMPLATES[feature][2]
    if any(k in feature for k in ("인력", "팀원", "대표자", "자격")):
        return "Team"
    if any(k in feature for k in ("네트워크", "수요", "의향", "계약", "매출", "투자", "견적", "MOU", "인증", "수출")):
        return "Scale-up"
    return "Solution"


def gap_questions(result: dict, form_sections: list) -> list[dict]:
    """벤치마크 부족 항목 상위 N개 → 갭 인터뷰 질문(dict) 목록.

    form_sections: Form.sections (id·category 속성). 대상 섹션은 카테고리 일치 섹션.
    반환 형식은 양식 YAML gap_questions와 동일(id·question·hint·target_sections)
    + evidence(합격작 통계 근거)·source="benchmark".
    """
    if not result.get("available"):
        return []
    by_cat: dict[str, list[str]] = {}
    for s in form_sections:
        by_cat.setdefault(getattr(s, "category", "") or "", []).append(str(getattr(s, "id", "")))
    out = []
    for i, g in enumerate(result.get("gaps", [])[:_MAX_GAP_QUESTIONS], 1):
        feat = g["feature"]
        q, hint, _ = _GAP_TEMPLATES.get(
            feat, (f"'{g['label']}'에 해당하는 내용이 있나요? 있다면 기관명·수치·시점을 구체적으로 알려주세요.", "없으면 비워두세요", ""),
        )
        cat = _category_for(feat)
        out.append({
            "id": f"bq{i}",
            "question": q,
            "hint": hint,
            "target_sections": by_cat.get(cat, []),
            "evidence": f"{result['program']} 합격작 {g['pass_pct']}%가 이 항목을 언급했습니다 (불합격작 {g['fail_pct']}%)",
            "source": "benchmark",
        })
    return out


def insight_note(program_code: str, top_n: int = 5) -> str:
    """양식 변환 프롬프트용 프로그램별 합격작 통계 인사이트 (근거 있을 때만 서술하도록 지시)."""
    keys = program_key(program_code)
    if keys is None:
        return ""
    bm = load_benchmark()
    prog_name, group_name = keys
    feats = (bm["groups"].get(group_name) or {}).get("features") or bm["programs"].get(prog_name, {}).get("features", [])
    ranked = sorted(
        (f for f in feats if f["delta_pp"] >= _MIN_DELTA_PP and f["pass_prevalence"] >= _MIN_PASS_PREVALENCE),
        key=lambda f: -f["delta_pp"],
    )[:top_n]
    if not ranked:
        return ""
    lines = [
        f"- {short_label(f['feature'])}: 합격작 {round(f['pass_prevalence']*100)}% 언급 (불합격작 {round(f['fail_prevalence']*100)}%)"
        for f in ranked
    ]
    return (
        f"## 실제 {prog_name} 합격작 통계 (서류심사 {bm['programs'][prog_name]['n']}건 실측)\n"
        "아래 항목은 합격작이 불합격작보다 뚜렷이 더 자주 서술한 요소다. 초안·인터뷰 답변에 해당 근거가 "
        "있으면 이 섹션에서 반드시 구체적으로(기관명·수치·시점) 서술하라. 근거가 없으면 절대 지어내지 말고 "
        "[수치 필요] 처리하라.\n" + "\n".join(lines)
    )
