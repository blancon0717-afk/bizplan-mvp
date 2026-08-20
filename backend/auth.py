"""인증 토큰 발급·검증 및 FastAPI 의존성.

토큰은 모두 HMAC-SHA256 서명 문자열이며 별도 저장소가 없다.
(세션 언락 코드가 쓰는 방식과 동일 — 테이블 추가 없이 만료·위조 검증)

  로그인 쿠키   : session|{user_id}|{exp}
  이메일 인증   : verify|{user_id}|{email}|{exp}            → 이메일 변경 시 자동 무효
  비밀번호 재설정: reset|{user_id}|{password_hash}|{exp}     → 재설정 즉시 자동 무효
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import time
from typing import Optional

from fastapi import Cookie, HTTPException, Response

from services.user_store import get_user_by_id

COOKIE_NAME = "bizplan_auth"

_SESSION_TTL_SEC = 14 * 24 * 3600   # 로그인 유지 14일
_VERIFY_TTL_SEC = 24 * 3600         # 이메일 인증 링크 24시간
_RESET_TTL_SEC = 1 * 3600           # 비밀번호 재설정 링크 1시간


def require_auth_secret() -> str:
    """AUTH_SECRET을 반환. 미설정이면 RuntimeError (앱 기동 시 검증)."""
    secret = os.getenv("AUTH_SECRET", "")
    if not secret or len(secret) < 32:
        raise RuntimeError(
            "AUTH_SECRET 환경변수가 없거나 너무 짧습니다(32자 이상 필요). "
            "생성: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    return secret


def _sign(payload: str) -> str:
    secret = require_auth_secret()
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _issue(purpose: str, parts: list[str], ttl_sec: int) -> str:
    exp = str(int(time.time()) + ttl_sec)
    payload = "|".join([purpose, *parts, exp])
    return f"{'.'.join(parts)}.{exp}.{_sign(payload)}"


def _verify(purpose: str, token: str, extra_lookup) -> Optional[int]:
    """토큰 검증 후 user_id 반환. 실패 시 None.

    extra_lookup(user_id) -> list[str] : 서명에 포함된 가변 값(email, password_hash)을
    현재 DB 값으로 다시 만들어 대조한다. 값이 바뀌었으면 서명이 어긋나 무효가 된다.
    """
    segments = token.split(".")
    if len(segments) < 3:
        return None
    sig = segments[-1]
    exp = segments[-2]
    head = segments[:-2]
    if not head or not exp.isdigit():
        return None
    try:
        user_id = int(head[0])
    except ValueError:
        return None
    if int(exp) < int(time.time()):
        return None

    extras = extra_lookup(user_id)
    if extras is None:
        return None
    payload = "|".join([purpose, str(user_id), *extras, exp])
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    return user_id


# ── 로그인 쿠키 ─────────────────────────────────────────────────────────────

def issue_session_token(user_id: int) -> str:
    return _issue("session", [str(user_id)], _SESSION_TTL_SEC)


def _cookie_secure() -> bool:
    override = os.getenv("COOKIE_SECURE")
    if override is not None:
        return override != "0"
    return os.getenv("APP_BASE_URL", "").startswith("https://")


def set_session_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=issue_session_token(user_id),
        max_age=_SESSION_TTL_SEC,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


# ── 이메일 인증 / 비밀번호 재설정 토큰 ──────────────────────────────────────

def issue_verify_token(user_id: int, email: str) -> str:
    return _issue("verify", [str(user_id), email], _VERIFY_TTL_SEC)


def verify_verify_token(token: str) -> Optional[int]:
    def lookup(user_id: int):
        user = get_user_by_id(user_id)
        return None if user is None else [user["email"]]

    return _verify("verify", token, lookup)


def issue_reset_token(user_id: int, password_hash: str) -> str:
    return _issue("reset", [str(user_id), password_hash], _RESET_TTL_SEC)


def verify_reset_token(token: str) -> Optional[int]:
    def lookup(user_id: int):
        user = get_user_by_id(user_id)
        return None if user is None else [user["password_hash"]]

    return _verify("reset", token, lookup)


# ── FastAPI 의존성 ──────────────────────────────────────────────────────────

def _user_from_cookie(token: Optional[str]) -> Optional[sqlite3.Row]:
    if not token:
        return None
    user_id = _verify("session", token, lambda _uid: [])
    if user_id is None:
        return None
    user = get_user_by_id(user_id)
    if user is None or not user["email_verified"]:
        return None
    return user


def get_current_user(bizplan_auth: Optional[str] = Cookie(default=None)) -> sqlite3.Row:
    """로그인 필수 엔드포인트용. 미로그인 시 401."""
    user = _user_from_cookie(bizplan_auth)
    if user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return user


def get_current_user_optional(
    bizplan_auth: Optional[str] = Cookie(default=None),
) -> Optional[sqlite3.Row]:
    """로그인 여부에 따라 동작이 갈리는 엔드포인트용."""
    return _user_from_cookie(bizplan_auth)


def demo() -> None:
    """토큰 발급·검증 자체 점검 (python backend/auth.py)."""
    os.environ["AUTH_SECRET"] = "x" * 64

    # 로그인 토큰: 정상 통과 / 변조 시 실패
    tok = issue_session_token(7)
    assert _verify("session", tok, lambda _u: []) == 7
    flipped = tok[:-1] + ("0" if tok[-1] != "0" else "1")
    assert _verify("session", flipped, lambda _u: []) is None
    assert _verify("verify", tok, lambda _u: []) is None, "용도가 다른 토큰이 통과하면 안 된다"

    # 만료 검증
    expired = _issue("session", ["7"], -10)
    assert _verify("session", expired, lambda _u: []) is None

    # 가변 값(이메일/해시)이 바뀌면 무효
    t2 = _issue("verify", ["7", "a@example.com"], 3600)
    assert _verify("verify", t2, lambda _u: ["a@example.com"]) == 7
    assert _verify("verify", t2, lambda _u: ["b@example.com"]) is None

    # 사용자 없음(lookup None) → 무효
    assert _verify("verify", t2, lambda _u: None) is None

    print("auth demo OK")


if __name__ == "__main__":
    demo()
