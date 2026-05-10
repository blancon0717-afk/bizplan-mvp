"""support_programs.csv 병합 스크립트.

새 CSV(주요 지원사업 공고 모음.csv) 89개 프로그램을
기존 support_programs.csv의 풍부한 메타(지역/program_code/설명)와 병합.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

NEW_CSV = BASE / "주요 지원사업 공고 모음 1a09764ea3ec804fafdef85d6d45e426.csv"
OLD_CSV = BASE / "data/programs/support_programs.csv"
OUT_CSV = BASE / "data/programs/support_programs.csv"

FIELDNAMES = ["name", "연차", "특화분야", "지역", "최대지원금액_만원", "지원시기", "상태", "program_code", "설명"]

# 비수도권 한정 키워드
_NONSEOUL_KW = ["창업중심대학", "전남형", "부산", "오션테크", "지역특화"]


def parse_amount(text: str) -> int:
    """금액 텍스트 → 만원 정수 (여러 값이면 최대값 취함)."""
    if not text or text.strip() in ("-", "예창면제", ""):
        return 0
    max_val = 0
    # 억 (소수점 포함): 1.5억, 2억, 15억, 200억
    for m in re.finditer(r"(\d+\.?\d*)억", text):
        max_val = max(max_val, int(float(m.group(1)) * 10000))
    # 천만: 3천만, 5천만
    for m in re.finditer(r"(\d+)천만", text):
        max_val = max(max_val, int(m.group(1)) * 1000)
    # 백만: 5백만
    for m in re.finditer(r"(\d+)백만", text):
        max_val = max(max_val, int(m.group(1)) * 100)
    # 순수 만 (천만·백만 뒤에 처리): 6000만, 8300만, 170만
    for m in re.finditer(r"(\d+)만", text):
        max_val = max(max_val, int(m.group(1)))
    return max_val


def to_pipe(text: str) -> str:
    """쉼표 구분 텍스트 → 파이프 구분 (matching.py 파싱 형식)."""
    if not text:
        return ""
    return "|".join(p.strip() for p in text.split(",") if p.strip())


def infer_region(name: str) -> str:
    for kw in _NONSEOUL_KW:
        if kw in name:
            return "비수도권"
    return "전국"


def infer_status(name: str, 분야_raw: str, year_str: str) -> str:
    if "★통합공고" in 분야_raw:
        return "통합공고"
    if "폐쇄" in name or "폐지" in name:
        return "종료"
    try:
        if int(year_str) <= 2024:
            return "종료"
    except (ValueError, TypeError):
        pass
    return "모집중"


def main() -> None:
    # 기존 CSV 메타 보존 (name → {지역, program_code, 설명, 상태})
    old_meta: dict[str, dict] = {}
    if OLD_CSV.exists():
        with OLD_CSV.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                name = row.get("name", "").strip()
                if name:
                    old_meta[name] = {
                        "지역":         row.get("지역", "").strip(),
                        "program_code": row.get("program_code", "").strip(),
                        "설명":         row.get("설명", "").strip(),
                        "상태":         row.get("상태", "").strip(),
                    }

    rows_out: list[dict] = []
    seen: set[str] = set()

    with NEW_CSV.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("이름", "").strip()
            if not name:
                continue
            if name in seen:
                continue
            seen.add(name)

            연차_raw = row.get("연차", "")
            분야_raw  = row.get("특화분야", "")
            시기_raw  = (
                row.get("지원시기(직전연도 공고 기준)", "")
                or row.get("지원시기", "")
            )
            금액_raw  = row.get("최대 지원 금액", "")
            연도_raw  = row.get("최신 업데이트 연도", "")

            meta = old_meta.get(name, {})

            rows_out.append({
                "name":            name,
                "연차":             to_pipe(연차_raw),
                "특화분야":         to_pipe(분야_raw),
                "지역":             meta.get("지역") or infer_region(name),
                "최대지원금액_만원":  parse_amount(금액_raw),
                "지원시기":         to_pipe(시기_raw),
                "상태":             meta.get("상태") or infer_status(name, 분야_raw, 연도_raw),
                "program_code":    meta.get("program_code", ""),
                "설명":             meta.get("설명", ""),
            })

    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows_out)

    종료 = sum(1 for r in rows_out if r["상태"] == "종료")
    통합 = sum(1 for r in rows_out if r["상태"] == "통합공고")
    유효 = len(rows_out) - 종료 - 통합
    code_cnt = sum(1 for r in rows_out if r["program_code"])

    print(f"✅ {len(rows_out)}개 병합 완료 → {OUT_CSV}")
    print(f"   모집중/예정: {유효}개 | 통합공고: {통합}개 | 종료: {종료}개")
    print(f"   YAML 양식 연결(program_code 있음): {code_cnt}개")


if __name__ == "__main__":
    main()
