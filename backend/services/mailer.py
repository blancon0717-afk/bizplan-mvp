"""이메일 발송 (Resend REST API).

httpx가 이미 의존성에 있으므로 resend SDK는 추가하지 않는다.
RESEND_API_KEY가 없으면 발송하지 않고 링크를 로그에만 남긴다 —
로컬 개발에서 도메인 인증 없이 가입·인증 흐름을 그대로 테스트하기 위함.
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

_API_URL = "https://api.resend.com/emails"
_TIMEOUT_SEC = 10.0

_BRAND = "사업계획서 AI"


def _wrap(title: str, body_html: str, cta_text: str, cta_url: str, footer: str) -> str:
    """인라인 스타일만 사용 — 메일 클라이언트가 <style> 태그를 자주 제거한다."""
    return f"""\
<div style="max-width:520px;margin:0 auto;padding:32px 24px;font-family:-apple-system,'Segoe UI','Malgun Gothic',sans-serif;color:#0f172a">
  <div style="font-size:14px;font-weight:700;color:#2563eb;letter-spacing:-0.01em">{_BRAND}</div>
  <h1 style="font-size:22px;line-height:1.4;margin:16px 0 12px;font-weight:700">{title}</h1>
  <div style="font-size:15px;line-height:1.7;color:#334155">{body_html}</div>
  <a href="{cta_url}"
     style="display:inline-block;margin:24px 0;padding:13px 28px;background:#2563eb;color:#ffffff;
            text-decoration:none;border-radius:8px;font-size:15px;font-weight:600">{cta_text}</a>
  <p style="font-size:13px;line-height:1.6;color:#64748b;margin:0">
    버튼이 눌리지 않으면 아래 주소를 브라우저에 붙여넣어 주세요.<br>
    <span style="color:#94a3b8;word-break:break-all">{cta_url}</span>
  </p>
  <hr style="border:none;border-top:1px solid #e2e8f0;margin:28px 0 16px">
  <p style="font-size:12px;line-height:1.6;color:#94a3b8;margin:0">{footer}</p>
</div>"""


def _send(to: str, subject: str, html: str) -> None:
    """실패해도 예외를 올리지 않는다 — 가입 자체를 막지 않기 위해 로그만 남긴다."""
    api_key = os.getenv("RESEND_API_KEY", "")
    mail_from = os.getenv("MAIL_FROM", "")

    if not api_key or not mail_from:
        logger.warning(
            "[메일] RESEND_API_KEY/MAIL_FROM 미설정 — 발송 생략(로컬 개발 모드). subject=%s\n%s",
            subject, html,
        )
        return

    try:
        res = httpx.post(
            _API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"from": mail_from, "to": [to], "subject": subject, "html": html},
            timeout=_TIMEOUT_SEC,
        )
        if res.status_code >= 400:
            # 응답 본문에 수신자 주소가 섞일 수 있어 상태 코드만 남긴다
            logger.error("[메일] 발송 실패 status=%s subject=%s", res.status_code, subject)
        else:
            logger.info("[메일] 발송 완료 subject=%s", subject)
    except Exception as e:  # noqa: BLE001 — 메일 실패가 가입/재설정을 막으면 안 된다
        logger.error("[메일] 발송 예외 subject=%s: %s", subject, e)


def send_verification_email(to: str, link: str) -> None:
    html = _wrap(
        title="이메일 인증을 완료해주세요",
        body_html=(
            "가입해주셔서 감사합니다.<br>"
            "아래 버튼을 눌러 이메일 인증을 완료하면 바로 이용하실 수 있습니다."
        ),
        cta_text="이메일 인증하기",
        cta_url=link,
        footer="이 링크는 24시간 후 만료됩니다. 본인이 가입한 적이 없다면 이 메일을 무시해주세요.",
    )
    _send(to, f"[{_BRAND}] 이메일 인증을 완료해주세요", html)


def send_password_reset_email(to: str, link: str) -> None:
    html = _wrap(
        title="비밀번호를 재설정하세요",
        body_html=(
            "비밀번호 재설정 요청을 받았습니다.<br>"
            "아래 버튼을 눌러 새 비밀번호를 설정해주세요."
        ),
        cta_text="비밀번호 재설정하기",
        cta_url=link,
        footer=(
            "이 링크는 1시간 후 만료됩니다. 본인이 요청하지 않았다면 이 메일을 무시해주세요. "
            "비밀번호는 변경되지 않습니다."
        ),
    )
    _send(to, f"[{_BRAND}] 비밀번호 재설정 안내", html)
