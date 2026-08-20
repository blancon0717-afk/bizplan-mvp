"""회원 계정 저장소 (SQLite).

세션(data/sessions/*.json)과 달리 계정은 영구 데이터이므로 파일 JSON을 쓰지 않는다.
Railway는 재배포 시 파일시스템이 초기화되므로 DB_PATH를 Volume 경로(/data/bizplan.db)로
지정해야 한다. 미지정 시 로컬 개발용 data/bizplan.db를 사용한다.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

import bcrypt

logger = logging.getLogger(__name__)

_DB_PATH = Path(os.getenv("DB_PATH", "data/bizplan.db"))

# 로그인 실패 제한 — 5회 실패 시 10분 잠금 (언락 코드와 동일 정책)
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_MINUTES = 10

USERNAME_RE = re.compile(r"^[a-z0-9]{4,20}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    username             TEXT NOT NULL UNIQUE COLLATE NOCASE,
    email                TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash        TEXT NOT NULL,
    email_verified       INTEGER NOT NULL DEFAULT 0,
    marketing_consent    INTEGER NOT NULL DEFAULT 0,
    marketing_consent_at TEXT,
    terms_agreed_at      TEXT NOT NULL,
    privacy_agreed_at    TEXT NOT NULL,
    unlocked             INTEGER NOT NULL DEFAULT 0,
    unlocked_at          TEXT,
    login_failures       INTEGER NOT NULL DEFAULT 0,
    locked_until         TEXT,
    created_at           TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    """요청마다 새 커넥션 — FastAPI 동기 엔드포인트가 스레드풀에서 돌기 때문."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """앱 기동 시 1회 호출 — 테이블 생성 및 WAL 활성화."""
    with _conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
    logger.info("[계정 DB] 준비 완료 — %s", _DB_PATH)


# ── 비밀번호 ────────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:  # 손상된 해시 — 인증 실패로 취급
        return False


def validate_password(plain: str) -> Optional[str]:
    """정책 위반 시 사용자용 메시지를, 통과 시 None을 반환."""
    if len(plain) < 8:
        return "비밀번호는 8자 이상이어야 합니다."
    if len(plain.encode("utf-8")) > 72:  # bcrypt는 72바이트 초과분을 무시하므로 명시적으로 막는다
        return "비밀번호가 너무 깁니다. (72바이트 이하)"
    kinds = sum([
        bool(re.search(r"[a-zA-Z]", plain)),
        bool(re.search(r"[0-9]", plain)),
        bool(re.search(r"[^a-zA-Z0-9]", plain)),
    ])
    if kinds < 2:
        return "비밀번호는 영문·숫자·특수문자 중 2종류 이상을 포함해야 합니다."
    return None


# ── 조회 ────────────────────────────────────────────────────────────────────

def get_user_by_id(user_id: int) -> Optional[sqlite3.Row]:
    with _conn() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_username(username: str) -> Optional[sqlite3.Row]:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()


def get_user_by_email(email: str) -> Optional[sqlite3.Row]:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()


def username_exists(username: str) -> bool:
    return get_user_by_username(username) is not None


def email_exists(email: str) -> bool:
    return get_user_by_email(email) is not None


# ── 생성 ────────────────────────────────────────────────────────────────────

def create_user(
    username: str,
    email: str,
    password: str,
    marketing_consent: bool,
) -> int:
    """계정 생성 후 user_id 반환. 중복 시 sqlite3.IntegrityError.

    약관·개인정보 동의는 가입의 필수 조건이므로 라우터에서 검증하고
    여기서는 동의 시각만 기록한다(정보통신망법 대응 — 동의 이력 보관).
    """
    now = _now()
    with _conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO users (
                username, email, password_hash, email_verified,
                marketing_consent, marketing_consent_at,
                terms_agreed_at, privacy_agreed_at, created_at
            ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?)
            """,
            (
                username,
                email,
                hash_password(password),
                1 if marketing_consent else 0,
                now if marketing_consent else None,
                now,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


# ── 이메일 인증 ─────────────────────────────────────────────────────────────

def mark_email_verified(user_id: int) -> None:
    with _conn() as conn:
        conn.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (user_id,))


# ── 로그인 실패 제한 ────────────────────────────────────────────────────────

def get_lock_remaining_seconds(user: sqlite3.Row) -> int:
    """잠금 중이면 남은 초, 아니면 0."""
    locked_until = user["locked_until"]
    if not locked_until:
        return 0
    try:
        until = datetime.fromisoformat(locked_until)
    except ValueError:
        return 0
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    remaining = (until - datetime.now(timezone.utc)).total_seconds()
    return int(remaining) if remaining > 0 else 0


def record_login_failure(user_id: int) -> None:
    """실패 누적. 상한 도달 시 잠금 시각을 기록하고 카운터를 리셋한다."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT login_failures FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return
        failures = int(row["login_failures"]) + 1
        if failures >= LOGIN_MAX_FAILURES:
            until = datetime.now(timezone.utc) + timedelta(minutes=LOGIN_LOCK_MINUTES)
            conn.execute(
                "UPDATE users SET login_failures = 0, locked_until = ? WHERE id = ?",
                (until.isoformat(), user_id),
            )
        else:
            conn.execute(
                "UPDATE users SET login_failures = ? WHERE id = ?", (failures, user_id)
            )


def clear_login_failures(user_id: int) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE users SET login_failures = 0, locked_until = NULL WHERE id = ?",
            (user_id,),
        )


# ── 비밀번호 재설정 ─────────────────────────────────────────────────────────

def update_password(user_id: int, new_password: str) -> None:
    """비밀번호 변경 + 로그인 잠금 해제.

    재설정 토큰 서명에 기존 password_hash를 섞기 때문에, 변경 즉시
    이전 재설정 링크는 자동으로 무효가 된다(별도 토큰 테이블 불필요).
    """
    with _conn() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, login_failures = 0, locked_until = NULL WHERE id = ?",
            (hash_password(new_password), user_id),
        )


# ── 결제 언락 (계정 단위) ───────────────────────────────────────────────────

def is_user_unlocked(user_id: int) -> bool:
    user = get_user_by_id(user_id)
    return bool(user and user["unlocked"])


def set_user_unlocked(user_id: int) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE users SET unlocked = 1, unlocked_at = ? WHERE id = ?",
            (_now(), user_id),
        )


def demo() -> None:
    """계정 저장소 자체 점검 (python backend/services/user_store.py).

    임시 DB에 대해 실행하며 운영 DB를 건드리지 않는다.
    """
    global _DB_PATH
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        _DB_PATH = Path(tmp) / "demo.db"
        init_db()

        # 비밀번호 정책
        assert validate_password("short1!") is not None, "8자 미만은 거부"
        assert validate_password("abcdefghij") is not None, "한 종류만 쓰면 거부"
        assert validate_password("abcd1234") is None, "영문+숫자는 통과"

        # 가입 · 해시 (평문 저장 금지)
        uid = create_user("tester01", "user@example.com", "abcd1234!", marketing_consent=True)
        user = get_user_by_username("TESTER01")  # 대소문자 무시 조회
        assert user is not None and int(user["id"]) == uid
        assert user["password_hash"] != "abcd1234!"
        assert verify_password("abcd1234!", user["password_hash"])
        assert not verify_password("wrongpass1!", user["password_hash"])
        assert user["marketing_consent_at"], "마케팅 동의 시각이 기록돼야 한다"
        assert not user["email_verified"], "가입 직후는 미인증"

        # 중복 차단 (대소문자 무시)
        assert username_exists("Tester01") and email_exists("USER@example.com")

        # 로그인 실패 제한 — 상한 도달 시 잠금
        for _ in range(LOGIN_MAX_FAILURES - 1):
            record_login_failure(uid)
        assert get_lock_remaining_seconds(get_user_by_id(uid)) == 0, "상한 전에는 잠기지 않는다"
        record_login_failure(uid)
        assert get_lock_remaining_seconds(get_user_by_id(uid)) > 0, "상한 도달 시 잠긴다"
        clear_login_failures(uid)
        assert get_lock_remaining_seconds(get_user_by_id(uid)) == 0

        # 비밀번호 변경 시 해시가 바뀐다 → 기존 재설정 토큰이 자동 무효가 되는 근거
        before = get_user_by_id(uid)["password_hash"]
        update_password(uid, "newpass99@")
        after = get_user_by_id(uid)["password_hash"]
        assert before != after and verify_password("newpass99@", after)

        # 이메일 인증 · 계정 단위 언락
        mark_email_verified(uid)
        assert get_user_by_id(uid)["email_verified"] == 1
        assert not is_user_unlocked(uid)
        set_user_unlocked(uid)
        assert is_user_unlocked(uid)

    print("user_store demo OK")


if __name__ == "__main__":
    demo()
