"""언락 코드 발급 (운영자 전용).

입금 확인 후 실행해 고객에게 코드를 전달한다.
코드는 HMAC-SHA256(UNLOCK_SECRET, session_id) 앞 8자리 — 서버와 동일 유도식.

사용법:
    python scripts/issue_unlock_code.py <session_id>

주의: 서버(.env 또는 Railway 환경변수)와 같은 UNLOCK_SECRET을 써야 코드가 일치한다.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sys
from pathlib import Path


def _load_env(path: Path) -> None:
    """프로젝트 루트 .env 로드 (python-dotenv 없이 단순 파싱)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    if len(sys.argv) != 2:
        print("사용법: python scripts/issue_unlock_code.py <session_id>")
        return 1
    session_id = sys.argv[1].strip()

    _load_env(Path(__file__).resolve().parent.parent / ".env")
    secret = os.getenv("UNLOCK_SECRET", "")
    if not secret:
        print("오류: UNLOCK_SECRET이 설정되지 않았습니다 (.env 확인)")
        return 1

    code = hmac.new(secret.encode(), session_id.encode(), hashlib.sha256).hexdigest()[:8].upper()
    print(f"세션      : {session_id}")
    print(f"언락 코드 : {code}")
    print("고객이 결제 안내 모달에 이 코드를 입력하면 전문이 열립니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
