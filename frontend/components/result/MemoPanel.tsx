"use client";
import { forwardRef, useImperativeHandle, useRef } from "react";
import type { SectionResult } from "@/lib/types";

export interface MemoPanelHandle {
  scrollToMemo: (originalIndex: number) => void;
}

const SEVERITY_STYLES = {
  critical: { label: "교체 필요", border: "border-l-red-400", chip: "bg-red-50 text-red-600" },
  warning: { label: "검토 권장", border: "border-l-amber-400", chip: "bg-amber-50 text-amber-700" },
  info: { label: "선택 개선", border: "border-l-blue-400", chip: "bg-blue-50 text-blue-600" },
};

interface MemoPanelProps {
  sections: SectionResult[];
  activeSectionId: string | null;
  showAnchors?: boolean;
  onMemoTitleClick?: (anchorText: string) => void;
  /** 섹션 미선택 상태에서 섹션 행 클릭 시 호출 */
  onSectionSelect?: (sectionId: string) => void;
  /** 빈 상태 CTA — 피드백 생성 트리거 (없으면 안내 문구만 표시) */
  onRequestFeedback?: () => void;
  isFeedbackRunning?: boolean;
  /** 피드백 카드 호버 시 문서 앵커 하이라이트 연동 (null이면 해제) */
  onMemoHover?: (anchorText: string | null) => void;
}

interface FeedbackCardProps {
  index: number;
  anchorText: string;
  note: string;
  severity: "critical" | "warning" | "info";
  onAnchorClick?: () => void;
  onHover?: (anchorText: string | null) => void;
}

function FeedbackCard({ index, anchorText, note, severity, onAnchorClick, onHover }: FeedbackCardProps) {
  const style = SEVERITY_STYLES[severity] ?? SEVERITY_STYLES.warning;

  return (
    <div
      className={`bg-white rounded-lg ring-1 ring-slate-200 border-l-[3px] ${style.border} p-3 transition-shadow hover:shadow-sm`}
      onMouseEnter={() => anchorText && onHover?.(anchorText)}
      onMouseLeave={() => onHover?.(null)}
    >
      <div className="flex items-center gap-2 mb-1.5">
        <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${style.chip}`}>
          {style.label}
        </span>
        <span className="text-[11px] font-medium text-slate-400">[{index + 1}]</span>
      </div>
      {anchorText && (
        <button
          onClick={onAnchorClick}
          className="block w-full text-left text-[13px] font-medium text-slate-700 leading-snug hover:text-blue-600 transition-colors"
        >
          &ldquo;{anchorText}&rdquo;
        </button>
      )}
      <p className={`text-xs text-slate-500 leading-relaxed ${anchorText ? "mt-1.5" : ""}`}>{note}</p>
    </div>
  );
}

const MemoPanel = forwardRef<MemoPanelHandle, MemoPanelProps>(
  function MemoPanel({ sections, activeSectionId, showAnchors = false, onMemoTitleClick, onSectionSelect, onRequestFeedback, isFeedbackRunning = false, onMemoHover }, ref) {
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

    // 피드백 생성 전 — 빈 상태 + 패널 내 CTA
    if (!showAnchors) {
      return (
        <div className="h-full flex flex-col items-center justify-center gap-4 px-8 text-center">
          <p className="text-sm font-semibold text-slate-700">심사위원 피드백</p>
          <p className="text-xs text-slate-400 leading-relaxed">
            심사위원 관점에서 초안의 약점을 분석하고<br />
            문장 단위로 보완 포인트를 짚어드립니다
          </p>
          {onRequestFeedback && (
            <button
              onClick={onRequestFeedback}
              disabled={isFeedbackRunning}
              className="flex items-center gap-2 px-4 py-2 rounded-xl bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors shadow-sm"
            >
              {isFeedbackRunning && (
                <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
              )}
              {isFeedbackRunning ? "피드백 생성 중..." : "피드백 확인하기"}
            </button>
          )}
        </div>
      );
    }

    // 전체 심각도 요약 (모든 섹션 합산)
    const totals = { critical: 0, warning: 0, info: 0 };
    for (const s of sections) {
      for (const m of s.inline_suggestions) {
        if (m.severity in totals) totals[m.severity as keyof typeof totals]++;
      }
    }

    const summaryStrip = (
      <div className="flex-shrink-0 px-4 py-3 border-b border-slate-200 bg-white">
        <div className="flex items-center gap-2">
          {(["critical", "warning", "info"] as const).map((sev) => (
            <div key={sev} className={`flex-1 rounded-lg px-2.5 py-2 ${SEVERITY_STYLES[sev].chip}`}>
              <div className="text-lg font-bold leading-none animate-count-up">{totals[sev]}</div>
              <div className="text-[10px] font-medium mt-1 opacity-80">{SEVERITY_STYLES[sev].label}</div>
            </div>
          ))}
        </div>
      </div>
    );

    // 섹션 미선택 — 섹션별 피드백 개수 목록
    if (!section) {
      return (
        <div className="h-full flex flex-col">
          {summaryStrip}
          <div className="flex-1 overflow-y-auto custom-scrollbar px-3 py-3">
            <p className="text-xs text-slate-400 px-1 mb-2">섹션을 선택하면 상세 피드백이 표시됩니다</p>
            <div className="space-y-0.5">
              {sections.map((s) => (
                <button
                  key={s.section_id}
                  onClick={() => onSectionSelect?.(s.section_id)}
                  className="w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg text-left hover:bg-white transition-colors"
                >
                  <span className="text-xs text-slate-600 truncate">
                    {s.section_id !== "overview" && <span className="text-slate-400 mr-1">{s.section_id}.</span>}
                    {s.section_title}
                  </span>
                  <span className="text-xs text-slate-400 flex-shrink-0">
                    {s.inline_suggestions.length}건
                  </span>
                </button>
              ))}
            </div>
          </div>
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
        {summaryStrip}
        <div className="flex-shrink-0 px-4 py-3 border-b border-slate-100">
          <h3 className="font-semibold text-slate-700 text-sm truncate">
            {section.section_title}
          </h3>
          <p className="text-xs text-slate-400 mt-0.5">
            심사위원 피드백 {orderedMemos.length}건 · {section.effective_completion_score}% 완성
          </p>
        </div>

        <div className="flex-1 overflow-y-auto custom-scrollbar px-4 py-3">
          {orderedMemos.length === 0 ? (
            <div className="text-center py-8 text-slate-400 text-sm">
              <p>이 섹션에는 피드백이 없습니다</p>
            </div>
          ) : (
            <div className="space-y-2.5">
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
                    onHover={onMemoHover}
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
