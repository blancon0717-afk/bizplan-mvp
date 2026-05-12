"use client";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { SectionResult } from "@/lib/types";

export interface MemoPanelHandle {
  scrollToMemo: (originalIndex: number) => void;
}

const SEVERITY_STYLES = {
  critical: { icon: "🔴", label: "중요", ring: "ring-red-200 bg-red-50" },
  warning: { icon: "🟡", label: "권장", ring: "ring-amber-200 bg-amber-50" },
  info: { icon: "🔵", label: "참고", ring: "ring-blue-200 bg-blue-50" },
};

interface MemoPanelProps {
  sections: SectionResult[];
  activeSectionId: string | null;
  showAnchors?: boolean;
  onMemoChange: (sectionId: string, memoIndex: number, response: string) => void;
  onRegenerate: (sectionId: string, memoIndex: number, memoResponse: string) => void;
  onMemoTitleClick?: (anchorText: string) => void;
  isRegenerating: Record<string, boolean>;
  usageData?: Record<string, { used: number; max: number }>;
  onPassMemo: (sectionId: string, memoIndex: number) => void;
  passedMemos?: Set<number>;
}

interface MemoCardProps {
  index: number;
  anchorText: string;
  note: string;
  severity: "critical" | "warning" | "info";
  response: string;
  onChange: (value: string) => void;
  onRegenerate: (response: string) => void;
  onAnchorClick?: () => void;
  isRegenerating: boolean;
  onPass: () => void;
  isPassed?: boolean;
  isApplied?: boolean;
}

function MemoCard({ index, anchorText, note, severity, response, onChange, onRegenerate, onAnchorClick, isRegenerating, onPass, isApplied }: MemoCardProps) {
  const [value, setValue] = useState(response);
  const [showConfirm, setShowConfirm] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => { if (!isApplied) setValue(response); }, [response, isApplied]);

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
            <button
              onClick={onAnchorClick}
              className="text-xs font-medium text-slate-600 hover:text-blue-600 hover:underline text-left"
            >
              {anchorText || (note.slice(0, 20) + (note.length > 20 ? "..." : ""))}
            </button>
            {isApplied && (
              <span className="text-xs text-emerald-600 font-medium">✓ 반영됨</span>
            )}
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
      {value.trim() && !showConfirm && (
        <button
          onClick={() => setShowConfirm(true)}
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
      {showConfirm && (
        <div className="mt-2 p-2 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-700">
          <p className="mb-2">해당 메모 내용을 반영하여 섹션이 재생성됩니다. 다른 섹션에도 관련 내용이 있을 수 있으니 확인해주세요. 계속할까요?</p>
          <div className="flex gap-2">
            <button
              onClick={() => { setShowConfirm(false); onRegenerate(value); }}
              className="flex-1 py-1 rounded bg-blue-600 text-white font-semibold hover:bg-blue-700"
            >
              확인
            </button>
            <button
              onClick={() => setShowConfirm(false)}
              className="flex-1 py-1 rounded bg-slate-100 text-slate-600 hover:bg-slate-200"
            >
              취소
            </button>
          </div>
        </div>
      )}
      <button
        onClick={onPass}
        className="mt-2 w-full py-1.5 rounded-lg text-xs font-semibold bg-slate-100 text-slate-400 hover:bg-slate-200 transition-colors"
      >
        이 메모 패스하기
      </button>
    </div>
  );
}

const MemoPanel = forwardRef<MemoPanelHandle, MemoPanelProps>(
  function MemoPanel({ sections, activeSectionId, showAnchors = false, onMemoChange, onRegenerate, onMemoTitleClick, isRegenerating, usageData, onPassMemo, passedMemos = new Set() }, ref) {
    const section = sections.find((s) => s.section_id === activeSectionId);
    const memoRefs = useRef<Record<number, HTMLDivElement | null>>({});
    const [appliedMemos, setAppliedMemos] = useState<Record<string, Set<number>>>({});

    useImperativeHandle(ref, () => ({
      scrollToMemo: (displayIndex: number) => {
        const el = memoRefs.current[displayIndex];
        if (!el) {
          const keys = Object.keys(memoRefs.current).map(Number).filter(k => memoRefs.current[k]);
          if (keys.length === 0) return;
          const closest = keys.reduce((a, b) => Math.abs(a - displayIndex) < Math.abs(b - displayIndex) ? a : b);
          const fallback = memoRefs.current[closest];
          if (fallback) {
            const container = fallback.closest('.overflow-y-auto');
            if (container) {
              const containerRect = container.getBoundingClientRect();
              const fallbackRect = fallback.getBoundingClientRect();
              container.scrollTop = container.scrollTop + fallbackRect.top - containerRect.top;
            }
          }
          return;
        }
        const container = el.closest('.overflow-y-auto');
        if (container) {
          const containerRect = container.getBoundingClientRect();
          const elRect = el.getBoundingClientRect();
          container.scrollTop = container.scrollTop + elRect.top - containerRect.top;
        } else {
          el.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      },
    }));

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

    const visibleMemos = section.inline_suggestions
      .map((m, originalIndex) => ({ ...m, originalIndex }));

    const fullText = (section.content_segments ?? []).map((s) => s.text ?? "").join("");
    const SEVERITY_ORDER: Record<string, number> = { critical: 0, warning: 1, info: 2 };
    const sortedAll = [...visibleMemos]
      .sort((a, b) => {
        const posA = fullText.indexOf(a.anchor_text);
        const posB = fullText.indexOf(b.anchor_text);
        return posA - posB;
      })
      .slice(0, 5);
    const sortedAllWithIndex = sortedAll.map((m, idx) => ({ ...m, displayIndex: idx }));
    const sortedVisibleMemos = sortedAllWithIndex.filter((m) => !passedMemos.has(m.originalIndex));
    const unAppliedMemos = sortedVisibleMemos.filter(m => !(appliedMemos[section.section_id] ?? new Set()).has(m.originalIndex));
    const appliedMemosList = sortedVisibleMemos.filter(m => (appliedMemos[section.section_id] ?? new Set()).has(m.originalIndex));
    const orderedMemos = [...unAppliedMemos, ...appliedMemosList];

    return (
      <div className="h-full flex flex-col">
        <div className="flex-shrink-0 px-4 py-3 border-b border-slate-100">
          <h3 className="font-semibold text-slate-700 text-sm truncate">
            {section.section_title}
          </h3>
          <p className="text-xs text-slate-400 mt-0.5">
            메모 {section.resolved_memo_count}/{sortedVisibleMemos.length} 해소 · {section.effective_completion_score}% 완성
          </p>
          {usageData?.memo && (
            <p className={`text-xs mt-0.5 ${(usageData.memo.used ?? 0) >= (usageData.memo.max ?? 3) ? "text-gray-400" : "text-blue-500"}`}>
              반영 ({usageData.memo.used}/{usageData.memo.max})
            </p>
          )}
        </div>

        {/* 메모 심각도 범례 */}
        <div className="flex-shrink-0 px-4 py-1.5 border-b border-slate-100 bg-slate-50">
          <div className="flex items-center gap-4 text-xs text-slate-400">
            <span className="flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-red-400 inline-block flex-shrink-0" />
              교체 필요
            </span>
            <span className="flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 inline-block flex-shrink-0" />
              검토 권장
            </span>
            <span className="flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-blue-400 inline-block flex-shrink-0" />
              선택 개선
            </span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto custom-scrollbar px-4 py-3">
          {orderedMemos.length === 0 ? (
            <div className="text-center py-8 text-slate-400 text-sm">
              <p>이 섹션에는 메모가 없습니다</p>
              <p className="text-xs mt-1">섹션 고도화로 더 풍부한 초안을 만들어보세요</p>
            </div>
          ) : (
            <div className="space-y-3">
              {orderedMemos.map((memo, i) => {
                const isApplied = (appliedMemos[section.section_id] ?? new Set()).has(memo.originalIndex);
                return (
                  <div
                    key={memo.originalIndex}
                    ref={(el) => { memoRefs.current[memo.displayIndex] = el; }}
                    className={`transition-all duration-500 ease-in-out ${isApplied ? "rounded-lg border bg-emerald-50 border-emerald-200 opacity-80" : "opacity-100"}`}
                  >
                    <MemoCard
                      index={memo.displayIndex}
                      anchorText={memo.anchor_text}
                      note={memo.note}
                      severity={memo.severity}
                      response={memo.response}
                      onChange={(val) => onMemoChange(section.section_id, memo.originalIndex, val)}
                      onRegenerate={(val) => {
                        setAppliedMemos(prev => ({
                          ...prev,
                          [section.section_id]: new Set([...(prev[section.section_id] ?? []), memo.originalIndex])
                        }));
                        onRegenerate(section.section_id, memo.originalIndex, val);
                      }}
                      onAnchorClick={() => onMemoTitleClick?.(memo.anchor_text)}
                      isRegenerating={!!isRegenerating[section.section_id]}
                      isApplied={isApplied}
                      onPass={() => { onPassMemo(section.section_id, memo.originalIndex); }}
                    />
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    );
  }
);
MemoPanel.displayName = "MemoPanel";
export default MemoPanel;
