from __future__ import annotations

import io
import logging

import httpx
import pdfplumber

log = logging.getLogger(__name__)


def download_pdf(url: str, timeout: float = 60.0) -> bytes:
    with httpx.Client(timeout=timeout, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.content


def extract_text(pdf_bytes: bytes, max_chars: int = 60_000) -> str:
    parts: list[str] = []
    total = 0
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            try:
                t = page.extract_text() or ""
            except Exception as e:
                log.debug("page text extract failed: %s", e)
                t = ""
            if not t:
                continue
            parts.append(t)
            total += len(t)
            if total >= max_chars:
                break
    return "\n".join(parts)[:max_chars]
