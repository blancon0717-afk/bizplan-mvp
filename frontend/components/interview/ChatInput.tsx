"use client";
import { useState, useRef, useEffect } from "react";

interface ChatInputProps {
  onSubmit: (text: string) => void;
  onSkip: () => void;
  isSubmitting: boolean;
  placeholder?: string;
  onInputChange?: (hasText: boolean) => void;
}

export default function ChatInput({ onSubmit, onSkip, isSubmitting, placeholder, onInputChange }: ChatInputProps) {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  function handleSubmit() {
    const trimmed = text.trim();
    if (!trimmed || isSubmitting) return;
    onSubmit(trimmed);
    setText("");
    onInputChange?.(false);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }

  return (
    <div className="border-t border-slate-200 bg-white px-4 py-3">
      <div className="max-w-2xl mx-auto">
        <div className="flex items-end gap-3">
          <div className="flex-1 relative">
            <textarea
              ref={textareaRef}
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                onInputChange?.(e.target.value.length > 0);
              }}
              onKeyDown={handleKeyDown}
              placeholder={placeholder ?? "답변을 입력하세요... (Enter로 전송, Shift+Enter로 줄바꿈)"}
              rows={3}
              className="w-full border border-slate-200 rounded-xl px-4 py-3 text-sm text-slate-800 resize-none focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent bg-slate-50 placeholder-slate-400"
            />
          </div>
          <div className="flex flex-col gap-2 pb-0.5">
            <button
              onClick={handleSubmit}
              disabled={!text.trim() || isSubmitting}
              className="w-10 h-10 rounded-xl bg-blue-600 text-white flex items-center justify-center hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {isSubmitting ? (
                <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
              ) : (
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                </svg>
              )}
            </button>
            <button
              onClick={onSkip}
              className="w-10 h-10 rounded-xl border border-slate-200 text-slate-400 flex items-center justify-center hover:bg-slate-50 hover:text-slate-600 transition-colors text-xs"
              title="건너뛰기"
            >
              →
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
