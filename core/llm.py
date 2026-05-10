"""Anthropic Claude API 호출 래퍼.

- API 키는 .env에서 로드
- 모든 호출을 logs/llm_calls.jsonl에 기록 (Phase 3 지도학습 대비)
- JSON 모드 응답 파싱 유틸 포함
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

_DEFAULT_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
_LOG_PATH = Path("logs/llm_calls.jsonl")


def get_client() -> Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or api_key.startswith("sk-ant-api-여기"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요."
        )
    return Anthropic(api_key=api_key)


def call_claude(
    system: str,
    user: str,
    *,
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.3,
    purpose: str = "generic",
    metadata: dict | None = None,
    use_cache: bool = False,
    cached_user_prefix: str | None = None,
) -> tuple[str, dict]:
    """Claude 호출. (응답 텍스트, 메타데이터) 반환.

    cached_user_prefix: use_cache=True 일 때 user 메시지 앞에 붙이는 캐시 대상 블록.
    Skills처럼 섹션 간 고정된 콘텐츠를 별도 content block으로 분리해 캐시 히트율을 높인다.
    """
    client = get_client()
    mdl = model or _DEFAULT_MODEL
    call_id = str(uuid.uuid4())[:8]

    if use_cache:
        system_param: str | list = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        extra_headers = {"anthropic-beta": "prompt-caching-2024-07-31"}
    else:
        system_param = system
        extra_headers = None

    if use_cache and cached_user_prefix:
        user_content: str | list = [
            {"type": "text", "text": cached_user_prefix, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": user},
        ]
    else:
        user_content = user

    start = time.time()
    resp = client.messages.create(
        model=mdl,
        max_tokens=max_tokens,
        timeout=90.0,
        temperature=temperature,
        system=system_param,
        messages=[{"role": "user", "content": user_content}],
        extra_headers=extra_headers,
    )
    duration_ms = int((time.time() - start) * 1000)

    text = "".join(
        block.text for block in resp.content if getattr(block, "type", "") == "text"
    )

    meta = {
        "call_id": call_id,
        "timestamp": datetime.utcnow().isoformat(),
        "model": mdl,
        "purpose": purpose,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "cache_creation_input_tokens": getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        "duration_ms": duration_ms,
        "stop_reason": resp.stop_reason,
    }
    if metadata:
        meta.update(metadata)

    _log_call(system, user, text, meta)
    return text, meta


def _log_call(system: str, user: str, response: str, meta: dict) -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        **meta,
        "system_preview": system[:300],
        "user_preview": user[:500],
        "response_full": response,   # 디버깅·학습용 전체 응답 (민감정보 없음)
    }
    with _LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def parse_json_response(text: str) -> dict:
    """LLM 응답에서 JSON 파싱. 코드펜스 자동 제거 + 잘림 복구 시도."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines)

    # 정상 파싱 시도
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass

    # 잘린 JSON 복구 시도 — 마지막으로 성공적으로 닫힌 객체 위치 찾기
    recovered = _try_recover_truncated_json(t)
    if recovered is not None:
        return recovered

    # 최후: 다시 파싱 시도해서 원래 에러 발생
    return json.loads(t)


def _try_recover_truncated_json(text: str) -> dict | None:
    """토큰 한도로 잘린 JSON을 최대한 복구.
    핵심 전략: content_segments 배열이 닫히지 않았으면 `]`와 필수 필드들을 임시로 채워 파싱.
    """
    import re

    # content_segments 배열 안까지만 있으면 거기까지 잘라서 닫기
    t = text.strip()

    # 1) 마지막 `{...}` 완전한 객체 위치까지만 잘라내기
    depth_brace = 0
    depth_bracket = 0
    in_string = False
    escape = False
    last_complete_pos = 0

    for i, ch in enumerate(t):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth_brace += 1
        elif ch == "}":
            depth_brace -= 1
            if depth_brace == 0 and depth_bracket == 0:
                last_complete_pos = i + 1
        elif ch == "[":
            depth_bracket += 1
        elif ch == "]":
            depth_bracket -= 1

    # 1단계 결과가 있으면 먼저 시도
    if last_complete_pos > 0:
        candidate = t[:last_complete_pos]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # 2) content_segments 배열에서 마지막 완전한 객체까지만 남기고 닫기
    match = re.search(r'"content_segments"\s*:\s*\[', t)
    if match:
        arr_start = match.end()
        depth_brace = 0
        depth_bracket = 1  # "[" 이후
        in_string = False
        escape = False
        last_seg_end = arr_start

        for i in range(arr_start, len(t)):
            ch = t[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth_brace += 1
            elif ch == "}":
                depth_brace -= 1
                if depth_brace == 0 and depth_bracket == 1:
                    last_seg_end = i + 1
            elif ch == "[":
                depth_bracket += 1
            elif ch == "]":
                depth_bracket -= 1

        # content_segments 배열까지만 + 기본 필드로 채운 최소 JSON 구성
        partial = t[:last_seg_end] + "]"
        partial += ', "confidence_level": "yellow"'
        partial += ', "reasoning": "[토큰 한도로 응답 일부 누락됨 — 복구 본문]"'
        partial += ', "used_answer_ids": []'
        partial += ', "missing_info": ["토큰 한도 초과로 응답이 잘림 — 섹션을 다시 생성해주세요"]'
        # inline_suggestions 복구 시도 — 있으면 추출, 없으면 빈 배열
        sugs_json = "[]"
        sug_match = re.search(r'"inline_suggestions"\s*:\s*(\[)', t)
        if sug_match:
            sug_start = sug_match.start(1)
            depth = 0
            in_str = False
            esc = False
            last_obj_end = sug_start
            for i in range(sug_start, len(t)):
                c = t[i]
                if esc:
                    esc = False
                    continue
                if c == "\\":
                    esc = True
                    continue
                if c == '"' and not esc:
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        last_obj_end = i + 1
                elif c == "]" and depth == 0:
                    sugs_json = t[sug_start:i + 1]
                    try:
                        json.loads(sugs_json)
                    except Exception:
                        sugs_json = (t[sug_start:last_obj_end] + "]") if last_obj_end > sug_start else "[]"
                    break
            else:
                sugs_json = (t[sug_start:last_obj_end] + "]") if last_obj_end > sug_start else "[]"
        partial += f', "inline_suggestions": {sugs_json}'
        partial += ', "rubric_check": {}'
        partial += "}"

        try:
            return json.loads(partial)
        except json.JSONDecodeError:
            return None

    # 3) strategic_feedbacks 배열에서 마지막 완전한 객체까지만 남기고 닫기
    match = re.search(r'"strategic_feedbacks"\s*:\s*\[', t)
    if match:
        arr_start = match.end()
        depth_brace = 0
        depth_bracket = 1  # "[" 이후
        in_string = False
        escape = False
        last_fb_end = arr_start

        for i in range(arr_start, len(t)):
            ch = t[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth_brace += 1
            elif ch == "}":
                depth_brace -= 1
                if depth_brace == 0 and depth_bracket == 1:
                    last_fb_end = i + 1
            elif ch == "[":
                depth_bracket += 1
            elif ch == "]":
                depth_bracket -= 1

        partial = t[:arr_start] + t[arr_start:last_fb_end] + "]}"
        try:
            return json.loads(partial)
        except json.JSONDecodeError:
            return None

    return None


if __name__ == "__main__":
    try:
        client = get_client()
        print("Client initialized OK.")
    except Exception as e:
        print(f"Error: {e}")
