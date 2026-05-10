"""전처리 파이프라인 end-to-end 검증 스크립트.

1) eporei 합격작 답변(60문항)을 INIT-Q01~10의 merged_from 매핑대로 합성
2) 새 세션 생성 + bulk PUT으로 답변 저장 (자동 추출 트리거 확인)
3) POST /extract-context 결과 8개 항목 출력
4) POST /generate 1개 섹션만 실행 → logs/llm_calls.jsonl 마지막 entry 분석
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
from urllib import request, error

API = "http://localhost:8000/api"
ROOT = Path(__file__).resolve().parent.parent
INIT_PATH = ROOT / "data" / "interview" / "initial_questions.json"
EPOREI_PATH = ROOT / "data" / "examples" / "eporei_answers.json"
LOG_PATH = ROOT / "logs" / "llm_calls.jsonl"


def http(method: str, path: str, body: dict | None = None, timeout: int = 180) -> dict:
    url = f"{API}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = request.Request(url, data=data, method=method,
                          headers={"Content-Type": "application/json"} if data else {})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8")
            return json.loads(text) if text else {}
    except error.HTTPError as e:
        print(f"!! HTTP {e.code} on {method} {path}: {e.read().decode('utf-8', 'ignore')}")
        raise


def synth_answers() -> dict[str, str]:
    init_qs = json.loads(INIT_PATH.read_text(encoding="utf-8"))
    eporei = json.loads(EPOREI_PATH.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for q in init_qs:
        merged = q.get("merged_from", [])
        chunks = [eporei[m] for m in merged if m in eporei and eporei[m].strip()]
        out[q["qid"]] = "\n\n".join(chunks) if chunks else ""
    return out


def tail_log_section_gen() -> dict | None:
    if not LOG_PATH.exists():
        return None
    last = None
    with LOG_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("purpose") == "section_generation":
                last = obj
    return last


def tail_log_extract() -> dict | None:
    if not LOG_PATH.exists():
        return None
    last = None
    with LOG_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("purpose") == "context_extraction":
                last = obj
    return last


def main() -> int:
    # 0. 헬스체크
    print("== [0] 헬스체크 ==")
    h = http("GET", "/programs")
    print(f"   programs: {len(h.get('programs', []))}개")

    # 1. 새 세션 생성
    print("\n== [1] 새 세션 생성 ==")
    sess = http("POST", "/sessions", {"program_code": "initial_package"})
    sid = sess["session_id"]
    print(f"   session_id={sid} program={sess['program_code']}")

    # 2. eporei 기반 INIT 답변 합성 + 일괄 저장
    print("\n== [2] eporei 답변 합성 → INIT-Q01~10 일괄 저장 ==")
    answers = synth_answers()
    total_chars = sum(len(v) for v in answers.values())
    filled = sum(1 for v in answers.values() if v.strip())
    print(f"   합성 결과: filled={filled}/10  total_chars={total_chars}")
    for qid, txt in answers.items():
        print(f"     {qid}: {len(txt):4d} chars")

    t0 = time.perf_counter()
    bulk = http("PUT", f"/sessions/{sid}/answers", {"answers": answers}, timeout=300)
    elapsed = time.perf_counter() - t0
    print(f"   PUT /answers: updated={bulk['updated']} context_extracted={bulk['context_extracted']}  ({elapsed:.1f}s)")

    # 3. 전처리 결과 확인
    print("\n== [3] GET /context — 자동 추출된 company_context 확인 ==")
    ctx_resp = http("GET", f"/sessions/{sid}/context")
    if ctx_resp.get("extracted"):
        ctx = ctx_resp["context"]
        ctx_meta = ctx.pop("_meta", {})
        print(f"   _meta: {ctx_meta}")
        print(f"   filled fields: {sum(1 for v in ctx.values() if (v or '').strip())}/8")
        for k, v in ctx.items():
            v_str = (v or "").strip()
            preview = v_str[:80].replace("\n", " ")
            print(f"   - {k}: ({len(v_str):4d}자) {preview}{'...' if len(v_str) > 80 else ''}")
    else:
        # 자동 트리거가 안 됐으면 수동 호출
        print("   자동 추출 안 됨 → POST /extract-context 수동 호출")
        manual = http("POST", f"/sessions/{sid}/extract-context")
        print(f"   manual: filled={manual['filled']}/{manual['total']}  in={manual['input_tokens']}  out={manual['output_tokens']}  dur={manual['duration_ms']}ms")
        ctx = manual["context"]

    extract_log = tail_log_extract()
    if extract_log:
        print(f"\n   [llm_calls.jsonl] context_extraction:")
        print(f"     model={extract_log.get('model')}  in={extract_log.get('input_tokens')}  out={extract_log.get('output_tokens')}  dur={extract_log.get('duration_ms')}ms")

    # 4. 1개 섹션만 생성 (속도·비용 최소화)
    print("\n== [4] POST /generate (섹션 1만) — 컨텍스트 모드 ==")
    log_size_before = LOG_PATH.stat().st_size if LOG_PATH.exists() else 0
    t0 = time.perf_counter()
    # SSE 응답이지만 끝까지 읽어서 완료 대기
    req_obj = request.Request(
        f"{API}/sessions/{sid}/generate",
        data=json.dumps({"section_ids": ["1"]}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    events = []
    with request.urlopen(req_obj, timeout=300) as resp:
        body = resp.read().decode("utf-8")
        for line in body.splitlines():
            if line.startswith("event:") or line.startswith("data:"):
                events.append(line)
    elapsed = time.perf_counter() - t0
    print(f"   /generate 완료: {elapsed:.1f}s  (SSE events={len(events)})")
    # 마지막 SSE event 출력
    for line in events[-12:]:
        print(f"     {line}")

    # 5. logs/llm_calls.jsonl 마지막 section_generation 항목
    print("\n== [5] logs/llm_calls.jsonl 마지막 section_generation 분석 ==")
    last = tail_log_section_gen()
    if last:
        print(f"   timestamp:    {last.get('timestamp')}")
        print(f"   model:        {last.get('model')}")
        print(f"   section_id:   {last.get('section_id')}")
        print(f"   input_mode:   {last.get('input_mode')}")
        print(f"   input_tokens: {last.get('input_tokens')}")
        print(f"   output_tokens:{last.get('output_tokens')}")
        print(f"   duration_ms:  {last.get('duration_ms')}")
    else:
        print("   section_generation log entry 없음")

    # 6. 비교용: 같은 섹션을 legacy 모드로도 호출 (company_context를 임시로 비워서)
    #    — 토큰 절감 효과를 직접 비교
    print("\n== [6] 비교: 동일 섹션을 legacy 모드로 강제 호출 ==")
    print("   (company_context를 None으로 패치하고 generate_section 직접 호출)")
    sys.path.insert(0, str(ROOT))
    from core.forms import load_form
    from core.generation import generate_section
    from core.interview import load_initial_questions, load_followup_questions
    from core.skills import load_skills
    from backend.services.session_store import get_session

    session = get_session(sid)
    form = load_form(session.program_code)
    section = form.sections[0]
    questions = load_initial_questions(INIT_PATH)
    followup = load_followup_questions(ROOT / "data" / "interview" / "questions.json")
    skills = load_skills(ROOT / "skills")

    t0 = time.perf_counter()
    legacy_result = generate_section(
        form, section, questions, session.answers, skills, followup,
        company_context=None,  # 강제 legacy 경로
    )
    legacy_elapsed = time.perf_counter() - t0
    legacy_meta = legacy_result.llm_meta or {}
    print(f"   legacy 완료: in={legacy_meta.get('input_tokens')}  out={legacy_meta.get('output_tokens')}  dur={legacy_elapsed:.1f}s")

    # 최종 비교표
    print("\n== [요약] 토큰·시간 비교 ==")
    if last and legacy_meta:
        ctx_in = last.get("input_tokens", 0)
        leg_in = legacy_meta.get("input_tokens", 0)
        delta = leg_in - ctx_in
        pct = (delta / leg_in * 100) if leg_in else 0
        print(f"   legacy  입력 토큰: {leg_in:>7}")
        print(f"   context 입력 토큰: {ctx_in:>7}")
        print(f"   절감:              {delta:>7} ({pct:.1f}%)")
        print(f"   legacy  소요:      {legacy_elapsed:.1f}s")
        print(f"   context 소요:      {last.get('duration_ms', 0)/1000:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
