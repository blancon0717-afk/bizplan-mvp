"""회원가입 · 로그인 · 이메일 인증 · 비밀번호 재설정 API."""
from __future__ import annotations

import logging
import os
import sqlite3
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from auth import (
    clear_session_cookie,
    get_current_user,
    issue_reset_token,
    issue_verify_token,
    set_session_cookie,
    verify_reset_token,
    verify_verify_token,
)
from services.mailer import send_password_reset_email, send_verification_email
from services.user_store import (
    EMAIL_RE,
    USERNAME_RE,
    clear_login_failures,
    create_user,
    email_exists,
    get_lock_remaining_seconds,
    get_user_by_email,
    get_user_by_username,
    mark_email_verified,
    record_login_failure,
    update_password,
    username_exists,
    validate_password,
    verify_password,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# 메일 재발송 쿨다운 60초.
# ponytail: 프로세스 메모리 dict — 백엔드가 단일 인스턴스(start_backend.ps1)라 충분하다.
#           인스턴스를 늘리면 DB 컬럼이나 Redis로 옮길 것.
_RESEND_COOLDOWN_SEC = 60
_last_sent: dict[str, float] = {}


def _base_url() -> str:
    return os.getenv("APP_BASE_URL", "http://localhost:3000").rstrip("/")


def _send_verification(background: BackgroundTasks, user_id: int, email: str) -> None:
    link = f"{_base_url()}/api/auth/verify?token={issue_verify_token(user_id, email)}"
    background.add_task(send_verification_email, email, link)


def _public_user(user: sqlite3.Row) -> dict:
    return {
        "username": user["username"],
        "email": user["email"],
        "email_verified": bool(user["email_verified"]),
        "marketing_consent": bool(user["marketing_consent"]),
        "unlocked": bool(user["unlocked"]),
    }


# ── 회원가입 ────────────────────────────────────────────────────────────────

class SignupBody(BaseModel):
    username: str = Field(max_length=32)
    password: str = Field(max_length=200)
    email: str = Field(max_length=254)
    terms_agreed: bool = False
    privacy_agreed: bool = False
    marketing_consent: bool = False


@router.post("/signup", status_code=201)
def signup(body: SignupBody, background: BackgroundTasks):
    username = body.username.strip().lower()
    email = body.email.strip().lower()

    if not USERNAME_RE.match(username):
        raise HTTPException(status_code=422, detail="아이디는 영문 소문자·숫자 4~20자입니다.")
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="올바른 이메일 형식이 아닙니다.")
    if (msg := validate_password(body.password)) is not None:
        raise HTTPException(status_code=422, detail=msg)
    if not body.terms_agreed or not body.privacy_agreed:
        raise HTTPException(
            status_code=422,
            detail="이용약관과 개인정보 수집·이용에 동의해야 가입할 수 있습니다.",
        )

    if username_exists(username):
        raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다.")
    if email_exists(email):
        raise HTTPException(status_code=409, detail="이미 가입된 이메일입니다.")

    try:
        user_id = create_user(username, email, body.password, body.marketing_consent)
    except sqlite3.IntegrityError:  # 동시 가입 경합
        raise HTTPException(status_code=409, detail="이미 사용 중인 아이디 또는 이메일입니다.")

    _last_sent[email] = time.time()
    _send_verification(background, user_id, email)
    logger.info("[가입] user_id=%s 인증메일 발송 요청", user_id)
    return {"ok": True, "message": "인증 메일을 보냈습니다. 메일함을 확인해주세요."}


@router.get("/check-username")
def check_username(username: str):
    """가입 폼 실시간 중복 확인."""
    name = username.strip().lower()
    if not USERNAME_RE.match(name):
        return {"available": False, "reason": "아이디는 영문 소문자·숫자 4~20자입니다."}
    if username_exists(name):
        return {"available": False, "reason": "이미 사용 중인 아이디입니다."}
    return {"available": True, "reason": "사용할 수 있는 아이디입니다."}


# ── 이메일 인증 ─────────────────────────────────────────────────────────────

@router.get("/verify")
def verify_email(token: str):
    """메일의 인증 링크. 결과 화면으로 리다이렉트한다."""
    user_id = verify_verify_token(token)
    if user_id is None:
        return RedirectResponse(url=f"{_base_url()}/verify?status=invalid", status_code=303)
    mark_email_verified(user_id)
    logger.info("[인증] user_id=%s 이메일 인증 완료", user_id)
    return RedirectResponse(url=f"{_base_url()}/verify?status=ok", status_code=303)


class EmailBody(BaseModel):
    email: str = Field(max_length=254)


@router.post("/resend")
def resend_verification(body: EmailBody, background: BackgroundTasks):
    """인증 메일 재발송. 계정 존재 여부는 노출하지 않는다."""
    email = body.email.strip().lower()
    generic = {"ok": True, "message": "가입된 계정이라면 인증 메일을 다시 보냈습니다."}

    if time.time() - _last_sent.get(email, 0.0) < _RESEND_COOLDOWN_SEC:
        raise HTTPException(status_code=429, detail="잠시 후 다시 시도해주세요. (1분에 1회)")

    user = get_user_by_email(email)
    if user is None or user["email_verified"]:
        return generic

    _last_sent[email] = time.time()
    _send_verification(background, int(user["id"]), email)
    return generic


# ── 로그인 / 로그아웃 ───────────────────────────────────────────────────────

class LoginBody(BaseModel):
    username: str = Field(max_length=32)
    password: str = Field(max_length=200)


@router.post("/login")
def login(body: LoginBody, response: Response):
    # 아이디·비밀번호 어느 쪽이 틀렸는지 구분하지 않는다(계정 열거 방지)
    fail = HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")

    user = get_user_by_username(body.username.strip().lower())
    if user is None:
        raise fail

    if (remaining := get_lock_remaining_seconds(user)) > 0:
        raise HTTPException(
            status_code=429,
            detail=f"로그인 시도가 많습니다. {remaining // 60 + 1}분 후 다시 시도해주세요.",
        )

    if not verify_password(body.password, user["password_hash"]):
        record_login_failure(int(user["id"]))
        raise fail

    if not user["email_verified"]:
        raise HTTPException(
            status_code=403,
            detail="이메일 인증이 필요합니다. 메일함을 확인해주세요.",
        )

    clear_login_failures(int(user["id"]))
    set_session_cookie(response, int(user["id"]))
    logger.info("[로그인] user_id=%s", user["id"])
    return {"ok": True, "user": _public_user(user)}


@router.post("/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(user: sqlite3.Row = Depends(get_current_user)):
    return {"user": _public_user(user)}


# ── 비밀번호 찾기 / 재설정 ──────────────────────────────────────────────────

@router.post("/forgot")
def forgot_password(body: EmailBody, background: BackgroundTasks):
    """재설정 메일 발송. 가입 여부와 무관하게 동일한 응답을 준다."""
    email = body.email.strip().lower()
    generic = {"ok": True, "message": "가입된 계정이라면 재설정 메일을 보냈습니다."}
    key = f"reset:{email}"

    if time.time() - _last_sent.get(key, 0.0) < _RESEND_COOLDOWN_SEC:
        raise HTTPException(status_code=429, detail="잠시 후 다시 시도해주세요. (1분에 1회)")

    user = get_user_by_email(email)
    if user is None:
        return generic

    _last_sent[key] = time.time()
    token = issue_reset_token(int(user["id"]), user["password_hash"])
    link = f"{_base_url()}/reset?token={token}"
    background.add_task(send_password_reset_email, email, link)
    logger.info("[비밀번호 재설정] user_id=%s 메일 발송 요청", user["id"])
    return generic


class ResetBody(BaseModel):
    token: str = Field(max_length=500)
    password: str = Field(max_length=200)


@router.post("/reset")
def reset_password(body: ResetBody, response: Response):
    if (msg := validate_password(body.password)) is not None:
        raise HTTPException(status_code=422, detail=msg)

    user_id = verify_reset_token(body.token)
    if user_id is None:
        raise HTTPException(
            status_code=400,
            detail="만료되었거나 이미 사용된 링크입니다. 재설정을 다시 요청해주세요.",
        )

    update_password(user_id, body.password)
    # 재설정 링크를 열었다는 건 메일 수신이 확인된 것 — 미인증 계정이면 함께 인증 처리
    mark_email_verified(user_id)
    clear_session_cookie(response)
    logger.info("[비밀번호 재설정] user_id=%s 완료", user_id)
    return {"ok": True, "message": "비밀번호가 변경되었습니다. 새 비밀번호로 로그인해주세요."}
