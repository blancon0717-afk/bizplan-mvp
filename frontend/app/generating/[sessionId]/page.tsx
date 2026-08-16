"use client";
import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import SectionProgressItem from "@/components/generating/SectionProgress";
import type { SectionProgress } from "@/lib/types";
import { api } from "@/lib/api";

interface SectionMeta {
  id: string;
  title: string;
  order: number;
}

type Phase = "generating" | "done" | "error" | "limit";

export default function GeneratingPage() {
  const params = useParams();
  const router = useRouter();
  const sessionId = params.sessionId as string;

  const [phase, setPhase] = useState<Phase>("generating");
  const [sections, setSections] = useState<SectionProgress[]>([]);
  const [doneCount, setDoneCount] = useState(0);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [timeProgress, setTimeProgress] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const startedRef = useRef(false);
  // 자동 재시도로 같은 섹션이 두 번 done 될 수 있어, 최초 완료만 카운트하기 위한 집합
  const doneIdsRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    localStorage.setItem("bizplan_session_id", sessionId);

    (async () => {
      const controller = new AbortController();
      // 백엔드 feedback_agent 검수 게이트 추가로 240→360초 상향
      const timeoutId = setTimeout(() => controller.abort(), 360_000);

      let response: Response;
      try {
        response = await api.generateFramework(sessionId);
      } catch (e) {
        clearTimeout(timeoutId);
        if (e instanceof DOMException && e.name === "AbortError") {
          setErrorMsg("생성 시간이 초과됐습니다. 다시 시도해주세요.");
        } else {
          setErrorMsg("백엔드 서버에 연결할 수 없습니다.");
        }
        setPhase("error");
        return;
      }
      clearTimeout(timeoutId);

      if (response.status === 429) {
        setPhase("limit");
        return;
      }
      if (!response.ok) {
        setErrorMsg("초안 생성 요청이 실패했습니다.");
        setPhase("error");
        return;
      }

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let completed = false;

      function processChunk(chunk: string) {
        const lines = chunk.split("\n");
        const eventType = lines.find((l) => l.startsWith("event:"))?.slice(6).trim();
        const dataLine = lines.find((l) => l.startsWith("data:"))?.slice(5).trim();
        if (!eventType || !dataLine) return;

        try {
          const data = JSON.parse(dataLine);
          if (eventType === "init") {
            setSections(
              data.sections.map((s: SectionMeta, i: number) => ({
                id: s.id,
                title: s.title,
                order: i,
                status: "generating" as const,
              }))
            );
          } else if (eventType === "section_retrying") {
            // 빈 섹션 자동 재시도 — 완료 표시됐던 섹션이 다시 생성 중으로 돌아간다
            setSections((prev) =>
              prev.map((s) =>
                s.id === data.section_id ? { ...s, status: "generating" as const } : s
              )
            );
          } else if (eventType === "section_done") {
            setSections((prev) =>
              prev.map((s) =>
                s.id === data.section_id
                  ? {
                      ...s,
                      status: "done" as const,
                      confidence_level: data.confidence_level,
                      completion_score: data.completion_score,
                    }
                  : s
              )
            );
            // 재시도로 같은 섹션이 다시 done 되어도 진행도가 총계를 넘지 않도록 최초 1회만 증가
            if (!doneIdsRef.current.has(data.section_id)) {
              doneIdsRef.current.add(data.section_id);
              setDoneCount((c) => c + 1);
            }
          } else if (eventType === "all_done") {
            setPhase("done");
            completed = true;
          } else if (eventType === "error") {
            setErrorMsg(data.message ?? "알 수 없는 오류");
            setPhase("error");
            completed = true;
          }
        } catch {
          // skip malformed events
        }
      }

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const chunks = buffer.split("\n\n");
          buffer = chunks.pop() ?? "";
          for (const chunk of chunks) processChunk(chunk);
        }
        if (buffer.trim()) processChunk(buffer);
      } catch {
        // network error or stream close
      }

      if (!completed) {
        setErrorMsg("초안 생성 중 오류가 발생했습니다. 다시 시도해주세요.");
        setPhase("error");
      }
    })();
  }, [sessionId]);

  useEffect(() => {
    if (phase === "done") {
      const t = setTimeout(() => router.push(`/draft/${sessionId}`), 1800);
      return () => clearTimeout(t);
    }
  }, [phase, sessionId, router]);

  useEffect(() => {
    if (phase !== "generating") return;
    setTimeProgress(0);
    const startTime = Date.now();
    const duration = 120_000;
    const interval = setInterval(() => {
      const e = Date.now() - startTime;
      const t = Math.min(e / duration, 1);
      const eased = 1 - Math.pow(1 - t, 2);
      setTimeProgress(Math.min(eased * 99, 99));
    }, 100);
    return () => clearInterval(interval);
  }, [phase]);

  useEffect(() => {
    if (phase !== "generating") return;
    setElapsed(0);
    const id = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [phase]);

  const total = sections.length;
  const sectionProgress = total > 0 ? (doneCount / total) * 100 : 0;
  const displayProgress = phase === "done" ? 100 : Math.max(sectionProgress, timeProgress);
  const generatingSectionTitle = sections.find((s) => s.status === "generating")?.title ?? null;

  if (phase === "limit") {
    return (
      <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center px-4">
        <div className="w-full max-w-sm text-center">
          <div className="w-16 h-16 rounded-full bg-blue-100 flex items-center justify-center mx-auto mb-4 text-2xl">
            📄
          </div>
          <h1 className="text-xl font-bold text-slate-900 mb-2">이미 생성된 초안이 있습니다</h1>
          <p className="text-slate-500 text-sm mb-6">
            사업계획서 초안은 1회만 생성 가능합니다. 기존 결과를 확인하세요.
          </p>
          <button
            onClick={() => router.push(`/draft/${sessionId}`)}
            className="px-6 py-2.5 bg-blue-600 text-white text-sm font-semibold rounded-xl hover:bg-blue-700 transition-colors"
          >
            초안 보기
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center px-4 py-12">
      <div className="w-full max-w-lg">
        <div className="text-center mb-8">
          {phase === "done" ? (
            <>
              <div className="w-16 h-16 rounded-full bg-emerald-100 flex items-center justify-center mx-auto mb-4">
                <svg
                  className="w-8 h-8 text-emerald-600"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M5 13l4 4L19 7"
                  />
                </svg>
              </div>
              <h1 className="text-2xl font-bold text-slate-900 mb-1">기본 초안 생성 완료!</h1>
              <p className="text-slate-400 text-sm mt-2">초안 검토 화면으로 이동합니다...</p>
            </>
          ) : phase === "error" ? (
            <>
              <div className="w-16 h-16 rounded-full bg-red-100 flex items-center justify-center mx-auto mb-4 text-3xl">
                ❌
              </div>
              <h1 className="text-xl font-bold text-slate-900 mb-2">오류가 발생했습니다</h1>
              <p className="text-red-600 text-sm mb-4">{errorMsg}</p>
              <button
                onClick={() => router.back()}
                className="px-4 py-2 bg-slate-600 text-white rounded-lg text-sm hover:bg-slate-700"
              >
                돌아가기
              </button>
            </>
          ) : (
            <>
              <div className="w-16 h-16 rounded-full bg-blue-100 flex items-center justify-center mx-auto mb-4">
                <div className="w-8 h-8 border-3 border-blue-500 border-t-transparent rounded-full animate-spin" />
              </div>
              <h1 className="text-2xl font-bold text-slate-900 mb-1">
                사업계획서 기본 초안 생성 중
              </h1>
              <p className="text-slate-500 text-sm">
                {total > 0
                  ? `${doneCount}/${total} 섹션 완료 (${Math.round(displayProgress)}%)`
                  : "초안 준비 중..."}
              </p>
              {generatingSectionTitle && (
                <p className="text-blue-500 text-xs mt-1 animate-pulse">
                  {generatingSectionTitle} 생성 중...
                </p>
              )}
              <p className="text-slate-400 text-xs mt-1">{elapsed}초 경과</p>
            </>
          )}
        </div>

        {total > 0 && (phase === "generating" || phase === "done") && (
          <div className="mb-6">
            <div className="w-full h-2 bg-slate-200 rounded-full overflow-hidden">
              <div
                className="h-full bg-blue-500 rounded-full"
                style={{
                  width: `${displayProgress}%`,
                  transition: "width 0.3s ease-in-out",
                }}
              />
            </div>
          </div>
        )}

        {sections.length > 0 && (
          <div className="space-y-2">
            {sections.map((section, i) => (
              <SectionProgressItem key={section.id} section={section} index={i} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
