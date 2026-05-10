"use client";
import { useEffect, useRef, useState } from "react";

function getBadgeStyle(probPct: number) {
  if (probPct >= 70) return { bg: "bg-emerald-600", ring: "ring-emerald-300", label: "합격 유력" };
  if (probPct >= 50) return { bg: "bg-amber-500", ring: "ring-amber-300", label: "합격 가능" };
  if (probPct >= 30) return { bg: "bg-orange-500", ring: "ring-orange-300", label: "보완 필요" };
  return { bg: "bg-red-600", ring: "ring-red-300", label: "준비 부족" };
}

interface RubricBadgeProps {
  probPct: number;
}

export default function RubricBadge({ probPct }: RubricBadgeProps) {
  const [displayPct, setDisplayPct] = useState(0);
  const [isPulsing, setIsPulsing] = useState(false);
  const prevPct = useRef(0);

  useEffect(() => {
    const target = probPct;
    const start = prevPct.current;
    const diff = target - start;
    if (diff === 0) return;

    // 수치가 올라갈 때만 테두리 pulse
    let pulseTimer: ReturnType<typeof setTimeout> | null = null;
    if (diff > 0) {
      setIsPulsing(true);
      pulseTimer = setTimeout(() => setIsPulsing(false), 500);
    }

    const duration = 1000;
    const startTime = performance.now();
    let rafId: number;

    function tick(now: number) {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplayPct(Math.round(start + diff * eased));
      if (progress < 1) {
        rafId = requestAnimationFrame(tick);
      } else {
        prevPct.current = target;
      }
    }

    rafId = requestAnimationFrame(tick);

    return () => {
      if (pulseTimer) clearTimeout(pulseTimer);
      cancelAnimationFrame(rafId);
    };
  }, [probPct]);

  const style = getBadgeStyle(probPct);

  return (
    <div
      className={[
        "flex items-center gap-2 px-3 py-1.5 rounded-full text-white shadow-sm",
        style.bg,
        "transition-all duration-300",
        isPulsing
          ? `ring-4 ${style.ring} scale-105`
          : `ring-2 ${style.ring} scale-100`,
      ].join(" ")}
    >
      <span className="text-xl font-bold tabular-nums">서류합격 {displayPct}%</span>
      <span className="text-xs font-medium opacity-90">{style.label}</span>
    </div>
  );
}
