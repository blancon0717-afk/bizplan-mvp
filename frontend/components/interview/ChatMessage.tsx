"use client";
import { useState } from "react";

interface AiMessageProps {
  text: string;
  hint?: string;
  questionNumber: number;
  total: number;
  exampleAnswer?: string;
  showExample?: boolean;
}

export function AiMessage({ text, hint, questionNumber, total, exampleAnswer, showExample }: AiMessageProps) {
  return (
    <div className="flex items-start gap-3 animate-fade-in-up">
      <div className="flex-shrink-0 w-8 h-8 rounded-full bg-blue-600 flex items-center justify-center text-white text-xs font-bold">
        AI
      </div>
      <div className="flex-1 max-w-xl">
        <div className="mb-1">
          <span className="text-xs text-slate-400 font-medium">
            질문 {questionNumber}/{total}
          </span>
        </div>
        <div className="bg-white border border-slate-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm">
          <p className="text-slate-800 text-sm leading-relaxed">{text}</p>
          {hint && (
            <p className="mt-2 text-xs text-slate-400 border-t border-slate-100 pt-2">
              💡 {hint}
            </p>
          )}
          {exampleAnswer && showExample && (
            <p className="mt-2 text-xs text-gray-400 italic border-t border-slate-100 pt-2">
              예시: {exampleAnswer}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

interface UserMessageProps {
  text: string;
  qid: string;
  isEditing: boolean;
  onStartEdit: () => void;
  onSaveEdit: (text: string) => void;
  onCancelEdit: () => void;
}

export function UserMessage({
  text,
  isEditing,
  onStartEdit,
  onSaveEdit,
  onCancelEdit,
}: UserMessageProps) {
  const [editText, setEditText] = useState(text);

  function handleSave() {
    if (!editText.trim()) return;
    onSaveEdit(editText.trim());
  }

  if (isEditing) {
    return (
      <div className="flex justify-end animate-fade-in-up">
        <div className="max-w-xl w-full">
          <textarea
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
            className="w-full border border-blue-300 rounded-2xl rounded-tr-sm px-4 py-3 text-sm text-slate-800 resize-none focus:outline-none focus:ring-2 focus:ring-blue-400 bg-blue-50"
            rows={4}
            autoFocus
          />
          <div className="flex justify-end gap-2 mt-2">
            <button
              onClick={onCancelEdit}
              className="px-3 py-1.5 text-xs text-slate-500 hover:text-slate-700 border border-slate-200 rounded-lg"
            >
              취소
            </button>
            <button
              onClick={handleSave}
              disabled={!editText.trim()}
              className="px-3 py-1.5 text-xs bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
            >
              저장
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-end items-start gap-2 animate-fade-in-up group">
      <button
        onClick={onStartEdit}
        className="opacity-0 group-hover:opacity-100 transition-opacity p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg"
        title="답변 수정"
      >
        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"
          />
        </svg>
      </button>
      <div className="max-w-xl">
        <div className="bg-blue-600 text-white rounded-2xl rounded-tr-sm px-4 py-3 shadow-sm">
          <p className="text-sm leading-relaxed whitespace-pre-wrap">{text}</p>
        </div>
      </div>
    </div>
  );
}
