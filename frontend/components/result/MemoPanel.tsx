"use client";
import { forwardRef, useImperativeHandle, useRef } from "react";
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
  onMemoTitleClick?: (anchorText: string) => void;
}

interface FeedbackCardProps {
  index: number;
  anchorText: string;
  note: string;
  severity: "critical" | "warning" | "info";
  onAnchorClick?: () => void;
}

function FeedbackCard({ index, anchorText, note, severity, onAnchorClick }: FeedbackCardProps) {
  const style = SEVERITY_STYLES[severity] ?? SEVERITY_STYLES.warning;

  return (
    <div className={`rounded-xl ring-1 ${style.ring} p-3`}>
      <div className="flex items-start gap-2">
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
          </div>
          <p className="text-xs text-slate-600 leading-relaxed">{note}</p>
        </div>
      </div>
    </div>
  );
}

const MemoPanel = forwardRef<MemoPanelHandle, MemoPanelProps>(
  function MemoPanel({ sections, activeSectionId, showAnchors = false, onMemoTitleClick }, ref) {
    const section = sections.find((s) => s.section_id === activeSectionId);
    const memoRefs = useRef<Record<number, HTMLDivElement | null>>({});

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
          <div className="w-10 h-10 rounded-full bg-slate-100 flex items-center justify-center text-xl">⚖️</div>
          <p className="text-sm font-medium text-slate-600">심사위원 피드백</p>
          <p className="text-xs text-slate-400 leading-relaxed">
            상단의 <span className="font-medium text-blue-500">피드백 확인하기</span>를 클릭하면<br />
            심사위원 관점의 피드백이 여기에 표시됩니다
          </p>
        </div>
      );
    }

    if (!section) {
      return (
        <div className="h-full flex items-center justify-center text-slate-400 text-sm">
          섹션을 선택하면 피드백이 표시됩니다
        </div>
      );
    }

    const visibleMemos = section.inline_suggestions
      .map((m, originalIndex) => ({ ...m, originalIndex }));

    const fullText = (section.content_segments ?? []).map((s) => s.text ?? "").join("");
    const sortedAll = [...visibleMemos]
      .sort((a, b) => {
        const posA = fullText.indexOf(a.anchor_text);
        const posB = fullText.indexOf(b.anchor_text);
        return posA - posB;
      })
      .slice(0, 5);
    const orderedMemos = sortedAll.map((m, idx) => ({ ...m, displayIndex: idx }));

    return (
      <div className="h-full flex flex-col">
        <div className="flex-shrink-0 px-4 py-3 border-b border-slate-100">
          <h3 className="font-semibold text-slate-700 text-sm truncate">
            {section.section_title}
          </h3>
          <p className="text-xs text-slate-400 mt-0.5">
            심사위원 피드백 {orderedMemos.length}건 · {section.effective_completion_score}% 완성
          </p>
        </div>

        {/* 피드백 심각도 범례 */}
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
              <p>이 섹션에는 피드백이 없습니다</p>
            </div>
          ) : (
            <div className="space-y-3">
              {orderedMemos.map((memo) => (
                <div
                  key={memo.originalIndex}
                  ref={(el) => { memoRefs.current[memo.displayIndex] = el; }}
                >
                  <FeedbackCard
                    index={memo.displayIndex}
                    anchorText={memo.anchor_text}
                    note={memo.note}
                    severity={memo.severity}
                    onAnchorClick={() => onMemoTitleClick?.(memo.anchor_text)}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }
);
MemoPanel.displayName = "MemoPanel";
export default MemoPanel;
