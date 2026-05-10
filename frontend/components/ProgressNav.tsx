"use client";
import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useRecommendStore } from "@/store/recommendStore";

const STEPS = [
  { id: 1, label: "기업정보 입력" },
  { id: 2, label: "지원사업 추천" },
  { id: 3, label: "인터뷰" },
  { id: 4, label: "섹션별 생성" },
  { id: 5, label: "사업계획서 초안" },
] as const;

function getStepFromPath(pathname: string): number {
  if (pathname.startsWith("/result")) return 5;
  if (pathname.startsWith("/generating")) return 4;
  if (pathname.startsWith("/interview")) return 3;
  if (pathname.startsWith("/recommend")) return 2;
  return 1;
}

function getSessionId(pathname: string): string | null {
  const m = pathname.match(/\/(?:interview|generating|result)\/([^/]+)/);
  return m ? m[1] : null;
}

function buildUrl(stepId: number, sessionId: string | null): string | null {
  if (stepId === 1) return "/";
  if (stepId === 2) return "/recommend";
  if (!sessionId) return null;
  if (stepId === 3) return `/interview/${sessionId}`;
  if (stepId === 4) return `/generating/${sessionId}`;
  if (stepId === 5) return `/result/${sessionId}`;
  return null;
}

function StepIcon({ status }: { status: "done" | "active" | "pending" }) {
  if (status === "done") {
    return (
      <span className="w-5 h-5 rounded-full bg-green-500 flex items-center justify-center flex-shrink-0">
        <svg width="10" height="8" viewBox="0 0 10 8" fill="none">
          <path
            d="M1 4L3.5 6.5L9 1"
            stroke="white"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
    );
  }
  if (status === "active") {
    return (
      <span className="w-5 h-5 rounded-full border-2 border-blue-500 bg-blue-50 flex items-center justify-center flex-shrink-0">
        <span className="w-2 h-2 rounded-full bg-blue-500" />
      </span>
    );
  }
  return (
    <span className="w-5 h-5 rounded-full border-2 border-gray-300 flex-shrink-0" />
  );
}

export default function ProgressNav() {
  const pathname = usePathname();
  const router = useRouter();
  const currentStep = getStepFromPath(pathname);
  const urlSessionId = getSessionId(pathname);
  const storeSessionId = useRecommendStore((s) => s.sessionId);
  const maxStep = useRecommendStore((s) => s.maxStep);
  const advanceStep = useRecommendStore((s) => s.advanceStep);

  const sessionId = urlSessionId ?? storeSessionId;

  useEffect(() => {
    advanceStep(currentStep);
  }, [currentStep, advanceStep]);

  return (
    <nav className="fixed left-0 top-0 w-[200px] h-screen bg-gray-50 border-r border-gray-200 z-40 flex flex-col py-6 px-3 overflow-y-auto">
      <div className="mb-5 px-2">
        <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-widest">
          진행 단계
        </span>
      </div>

      <ol className="flex flex-col gap-1">
        {STEPS.map((step) => {
          const status: "done" | "active" | "pending" =
            step.id === currentStep
              ? "active"
              : step.id <= maxStep
              ? "done"
              : "pending";

          const url = buildUrl(step.id, sessionId);
          const isClickable = status !== "pending" && !!url;

          return (
            <li key={step.id}>
              <button
                onClick={() => {
                  if (isClickable && url) router.push(url);
                }}
                disabled={!isClickable}
                className={`w-full text-left flex items-center gap-2.5 px-2 py-2.5 rounded-lg text-sm transition-colors ${
                  status === "active"
                    ? "bg-blue-50 text-blue-700 font-medium"
                    : status === "done"
                    ? "text-gray-700 hover:bg-gray-100 cursor-pointer"
                    : "text-gray-400 cursor-default"
                }`}
              >
                <StepIcon status={status} />
                <span className="leading-snug">{step.label}</span>
              </button>
            </li>
          );
        })}
      </ol>

      <div className="mt-auto pt-6 px-2">
        <div className="text-[10px] text-gray-300 leading-relaxed">
          완료된 단계는<br />클릭으로 이동 가능
        </div>
      </div>
    </nav>
  );
}
