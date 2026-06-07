"use client";
import { useEffect, useState, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { useRecommendStore } from "@/store/recommendStore";
import ProgramCard from "@/components/program/ProgramCard";
import type { SupportProgramMatch } from "@/lib/types";

function RecommendPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const mode = searchParams.get("mode");
  const sessionParam = searchParams.get("session");

  const { programs: storedPrograms, profile, setSessionId } = useRecommendStore();
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // convert mode state
  const [convertPrograms, setConvertPrograms] = useState<SupportProgramMatch[]>([]);
  const [isLoadingPrograms, setIsLoadingPrograms] = useState(false);
  const [isConverting, setIsConverting] = useState(false);
  const [convertingName, setConvertingName] = useState("");
  const [convertDone, setConvertDone] = useState(0);
  const [convertTotal, setConvertTotal] = useState(0);

  const isConvertMode = mode === "convert";
  const [sessionId] = useState<string>(
    () =>
      sessionParam ??
      (typeof window !== "undefined" ? localStorage.getItem("bizplan_session_id") : "") ??
      ""
  );

  useEffect(() => {
    if (!isConvertMode && storedPrograms.length === 0) {
      router.replace("/");
    }
  }, [isConvertMode, storedPrograms, router]);

  useEffect(() => {
    if (!isConvertMode || !profile) return;
    setIsLoadingPrograms(true);
    api
      .recommend(profile)
      .then((res) => setConvertPrograms(res.programs.filter((p) => p.is_eligible && p.has_form)))
      .catch(() => setError("지원사업 추천을 불러올 수 없습니다."))
      .finally(() => setIsLoadingPrograms(false));
  }, [isConvertMode, profile]);

  async function handleWrite(programCode: string) {
    setIsStarting(true);
    setError(null);
    try {
      const { session_id } = await api.createSession(programCode);
      setSessionId(session_id);
      router.push(`/interview/${session_id}`);
    } catch {
      setError("세션을 생성할 수 없습니다. 다시 시도해주세요.");
      setIsStarting(false);
    }
  }

  async function handleConvert(programCode: string, programName: string) {
    if (!sessionId) {
      setError("세션 ID를 찾을 수 없습니다. 처음부터 다시 시도해주세요.");
      return;
    }
    setIsConverting(true);
    setConvertingName(programName);
    setConvertDone(0);
    setConvertTotal(0);

    let response: Response;
    try {
      response = await api.convertToForm(sessionId, programCode);
    } catch {
      setError("변환 요청에 실패했습니다. 다시 시도해주세요.");
      setIsConverting(false);
      return;
    }

    if (!response.ok) {
      setError("양식 변환에 실패했습니다.");
      setIsConverting(false);
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
          setConvertTotal((data.sections as { order: number }[]).length);
        } else if (eventType === "section_done") {
          setConvertDone((c) => c + 1);
        } else if (eventType === "all_done") {
          completed = true;
        } else if (eventType === "error") {
          setError(data.message ?? "변환 중 오류가 발생했습니다.");
          setIsConverting(false);
          completed = true;
        }
      } catch {
        /* skip malformed events */
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
      /* network error */
    }

    if (completed) {
      router.push(`/result/${sessionId}`);
    } else {
      setError("변환 중 오류가 발생했습니다. 다시 시도해주세요.");
      setIsConverting(false);
    }
  }

  const eligible = (isConvertMode ? convertPrograms : storedPrograms).filter((p) => p.is_eligible);

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 flex flex-col">
      {isConverting && (
        <div className="fixed inset-0 z-50 bg-white/90 backdrop-blur-sm flex flex-col items-center justify-center gap-4">
          <div className="w-10 h-10 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-slate-700 font-semibold text-lg">{convertingName} 양식으로 변환 중</p>
          {convertTotal > 0 && (
            <p className="text-slate-500 text-sm">
              {convertDone}/{convertTotal} 섹션 완료
            </p>
          )}
        </div>
      )}

      <header className="px-6 py-4 border-b border-slate-200 bg-white/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="max-w-3xl mx-auto flex items-center gap-3">
          <button
            onClick={() => router.back()}
            className="w-8 h-8 rounded-lg bg-slate-100 hover:bg-slate-200 flex items-center justify-center text-slate-600 transition-colors"
            aria-label="뒤로"
          >
            ←
          </button>
          <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center text-white font-bold text-sm">
            AI
          </div>
          <span className="font-semibold text-slate-800 text-lg">
            {isConvertMode ? "지원사업 선택" : "지원사업 추천 결과"}
          </span>
        </div>
      </header>

      <main className="flex-1 px-4 py-10">
        <div className="max-w-3xl mx-auto">
          {isConvertMode && (
            <p className="text-slate-500 text-sm mb-6">
              아래 양식 중 하나를 선택하면 기본 초안을 해당 양식으로 자동 변환합니다.
            </p>
          )}

          {profile && !isConvertMode && (
            <div className="mb-6 p-4 bg-white rounded-xl border border-slate-200 text-sm text-slate-600 flex flex-wrap gap-3">
              <span className="font-medium text-slate-800">입력 조건:</span>
              <span>{profile.업력}</span>
              <span>·</span>
              <span>{profile.지역}</span>
              <span>·</span>
              <span>{profile.청년 ? "청년 창업자" : "일반"}</span>
              {profile.아이템 && (
                <>
                  <span>·</span>
                  <span className="truncate max-w-xs">{profile.아이템}</span>
                </>
              )}
            </div>
          )}

          {error && (
            <div className="mb-6 p-4 rounded-xl bg-red-50 border border-red-200 text-red-700 text-sm">
              {error}
            </div>
          )}

          {isLoadingPrograms && (
            <div className="flex items-center justify-center py-20">
              <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
            </div>
          )}

          {!isConvertMode && eligible.length > 0 && (
            <section className="mb-8">
              <h2 className="text-lg font-bold text-slate-900 mb-4">
                지원 가능한 사업{" "}
                <span className="text-blue-600">{eligible.length}개</span>
              </h2>
              <div className="space-y-4">
                {eligible.map((p) => (
                  <ProgramCard
                    key={p.name}
                    variant="recommend"
                    program={p}
                    onWrite={handleWrite}
                    isStarting={isStarting}
                  />
                ))}
              </div>
            </section>
          )}

          {isConvertMode && !isLoadingPrograms && convertPrograms.length > 0 && (
            <div className="space-y-4">
              {convertPrograms.map((p) => (
                <div
                  key={p.program_code}
                  className="bg-white rounded-2xl border border-slate-200 p-5 flex flex-col gap-3 shadow-sm"
                >
                  <div>
                    <h3 className="font-semibold text-slate-900 text-base leading-snug">
                      {p.name}
                    </h3>
                    {p.설명 && (
                      <p className="text-sm text-slate-600 mt-1 leading-relaxed">{p.설명}</p>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {p.지역 && (
                      <span className="text-xs px-2 py-1 rounded-lg bg-slate-100 text-slate-600">
                        {p.지역}
                      </span>
                    )}
                    {p.최대지원금액_만원 > 0 && (
                      <span className="text-xs px-2 py-1 rounded-lg bg-slate-100 text-slate-600">
                        최대{" "}
                        {p.최대지원금액_만원 >= 10000
                          ? `${p.최대지원금액_만원 / 10000}억원`
                          : `${p.최대지원금액_만원.toLocaleString()}만원`}
                      </span>
                    )}
                  </div>
                  <button
                    onClick={() => handleConvert(p.program_code, p.name)}
                    disabled={isConverting}
                    className="w-full py-2.5 rounded-xl bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    이 양식으로 변환하기 →
                  </button>
                </div>
              ))}
            </div>
          )}

          {!isConvertMode && storedPrograms.length === 0 && !isLoadingPrograms && (
            <div className="text-center py-20 text-slate-400">추천 결과가 없습니다.</div>
          )}

          {isConvertMode && !isLoadingPrograms && convertPrograms.length === 0 && !error && (
            <div className="text-center py-20 text-slate-400">지원 가능한 양식이 없습니다.</div>
          )}
        </div>
      </main>

      <footer className="py-4 text-center text-xs text-slate-400 border-t border-slate-100">
        사업계획서 AI MVP
      </footer>
    </div>
  );
}

export default function RecommendPage() {
  return (
    <Suspense
      fallback={
        <div className="h-screen flex items-center justify-center">
          <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      }
    >
      <RecommendPageInner />
    </Suspense>
  );
}
