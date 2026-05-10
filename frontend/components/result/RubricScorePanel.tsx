"use client";
import { useState } from "react";
import type { RubricScoreResult } from "@/lib/types";

interface Props {
  score: RubricScoreResult | null;
  isLoading: boolean;
}

export default function RubricScorePanel({ score, isLoading }: Props) {
  const [expanded, setExpanded] = useState(false);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 px-4 py-2 bg-slate-50 border-b border-slate-100 text-xs text-slate-400">
        <span className="w-3 h-3 border border-slate-300 border-t-transparent rounded-full animate-spin inline-block" />
        루브릭 채점 중 (Haiku)...
      </div>
    );
  }

  if (!score?.available) return null;

  const { prob_pct = 0, base_rate_pct = 0, hits = [] } = score;

  const colorCls =
    prob_pct >= 70
      ? { border: "border-green-200", bg: "bg-green-50" }
      : prob_pct >= 50
      ? { border: "border-amber-200", bg: "bg-amber-50" }
      : { border: "border-red-200", bg: "bg-red-50" };

  return (
    <div className={`border-b ${colorCls.border} ${colorCls.bg}`}>
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-3 px-4 py-2 hover:bg-white/50 transition-colors text-left"
      >
        <span className="text-xs text-slate-500 flex-1">
          루브릭 채점 {prob_pct}% · 모집단 평균 {base_rate_pct}%
        </span>
        <span className="text-slate-400 text-xs">{expanded ? "▲" : "▼"}</span>
      </button>

      {expanded && (
        <div className="px-4 pb-3">
          <p className="text-xs font-semibold text-green-700 mb-1.5">✅ 충족 신호</p>
          {hits.length === 0 ? (
            <p className="text-xs text-slate-400">없음</p>
          ) : (
            hits.map((h, i) => (
              <p key={i} className="text-xs text-green-700 leading-relaxed">
                · {h.feature}
              </p>
            ))
          )}
        </div>
      )}
    </div>
  );
}
