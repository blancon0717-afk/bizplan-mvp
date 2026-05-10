"use client";
import type { SectionProgress } from "@/lib/types";

const CONFIDENCE_STYLES = {
  green: { dot: "bg-emerald-500", label: "근거 충분", text: "text-emerald-700" },
  yellow: { dot: "bg-amber-400", label: "일부 추론", text: "text-amber-700" },
  red: { dot: "bg-red-500", label: "보완 필요", text: "text-red-700" },
};

interface SectionProgressItemProps {
  section: SectionProgress;
  index: number;
}

export default function SectionProgressItem({ section, index }: SectionProgressItemProps) {
  const conf = section.confidence_level
    ? CONFIDENCE_STYLES[section.confidence_level]
    : null;

  return (
    <div
      className={`flex items-center gap-4 p-4 rounded-xl border transition-all duration-300 ${
        section.status === "done"
          ? "bg-white border-slate-200 shadow-sm"
          : section.status === "generating"
          ? "bg-blue-50 border-blue-200"
          : "bg-slate-50 border-slate-100"
      }`}
    >
      {/* 상태 아이콘 */}
      <div className="flex-shrink-0 w-8 h-8 flex items-center justify-center">
        {section.status === "done" && conf ? (
          <div className={`w-3 h-3 rounded-full ${conf.dot}`} />
        ) : section.status === "generating" ? (
          <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        ) : (
          <div className="w-3 h-3 rounded-full bg-slate-300" />
        )}
      </div>

      {/* 섹션 정보 */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400 font-mono">{index + 1}</span>
          <span className={`text-sm font-medium truncate ${
            section.status === "done" ? "text-slate-800" : "text-slate-500"
          }`}>
            {section.title}
          </span>
        </div>
        {section.status === "done" && conf && (
          <div className="mt-0.5 flex items-center gap-2">
            <span className={`text-xs ${conf.text}`}>{conf.label}</span>
            {section.completion_score !== undefined && (
              <span className="text-xs text-slate-400">· {section.completion_score}%</span>
            )}
          </div>
        )}
        {section.status === "generating" && (
          <p className="mt-0.5 text-xs text-blue-500">생성 중...</p>
        )}
      </div>
    </div>
  );
}
