"use client";
import { useState } from "react";
import { api } from "@/lib/api";

const LEAD_EMAIL_KEY = "bizplan_lead_email";

/** 이미 이메일을 남긴 사용자인지 (다운로드 게이트 통과 여부) */
export function hasLeadEmail(): boolean {
  try {
    return Boolean(localStorage.getItem(LEAD_EMAIL_KEY));
  } catch {
    return false;
  }
}

interface EmailGateModalProps {
  sessionId: string;
  open: boolean;
  onClose: () => void;
  /** 이메일 제출 성공 후 호출 — 다운로드를 이어서 실행 */
  onDone: () => void;
}

/** DOCX 다운로드 전 이메일 수집 모달. 제출 성공 시 localStorage에 기록해 재요청하지 않는다. */
export default function EmailGateModal({ sessionId, open, onClose, onDone }: EmailGateModalProps) {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (!open) return null;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = email.trim();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(trimmed)) {
      setError("올바른 이메일 주소를 입력해주세요.");
      return;
    }
    setIsSubmitting(true);
    setError(null);
    try {
      await api.submitLead(sessionId, trimmed);
      try {
        localStorage.setItem(LEAD_EMAIL_KEY, trimmed);
      } catch { /* 시크릿 모드 등 저장 실패 시 세션 중 재요청만 감수 */ }
      // GA4 리드 이벤트 (GA 미설정 시 gtag 없음 → no-op)
      (window as unknown as { gtag?: (...args: unknown[]) => void }).gtag?.("event", "lead_submit");
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "저장에 실패했습니다. 잠시 후 다시 시도해주세요.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-slate-900/50 flex items-center justify-center px-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-7"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-bold text-slate-900 mb-2">
          다운로드 전에 이메일을 남겨주세요
        </h2>
        <p className="text-sm text-slate-500 leading-relaxed mb-5">
          DOCX 파일은 무료입니다. 새 지원사업 양식 추가와 정식 오픈 소식을
          이메일로 알려드릴게요.
        </p>
        <form onSubmit={handleSubmit} className="space-y-3">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="name@example.com"
            autoFocus
            className="w-full rounded-xl border border-slate-300 px-4 py-3 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
          {error && <p className="text-xs text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={isSubmitting}
            className="w-full py-3 rounded-xl bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 transition-colors disabled:bg-slate-300 disabled:cursor-not-allowed"
          >
            {isSubmitting ? "저장 중..." : "이메일 남기고 다운로드"}
          </button>
        </form>
        <button
          onClick={onClose}
          className="mt-3 w-full text-center text-xs text-slate-400 hover:text-slate-600 transition-colors"
        >
          닫기
        </button>
      </div>
    </div>
  );
}
