"use client";
import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import SectionProgressItem from "@/components/generating/SectionProgress";
import type { SectionProgress } from "@/lib/types";

interface SectionMeta {
  id: string;
  title: string;
  order: number;
}

type Phase = "loading" | "selecting" | "generating" | "done" | "error" | "limit";

export default function GeneratingPage() {
  const params = useParams();
  const router = useRouter();
  const sessionId = params.sessionId as string;

  const [phase, setPhase] = useState<Phase>("loading");
  const [allSections, setAllSections] = useState<SectionMeta[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [sections, setSections] = useState<SectionProgress[]>([]);
  const [doneCount, setDoneCount] = useState(0);
  const [overallCompletion, setOverallCompletion] = useState(0);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [timeProgress, setTimeProgress] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const startedRef = useRef(false);

  // 섹션 목록 로드
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    fetch(`/api/sessions/${sessionId}/sections`)
      .then((r) => r.json())
      .then((data) => {
        const secs: SectionMeta[] = data.sections ?? [];
        setAllSections(secs);
        setSelectedIds(new Set(secs.map((s) => s.id)));
        localStorage.setItem("bizplan_session_id", sessionId);
        setPhase("selecting");
      })
      .catch(() => {
        setErrorMsg("섹션 정보를 불러올 수 없습니다.");
        setPhase("error");
      });
  }, [sessionId]);

  function toggleSection(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function selectAll() {
    setSelectedIds(new Set(allSections.map((s) => s.id)));
  }

  function deselectAll() {
    setSelectedIds(new Set());
  }

  async function startGeneration(sectionIds: string[] | null) {
    setPhase("generating");
    setDoneCount(0);
    setSections([]);

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 180_000);

    let response: Response;
    try {
      response = await fetch(`/api/sessions/${sessionId}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ section_ids: sectionIds }),
        signal: controller.signal,
      });
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        setErrorMsg("생성 시간이 초과됐습니다. 다시 시도해주세요.");
      } else {
        setErrorMsg("백엔드 서버에 연결할 수 없습니다.");
      }
      setPhase("error");
      return;
    } finally {
      clearTimeout(timeoutId);
    }

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
            data.sections.map((s: SectionMeta) => ({
              id: s.id,
              title: s.title,
              order: s.order,
              status: "generating" as const,
            }))
          );
        } else if (eventType === "section_done") {
          setSections((prev) =>
            prev.map((s) =>
              s.id === data.section_id
                ? { ...s, status: "done" as const, confidence_level: data.confidence_level, completion_score: data.completion_score }
                : s
            )
          );
          setDoneCount((c) => c + 1);
        } else if (eventType === "all_done") {
          setOverallCompletion(data.overall_completion);
          setPhase("done");
          completed = true;
        } else if (eventType === "error") {
          setErrorMsg(data.message ?? "알 수 없는 오류");
          setPhase("error");
          completed = true;
        } else if (eventType === "section_error") {
          console.warn("섹션 생성 실패:", data);
          // 전체 중단 아닌 부분 실패 — 계속 진행하되 콘솔 기록
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
      // 스트림 종료 후 버퍼에 남은 이벤트 처리 (마지막 \n\n 누락 방어)
      if (buffer.trim()) processChunk(buffer);
    } catch {
      // 서버 재시작 / 네트워크 오류 등으로 스트림이 끊긴 경우 — 결과가 있으면 이동
    }

    // all_done·error 없이 스트림 종료 (서버 오류·연결 끊김) → 에러 표시
    if (!completed) {
      setErrorMsg("일부 섹션 생성 중 오류가 발생했습니다. 다시 시도해주세요.");
      setPhase("error");
    }
  }

  // 생성 완료 → 결과 화면 이동
  useEffect(() => {
    if (phase === "done") {
      const t = setTimeout(() => router.push(`/result/${sessionId}`), 1800);
      return () => clearTimeout(t);
    }
  }, [phase, sessionId, router]);

  // generating 시작 시 시간 기반 easeOut 진행 바
  useEffect(() => {
    if (phase !== "generating") return;
    setTimeProgress(0);
    const startTime = Date.now();
    const duration = 90000;
    const interval = setInterval(() => {
      const elapsed = Date.now() - startTime;
      const t = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - t, 2);
      setTimeProgress(Math.min(eased * 99, 99));
    }, 100);
    return () => clearInterval(interval);
  }, [phase]);

  // generating 중 경과 시간 카운트
  useEffect(() => {
    if (phase !== "generating") return;
    setElapsed(0);
    const id = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [phase]);

  const total = sections.length > 0 ? sections.length : allSections.length;

  // sections에 없는 항목은 allSections 기반으로 "generating" 상태로 채움
  const displaySections: SectionProgress[] = sections.length > 0
    ? sections
    : allSections.map((s) => ({ ...s, status: "generating" as const }));

  // 섹션 완료 기준 진행률
  const sectionProgress = total > 0 ? (doneCount / total) * 100 : 0;

  // 표시용 진행률: 섹션 완료분과 시간 기반 easeOut 중 큰 값 (최대 99%, done 시 100%)
  const displayProgress = phase === "done" ? 100 : Math.max(sectionProgress, timeProgress);
  const pct = Math.round(displayProgress);

  // 현재 생성 중인 첫 번째 섹션 제목
  const generatingSectionTitle = sections.find((s) => s.status === "generating")?.title ?? null;

  // ── 섹션 선택 화면 ──────────────────────────────────────
  if (phase === "loading") {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (phase === "limit") {
    return (
      <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center px-4">
        <div className="w-full max-w-sm text-center">
          <div className="w-16 h-16 rounded-full bg-blue-100 flex items-center justify-center mx-auto mb-4 text-2xl">📄</div>
          <h1 className="text-xl font-bold text-slate-900 mb-2">이미 생성된 초안이 있습니다</h1>
          <p className="text-slate-500 text-sm mb-6">사업계획서 생성은 1회만 가능합니다. 기존 결과를 확인하세요.</p>
          <button
            onClick={() => router.push(`/result/${sessionId}`)}
            className="px-6 py-2.5 bg-blue-600 text-white text-sm font-semibold rounded-xl hover:bg-blue-700 transition-colors"
          >
            결과 보기
          </button>
        </div>
      </div>
    );
  }

  if (phase === "selecting") {
    const isAllSelected = selectedIds.size === allSections.length;
    return (
      <div className="min-h-screen bg-slate-50 flex flex-col items-center px-4 py-12">
        <div className="w-full max-w-lg">
          <div className="text-center mb-8">
            <h1 className="text-2xl font-bold text-slate-900 mb-2">생성할 섹션을 선택하세요</h1>
            <p className="text-slate-500 text-sm">선택한 섹션만 생성됩니다. 나머지는 기존 내용이 유지됩니다.</p>
          </div>

          {/* 전체 선택/해제 */}
          <div className="flex justify-end gap-2 mb-3">
            <button
              onClick={selectAll}
              className="text-xs text-blue-600 hover:underline"
            >
              전체 선택
            </button>
            <span className="text-slate-300">|</span>
            <button
              onClick={deselectAll}
              className="text-xs text-slate-400 hover:underline"
            >
              전체 해제
            </button>
          </div>

          {/* 섹션 체크박스 목록 */}
          <div className="bg-white rounded-2xl border border-slate-200 divide-y divide-slate-100 mb-6 shadow-sm">
            {allSections.map((s) => (
              <label
                key={s.id}
                className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-slate-50 transition-colors"
              >
                <input
                  type="checkbox"
                  checked={selectedIds.has(s.id)}
                  onChange={() => toggleSection(s.id)}
                  className="w-4 h-4 rounded accent-blue-600 flex-shrink-0"
                />
                <span className="text-xs text-slate-400 w-6 flex-shrink-0">{s.order}</span>
                <span className="text-sm text-slate-700 font-medium">{s.title}</span>
              </label>
            ))}
          </div>

          {/* 생성 버튼 */}
          <div className="flex gap-3">
            <button
              onClick={() => startGeneration(null)}
              className="flex-1 py-3 rounded-xl bg-blue-600 text-white font-semibold text-sm hover:bg-blue-700 transition-colors shadow-sm"
            >
              전체 생성 ({allSections.length}개)
            </button>
            <button
              onClick={() => {
                if (selectedIds.size === 0) return;
                const isAll = selectedIds.size === allSections.length;
                startGeneration(isAll ? null : [...selectedIds]);
              }}
              disabled={selectedIds.size === 0 || isAllSelected}
              className="flex-1 py-3 rounded-xl border-2 border-blue-600 text-blue-600 font-semibold text-sm hover:bg-blue-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              선택 생성 ({selectedIds.size}개)
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── 생성 진행 / 완료 / 오류 화면 ────────────────────────
  return (
    <div className="min-h-screen bg-slate-50 flex flex-col items-center justify-center px-4 py-12">
      <div className="w-full max-w-lg">
        <div className="text-center mb-8">
          {phase === "done" ? (
            <>
              <div className="w-16 h-16 rounded-full bg-emerald-100 flex items-center justify-center mx-auto mb-4">
                <svg className="w-8 h-8 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <h1 className="text-2xl font-bold text-slate-900 mb-1">초안 생성 완료!</h1>
              <div className="mt-2 text-center">
                <p className="text-slate-400 text-sm">결과 화면으로 이동합니다...</p>
                <p className="text-slate-400 text-xs mt-1">결과 페이지에서 합격률을 확인하세요</p>
              </div>
            </>
          ) : phase === "error" ? (
            <>
              <div className="w-16 h-16 rounded-full bg-red-100 flex items-center justify-center mx-auto mb-4 text-3xl">❌</div>
              <h1 className="text-xl font-bold text-slate-900 mb-2">오류가 발생했습니다</h1>
              <p className="text-red-600 text-sm mb-4">{errorMsg}</p>
              <button onClick={() => router.back()} className="px-4 py-2 bg-slate-600 text-white rounded-lg text-sm hover:bg-slate-700">
                돌아가기
              </button>
            </>
          ) : (
            <>
              <div className="w-16 h-16 rounded-full bg-blue-100 flex items-center justify-center mx-auto mb-4">
                <div className="w-8 h-8 border-3 border-blue-500 border-t-transparent rounded-full animate-spin" />
              </div>
              <h1 className="text-2xl font-bold text-slate-900 mb-1">사업계획서 초안 생성 중</h1>
              <p className="text-slate-500 text-sm">
                {total > 0 ? `${doneCount}/${total} 섹션 완료 (${pct}%)` : "섹션 로딩 중..."}
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
                style={{ width: `${displayProgress}%`, transition: "width 0.3s ease-in-out" }}
              />
            </div>
          </div>
        )}

        {displaySections.length > 0 && (
          <div className="space-y-2">
            {displaySections.map((section, i) => (
              <SectionProgressItem key={section.id} section={section} index={i} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
