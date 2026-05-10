"use client";
import { useEffect, useRef, useState } from "react";
import type { SectionResult } from "@/lib/types";

const SEVERITY_STYLES = {
  critical: { icon: "🔴", label: "중요", ring: "ring-red-200 bg-red-50" },
  warning: { icon: "🟡", label: "권장", ring: "ring-amber-200 bg-amber-50" },
  info: { icon: "🔵", label: "참고", ring: "ring-blue-200 bg-blue-50" },
};

interface MemoPanelProps {
  sections: SectionResult[];
  activeSectionId: string | null;
  scrollToMemoIndex?: number | null;
  showAnchors?: boolean;
  onMemoChange: (sectionId: string, memoIndex: number, response: string) => void;
  onRegenerate: (sectionId: string, memoIndex: number, memoResponse: string) => void;
  isRegenerating: Record<string, boolean>;
}

interface MemoCardProps {
  index: number;
  anchorText: string;
  note: string;
  severity: "critical" | "warning" | "info";
  response: string;
  onChange: (value: string) => void;
  onRegenerate: (response: string) => void;
  isRegenerating: boolean;
}

function MemoCard({ index, anchorText, note, severity, response, onChange, onRegenerate, isRegenerating }: MemoCardProps) {
  const [value, setValue] = useState(response);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => { setValue(response); }, [response]);

  function handleChange(v: string) {
    setValue(v);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => onChange(v), 600);
  }

  const style = SEVERITY_STYLES[severity] ?? SEVERITY_STYLES.warning;

  return (
    <div className={`rounded-xl ring-1 ${style.ring} p-3`}>
      <div className="flex items-start gap-2 mb-2">
        <span className="text-xs font-bold text-slate-400">[{index + 1}]</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 mb-1">
            <span>{style.icon}</span>
            <span className="text-xs font-medium text-slate-600">{anchorText}</span>
          </div>
          <p className="text-xs text-slate-600 leading-relaxed">{note}</p>
        </div>
      </div>
      <textarea
        value={value}
        onChange={(e) => handleChange(e.target.value)}
        placeholder="보완 내용을 입력하세요..."
        rows={3}
        className={`w-full text-xs border rounded-lg px-3 py-2 resize-none focus:outline-none focus:ring-2 focus:ring-blue-300 transition-colors ${
          value.trim() ? "border-emerald-300 bg-emerald-50/50" : "border-slate-200 bg-slate-50"
        }`}
      />
      {value.trim() && (
        <button
          onClick={() => onRegenerate(value)}
          disabled={isRegenerating}
          className="mt-2 w-full py-1.5 rounded-lg text-xs font-semibold bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 flex items-center justify-center gap-1.5 transition-colors"
        >
          {isRegenerating ? (
            <>
              <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />
              반영 중...
            </>
          ) : (
            "본문에 반영하기 →"
          )}
        </button>
      )}
    </div>
  );
}

export default function MemoPanel({ sections, activeSectionId, scrollToMemoIndex, showAnchors = false, onMemoChange, onRegenerate, isRegenerating }: MemoPanelProps) {
  const section = sections.find((s) => s.section_id === activeSectionId);
  const memoRefs = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    if (scrollToMemoIndex !== null && scrollToMemoIndex !== undefined) {
      memoRefs.current[scrollToMemoIndex]?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [scrollToMemoIndex]);

  if (!showAnchors) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3 px-6 text-center">
        <div className="w-10 h-10 rounded-full bg-slate-100 flex items-center justify-center text-xl">📋</div>
        <p className="text-sm font-medium text-slate-600">메모 패널</p>
        <p className="text-xs text-slate-400 leading-relaxed">
          상단의 <span className="font-medium text-blue-500">피드백 확인하기</span>를 클릭하면<br />
          보완이 필요한 항목이 여기에 표시됩니다
        </p>
      </div>
    );
  }

  if (!section) {
    return (
      <div className="h-full flex items-center justify-center text-slate-400 text-sm">
        섹션을 선택하면 메모가 표시됩니다
      </div>
    );
  }

  // critical은 본문 하이라이트로만 표시 — 패널에서 제외
  const visibleMemos = section.inline_suggestions
    .map((m, originalIndex) => ({ ...m, originalIndex }))
    .filter((m) => m.severity !== "critical");

  return (
    <div className="h-full flex flex-col">
      <div className="flex-shrink-0 px-4 py-3 border-b border-slate-100">
        <h3 className="font-semibold text-slate-700 text-sm truncate">
          {section.section_title}
        </h3>
        <p className="text-xs text-slate-400 mt-0.5">
          메모 {section.resolved_memo_count}/{visibleMemos.length} 해소 · {section.effective_completion_score}% 완성
        </p>
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar px-4 py-3">
        {visibleMemos.length === 0 ? (
          <div className="text-center py-8 text-slate-400 text-sm">
            <p>이 섹션에는 메모가 없습니다</p>
            <p className="text-xs mt-1">섹션 고도화로 더 풍부한 초안을 만들어보세요</p>
          </div>
        ) : (
          <div className="space-y-3">
            {visibleMemos.map((memo) => (
              <div
                key={memo.originalIndex}
                ref={(el) => { memoRefs.current[memo.originalIndex] = el; }}
              >
                <MemoCard
                  index={memo.originalIndex}
                  anchorText={memo.anchor_text}
                  note={memo.note}
                  severity={memo.severity}
                  response={memo.response}
                  onChange={(val) => onMemoChange(section.section_id, memo.originalIndex, val)}
                  onRegenerate={(val) => onRegenerate(section.section_id, memo.originalIndex, val)}
                  isRegenerating={!!isRegenerating[section.section_id]}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
