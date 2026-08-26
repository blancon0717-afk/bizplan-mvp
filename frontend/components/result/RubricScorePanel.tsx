"use client";
import { useState } from "react";
import type { BenchmarkItem, BenchmarkResult } from "@/lib/types";

/**
 * 합격작 항목 벤치마크 패널 — "합격작 68%가 언급 · 이 문서엔 없음".
 * 실제 서류심사 통계(benchmark_v1)의 보유율 비교. 합격을 보장하는 예측이 아니다.
 */
interface Props {
  result: BenchmarkResult | null;
  isLoading: boolean;
}

function ItemRow({ item }: { item: BenchmarkItem }) {
  return (
    <li className="flex items-center gap-3 py-1.5 text-sm">
      <span
        className={`w-5 h-5 rounded-full flex items-center justify-center text-[11px] font-bold flex-shrink-0 ${
          item.present ? "bg-emerald-100 text-emerald-700" : "bg-rose-100 text-rose-600"
        }`}
        aria-label={item.present ? "이 문서에 있음" : "이 문서에 없음"}
      >
        {item.present ? "✓" : "✗"}
      </span>
      <span className={`flex-1 truncate ${item.present ? "text-slate-600" : "text-slate-800 font-medium"}`}>
        {item.label}
      </span>
      <span className="text-xs text-slate-500 tabular-nums whitespace-nowrap">
        합격작 <b className="text-slate-700">{item.pass_pct}%</b> · 불합격작 {item.fail_pct}%
      </span>
    </li>
  );
}

export default function RubricScorePanel({ result, isLoading }: Props) {
  const [expanded, setExpanded] = useState(true);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 px-4 py-2 bg-slate-50 border-b border-slate-100 text-xs text-slate-400">
        <span className="w-3 h-3 border border-slate-300 border-t-transparent rounded-full animate-spin inline-block" />
        합격작 벤치마크 대조 중...
      </div>
    );
  }
  if (!result?.available) return null;

  const gaps = result.gaps ?? [];
  const strengths = result.strengths ?? [];
  if (gaps.length === 0 && strengths.length === 0) return null;

  return (
    <div className="border-b border-slate-200 bg-slate-50/70">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-3 px-4 py-2 hover:bg-white/60 transition-colors text-left"
      >
        <span className="text-xs font-semibold tracking-wide text-slate-500 uppercase">합격작 벤치마크</span>
        <span className="text-sm text-slate-700">
          {gaps.length > 0
            ? <>합격작이 갖춘 항목 중 <b className="text-rose-600">{gaps.length}개</b>가 이 문서에 없습니다</>
            : <>합격작이 갖춘 핵심 항목을 모두 갖췄습니다</>}
        </span>
        <span className="ml-auto text-xs text-slate-400">
          {result.program} 서류심사 {result.n_docs}건 통계 · {expanded ? "접기 ▲" : "펼치기 ▼"}
        </span>
      </button>

      {expanded && (
        <div className="px-4 pb-3 grid gap-4 md:grid-cols-2">
          <div>
            <div className="text-[11px] font-semibold text-rose-600 mb-1">보완하면 좋은 항목 (합격작 보유율 순)</div>
            <ul className="divide-y divide-slate-100">
              {gaps.map((g) => <ItemRow key={g.feature} item={g} />)}
              {gaps.length === 0 && <li className="text-xs text-slate-400 py-1">없음</li>}
            </ul>
          </div>
          <div>
            <div className="text-[11px] font-semibold text-emerald-700 mb-1">이미 갖춘 항목</div>
            <ul className="divide-y divide-slate-100">
              {strengths.map((s) => <ItemRow key={s.feature} item={s} />)}
              {strengths.length === 0 && <li className="text-xs text-slate-400 py-1">없음</li>}
            </ul>
          </div>
          <p className="md:col-span-2 text-[11px] text-slate-400">
            * 실제 합격·불합격 사업계획서의 항목 언급 비율 비교입니다. 합격을 보장하지 않으며, 없는 사실을 지어 넣지 마세요 —
            보유한 항목만 답변에 반영하면 초안에 자동으로 들어갑니다.
          </p>
        </div>
      )}
    </div>
  );
}
