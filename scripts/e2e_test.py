"""E2E 자동 테스트 스크립트."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "http://localhost:8000"
ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
LLM_CALLS = LOGS / "llm_calls.jsonl"
TEST_LOG  = LOGS / "test_log.txt"

HAIKU_PRICE  = {"input": 0.80, "output": 4.00, "cache_create": 1.00, "cache_read": 0.08}
SONNET_PRICE = {"input": 3.00, "output": 15.00, "cache_create": 3.75, "cache_read": 0.30}

results: list[dict] = []


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(TEST_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def tok_cost(tokens: int, rate_per_million: float) -> float:
    return tokens * rate_per_million / 1_000_000


def record(step: str, ok: bool, elapsed: float, detail: str = "") -> None:
    results.append({"step": step, "ok": ok, "elapsed": elapsed, "detail": detail})
    status = "PASS" if ok else "FAIL"
    log(f"  [{status}]  {step}  ({elapsed:.1f}s)  {detail}")


def stream_sse(url: str, timeout: int = 600) -> tuple[bool, float, dict]:
    """SSE 스트림 소비 -> (성공 여부, 소요초, 마지막 이벤트 data)."""
    t0 = time.time()
    last_data: dict = {}
    error_events: list[str] = []
    try:
        with requests.post(url, stream=True, timeout=timeout) as r:
            if not r.ok:
                return False, time.time() - t0, {"http_status": r.status_code}
            buf = ""
            for chunk in r.iter_content(chunk_size=None, decode_unicode=True):
                buf += chunk
                while "\n\n" in buf:
                    event_str, buf = buf.split("\n\n", 1)
                    lines = event_str.strip().splitlines()
                    ev_type = next((l[6:].strip() for l in lines if l.startswith("event:")), "")
                    data_line = next((l[5:].strip() for l in lines if l.startswith("data:")), "")
                    if not data_line:
                        continue
                    try:
                        data = json.loads(data_line)
                    except Exception:
                        continue
                    if ev_type == "error":
                        error_events.append(data.get("message", "unknown"))
                    if ev_type in ("all_done", "error"):
                        last_data = data
                        last_data["_event"] = ev_type
                        last_data["_errors"] = error_events
                        return ev_type != "error", time.time() - t0, last_data
        return bool(last_data), time.time() - t0, last_data
    except Exception as e:
        return False, time.time() - t0, {"exception": str(e)}


def parse_llm_costs(start_ts: str) -> list[dict]:
    """start_ts 이후 llm_calls.jsonl 항목 파싱."""
    rows = []
    if not LLM_CALLS.exists():
        return rows
    for line in LLM_CALLS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("timestamp", "") < start_ts:
            continue
        model = d.get("model", "")
        price = SONNET_PRICE if "sonnet" in model else HAIKU_PRICE
        in_tok  = int(d.get("input_tokens", 0))
        out_tok = int(d.get("output_tokens", 0))
        cc_tok  = int(d.get("cache_creation_input_tokens", 0))
        cr_tok  = int(d.get("cache_read_input_tokens", 0))
        cost = (
            tok_cost(in_tok,  price["input"])
            + tok_cost(out_tok, price["output"])
            + tok_cost(cc_tok,  price["cache_create"])
            + tok_cost(cr_tok,  price["cache_read"])
        )
        rows.append({
            "purpose":  d.get("purpose", "?"),
            "model":    "haiku" if "haiku" in model else "sonnet",
            "in_tok":   in_tok,
            "out_tok":  out_tok,
            "cc_tok":   cc_tok,
            "cr_tok":   cr_tok,
            "dur_s":    round(int(d.get("duration_ms", 0)) / 1000, 1),
            "cost_usd": cost,
            "stop":     d.get("stop_reason", "?"),
        })
    return rows


def main() -> None:
    test_start_dt = datetime.now()
    start_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    start_msg = f"테스트 시작: {test_start_dt.strftime('%Y-%m-%d %H:%M:%S')}"

    LLM_CALLS.write_text("", encoding="utf-8")
    TEST_LOG.write_text(start_msg + "\n", encoding="utf-8")
    print("\n" + "=" * 65)
    print(start_msg)
    print("=" * 65)

    # [1] 백엔드 health
    t0 = time.time()
    try:
        r = requests.get(f"{BASE}/health", timeout=10)
        ok = r.ok and r.json().get("status") == "ok"
        record("백엔드 /health", ok, time.time() - t0, r.text)
    except Exception as e:
        record("백엔드 /health", False, time.time() - t0, str(e))
        log("백엔드 연결 실패 -- 테스트 중단")
        sys.exit(1)

    # [2] 세션 생성 + 답변 주입
    t0 = time.time()
    session_id = None
    try:
        r = requests.post(f"{BASE}/api/dev/load-test-session",
                          json={"program_code": "initial_package"}, timeout=30)
        if r.ok:
            sess = r.json()
            session_id = sess["session_id"]
            answers_loaded = sess["answers_loaded"]
            record("세션 생성+답변 주입", True, time.time() - t0,
                   f"session_id={session_id}  answers={answers_loaded}")
        else:
            record("세션 생성+답변 주입", False, time.time() - t0,
                   f"HTTP {r.status_code}: {r.text[:100]}")
            sys.exit(1)
    except Exception as e:
        record("세션 생성+답변 주입", False, time.time() - t0, str(e))
        sys.exit(1)

    # [3] 사업계획서 생성
    log(f"\n[3] 사업계획서 생성 중... (session={session_id})")
    ok, elapsed, data = stream_sse(f"{BASE}/api/sessions/{session_id}/generate")
    if ok:
        overall = data.get("overall_completion", "?")
        total   = data.get("total_sections", "?")
        detail  = f"overall={overall}%  sections={total}"
    else:
        detail = str(data)[:120]
    record("사업계획서 생성", ok, elapsed, detail)

    # [4] 피드백 생성
    log(f"\n[4] 피드백 생성 중...")
    ok_fb, elapsed_fb, data_fb = stream_sse(f"{BASE}/api/sessions/{session_id}/feedback")
    fb_errors = data_fb.get("_errors", [])
    if ok_fb:
        strat      = data_fb.get("strategic_feedback_count", "?")
        detail_fb  = f"strategic={strat}  json_parse_errors={len(fb_errors)}"
    else:
        detail_fb = f"errors={fb_errors[:2]}"
    record("피드백 생성", ok_fb, elapsed_fb, detail_fb)
    json_ok = len(fb_errors) == 0
    record("  L evaluate_section JSON 파싱", json_ok, 0,
           "오류 없음" if json_ok else f"{len(fb_errors)}건: {fb_errors[0][:60] if fb_errors else ''}")

    # [5] 액션플랜
    log(f"\n[5] 액션플랜 생성 중...")
    t0 = time.time()
    try:
        r = requests.post(f"{BASE}/api/sessions/{session_id}/action-plan", timeout=120)
        if r.ok and "action_plan" in r.json():
            plan_len = len(r.json()["action_plan"])
            record("액션플랜 생성", True, time.time() - t0, f"length={plan_len}chars")
        else:
            record("액션플랜 생성", False, time.time() - t0, r.text[:80])
    except Exception as e:
        record("액션플랜 생성", False, time.time() - t0, str(e))

    # [6] 문서 점검
    log(f"\n[6] 문서 점검 중...")
    t0 = time.time()
    try:
        r = requests.post(f"{BASE}/api/sessions/{session_id}/document-check", timeout=120)
        if r.ok and "result" in r.json():
            res_len = len(r.json()["result"])
            record("문서 점검", True, time.time() - t0, f"length={res_len}chars")
        else:
            record("문서 점검", False, time.time() - t0, r.text[:80])
    except Exception as e:
        record("문서 점검", False, time.time() - t0, str(e))

    # [7] 비용 분석
    log(f"\n[7] 비용 집계 중...")
    time.sleep(1)
    rows = parse_llm_costs(start_ts)

    by_purpose: dict[str, dict] = {}
    for row in rows:
        p = row["purpose"]
        if p not in by_purpose:
            by_purpose[p] = {"calls": 0, "in": 0, "out": 0, "cc": 0, "cr": 0,
                              "cost": 0.0, "stop_err": 0}
        by_purpose[p]["calls"]    += 1
        by_purpose[p]["in"]       += row["in_tok"]
        by_purpose[p]["out"]      += row["out_tok"]
        by_purpose[p]["cc"]       += row["cc_tok"]
        by_purpose[p]["cr"]       += row["cr_tok"]
        by_purpose[p]["cost"]     += row["cost_usd"]
        if row["stop"] != "end_turn":
            by_purpose[p]["stop_err"] += 1

    total_cost         = sum(r["cost_usd"] for r in rows)
    total_cache_read   = sum(r["cr_tok"]   for r in rows)
    total_cache_create = sum(r["cc_tok"]   for r in rows)
    total_elapsed      = (datetime.now() - test_start_dt).total_seconds()

    # [8] 리포트
    W = 65
    sep = "-" * W
    lines = [
        "",
        "=" * W,
        "  E2E 테스트 결과 리포트",
        f"  {test_start_dt.strftime('%Y-%m-%d %H:%M')}  /  총 소요 {total_elapsed:.0f}초",
        "=" * W,
        "",
        "[ 단계별 결과 ]",
        f"{'단계':<32} {'결과':<6} {'소요(s)':<8} 비고",
        sep,
    ]
    for res in results:
        status = "PASS" if res["ok"] else "FAIL"
        lines.append(
            f"{res['step']:<32} {status:<6} {res['elapsed']:<8.1f} {res['detail']}"
        )

    lines += [
        "",
        "[ 단계별 LLM 비용 ]",
        f"{'purpose':<26} {'N':<4} {'in_tok':<8} {'out_tok':<8} "
        f"{'cache↑':<8} {'cache↓':<8} cost($)",
        sep,
    ]
    for p, v in by_purpose.items():
        stop_tag = f" [stop_err={v['stop_err']}]" if v["stop_err"] else ""
        lines.append(
            f"{p:<26} {v['calls']:<4} {v['in']:<8} {v['out']:<8} "
            f"{v['cc']:<8} {v['cr']:<8} ${v['cost']:.5f}{stop_tag}"
        )
    lines += [
        sep,
        f"{'합계':<26} {len(rows):<4} {'':8} {'':8} "
        f"{total_cache_create:<8} {total_cache_read:<8} ${total_cost:.5f}",
        "",
        f"캐시 히트율:  create {total_cache_create:,}tok / read {total_cache_read:,}tok",
        f"총 API 비용:  ${total_cost:.4f}  (~KRW {total_cost * 1380:.0f}원)",
        "=" * W,
    ]

    report = "\n".join(lines)
    print(report)
    with open(TEST_LOG, "a", encoding="utf-8") as f:
        f.write(report + "\n")

    pass_count = sum(1 for r in results if r["ok"])
    fail_count = len(results) - pass_count
    log(f"\n최종: {pass_count}PASS / {fail_count}FAIL  |  리포트 -> {TEST_LOG}")


if __name__ == "__main__":
    main()
