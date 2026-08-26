"use client";
import { useEffect, useRef, useState } from "react";
import type { BenchmarkResult } from "@/lib/types";

/**
 * 합격작 벤치마크 배지.
 * - empirical_rate: "이 점수대 문서의 N%가 서류합격" (실측 역사 통계 — 예측 주장 아님)
 * - distribution_position: "합격작 평균 X점" 대비 위치만 (확률 표기 금지)
 */
interface RubricBadgeProps {
  result: BenchmarkResult | null;
}

function useCountUp(target: number, duration = 900): number {
  const [value, setValue] = useState(0);
  const prev = useRef(0);
  useEffect(() => {
    const start = prev.current;
    const diff = target - start;
    if (diff === 0) return;
    const t0 = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const p = Math.min((now - t0) / duration, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      setValue(Math.round(start + diff * eased));
      if (p < 1) raf = requestAnimationFrame(tick);
      else prev.current = target;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);
  return value;
}

export default function RubricBadge({ result }: RubricBadgeProps) {
  const score = result?.available ? (result.score ?? 0) : 0;
  const shown = useCountUp(score);

  if (!result?.available) return null;

  const max = result.score_max ?? 16;
  const passMean = result.pass_mean ?? 0;
  const aboveMean = score >= passMean;
  const tone = aboveMean
    ? { bg: "bg-emerald-600", ring: "ring-emerald-200" }
    : { bg: "bg-amber-500", ring: "ring-amber-200" };

  const isEmpirical = result.display_mode === "empirical_rate" && result.empirical_pass_rate_pct != null;

  return (
    <div className={`flex items-center gap-3 rounded-2xl px-3 py-1.5 text-white shadow-sm ring-2 ${tone.bg} ${tone.ring}`}>
      <div className="flex items-baseline gap-1 leading-none">
        <span className="text-xl font-bold tabular-nums">{shown}</span>
        <span className="text-xs opacity-80">/ {max}</span>
      </div>
      <div className="h-6 w-px bg-white/30" />
      <div className="text-xs leading-tight">
        {isEmpirical ? (
          <>
            <div className="font-semibold">이 점수대 문서의 {result.empirical_pass_rate_pct}%가 서류합격</div>
            <div className="opacity-80">실제 {result.program} {result.n_docs}건 통계</div>
          </>
        ) : (
          <>
            <div className="font-semibold">
              합격작 평균 {passMean}점 · {aboveMean ? "평균 이상" : `${(passMean - score).toFixed(1)}점 부족`}
            </div>
            <div className="opacity-80">합격작 {result.n_pass_docs}건 체크리스트 기준</div>
          </>
        )}
      </div>
    </div>
  );
}
