"use client";
import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import RubricBadge from "@/components/result/RubricBadge";
import DocumentPanel, { type DocumentPanelHandle } from "@/components/result/DocumentPanel";
import MemoPanel, { type MemoPanelHandle } from "@/components/result/MemoPanel";
import RubricScorePanel from "@/components/result/RubricScorePanel";
import { useResultStore } from "@/store/resultStore";
import { api } from "@/lib/api";
import type { RubricScoreResult } from "@/lib/types";

export default function ResultPage() {
  const params = useParams();
  const router = useRouter();
  const sessionId = params.sessionId as string;

  const { sections, overallCompletion, localProbPct, activeSectionId, isRegenerating,
    init, setActiveSectionId, updateMemoResponse, updateSectionSuggestions, regenerateSection, editSection, syncProbPct } = useResultStore();

  const documentPanelRef = useRef<DocumentPanelHandle>(null);
  const memoPanelRef = useRef<MemoPanelHandle>(null);

  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isDownloading, setIsDownloading] = useState(false);
  const [programName, setProgramName] = useState("");
  const [rubricScore, setRubricScore] = useState<RubricScoreResult | null>(null);
  const [isLoadingScore, setIsLoadingScore] = useState(false);
  const [editingSectionId, setEditingSectionId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState("");
  const [isRegenAll, setIsRegenAll] = useState(false);
  const [regenError, setRegenError] = useState<string | null>(null);
  const [showAnchors, setShowAnchors] = useState(false);
  const [isFeedbackRunning, setIsFeedbackRunning] = useState(false);
  const [feedbackDoneCount, setFeedbackDoneCount] = useState(0);
  const [feedbackTotal, setFeedbackTotal] = useState(0);
  const [actionPlan, setActionPlan] = useState<string | null>(null);
  const [showActionPlan, setShowActionPlan] = useState(false);
  const [isActionPlanLoading, setIsActionPlanLoading] = useState(false);
  const [documentCheck, setDocumentCheck] = useState<string | null>(null);
  const [isDocumentChecking, setIsDocumentChecking] = useState(false);
  const [usageData, setUsageData] = useState<Record<string, { used: number; max: number }>>({});
  const [passedMemoMap, setPassedMemoMap] = useState<Record<string, Set<number>>>({});

  useEffect(() => {
    async function load() {
      try {
        const [results, session] = await Promise.all([
          api.getResults(sessionId),
          api.getSession(sessionId),
        ]);
        init(sessionId, results.sections, results.overall_completion);
        localStorage.setItem("bizplan_session_id", sessionId);
        const programs = await api.getPrograms();
        const prog = programs.programs.find((p) => p.code === session.program_code);
        setProgramName(prog?.name ?? session.program_code);

        // 루브릭 채점 — 결과 로드 후 비동기로 실행
        setIsLoadingScore(true);
        api.getScore(sessionId)
          .then((score) => {
            setRubricScore(score);
            if (score?.prob_pct != null) syncProbPct(score.prob_pct);
          })
          .catch(() => {})
          .finally(() => setIsLoadingScore(false));
        api.getUsage(sessionId).then(setUsageData).catch(() => {});
      } catch {
        const savedId = localStorage.getItem("bizplan_session_id");
        if (savedId && savedId !== sessionId) {
          router.replace(`/result/${savedId}`);
        } else {
          setError("결과를 불러올 수 없습니다.");
        }
      } finally {
        setIsLoading(false);
      }
    }
    load();
  }, [sessionId]);

  function handleAnchorClick(sectionId: string, memoIndex: number) {
    setActiveSectionId(sectionId);
    setTimeout(() => {
      memoPanelRef.current?.scrollToMemo(memoIndex);
    }, 50);
  }

  function handleMemoTitleClick(originalIndex: number) {
    if (activeSectionId) {
      documentPanelRef.current?.scrollToAnchor(activeSectionId, originalIndex);
    }
  }

  function handleStartEdit(sectionId: string, content: string) {
    setEditingSectionId(sectionId);
    setEditContent(content);
    setActiveSectionId(sectionId);
  }

  async function handleSaveEdit() {
    if (!editingSectionId) return;
    await editSection(sessionId, editingSectionId, editContent);
    setEditingSectionId(null);
    setEditContent("");
    api.getUsage(sessionId).then(setUsageData).catch(() => {});
  }

  function handleCancelEdit() {
    setEditingSectionId(null);
    setEditContent("");
  }

  async function handleSectionFeedback(sectionId: string) {
    setIsFeedbackRunning(true);
    try {
      const res = await fetch(`/api/sessions/${sessionId}/feedback/${sectionId}`, { method: "POST" });
      if (!res.ok) return;
      const data = await res.json();
      updateSectionSuggestions(data.section_id, data.inline_suggestions);
      setShowAnchors(true);
    } finally {
      setIsFeedbackRunning(false);
    }
  }

  async function handleRegenerate(sectionId: string) {
    try {
      await regenerateSection(sessionId, sectionId);
    } catch {
      setRegenError("재생성 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.");
    }
    api.getUsage(sessionId).then(setUsageData).catch(() => {});
  }

  async function handleMemoRegenerate(sectionId: string, memoIndex: number, memoResponse: string) {
    try {
      await regenerateSection(sessionId, sectionId, memoResponse, memoIndex);
      await handleSectionFeedback(sectionId);
    } catch {
      setRegenError("재생성 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.");
    }
    api.getUsage(sessionId).then(setUsageData).catch(() => {});
  }

  async function handleRegenerateAll() {
    setIsRegenAll(true);
    try {
      const res = await fetch(`/api/sessions/${sessionId}/results/regenerate-all`, { method: "POST" });
      if (res.status === 429) {
        setRegenError("전체 고도화는 1회만 가능합니다.");
        return;
      }
      if (!res.ok) {
        setRegenError("전체 고도화 중 오류가 발생했습니다.");
        return;
      }
      const data = await res.json();
      for (const section of (data.sections ?? [])) {
        updateSectionAfterRegen(section, data.overall_completion);
      }
      api.getUsage(sessionId).then(setUsageData).catch(() => {});
    } catch {
      setRegenError("전체 고도화 중 오류가 발생했습니다.");
    } finally {
      setIsRegenAll(false);
    }
  }

  async function handleFeedback() {
    setShowAnchors(true);
    setIsFeedbackRunning(true);
    setFeedbackDoneCount(0);
    setFeedbackTotal(0);
    try {
      const response = await fetch(`/api/sessions/${sessionId}/feedback`, { method: "POST" });
      if (!response.ok) return;
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() ?? "";
        for (const chunk of chunks) {
          const lines = chunk.split("\n");
          const eventType = lines.find((l) => l.startsWith("event:"))?.slice(6).trim();
          const dataLine = lines.find((l) => l.startsWith("data:"))?.slice(5).trim();
          if (!eventType || !dataLine) continue;
          try {
            const data = JSON.parse(dataLine);
            if (eventType === "init") {
              setFeedbackTotal(data.total);
            } else if (eventType === "section_feedback_done") {
              setFeedbackDoneCount((c) => c + 1);
              updateSectionSuggestions(data.section_id, data.inline_suggestions);
            } else if (eventType === "all_done") {
              // 전략 피드백 반영 — 재조회 없이 페이로드로 처리
              for (const s of (data.sections ?? [])) {
                updateSectionSuggestions(s.section_id, s.inline_suggestions);
              }
            }
          } catch { /* skip */ }
        }
      }
    } finally {
      setIsFeedbackRunning(false);
      api.getUsage(sessionId).then(setUsageData).catch(() => {});
    }
  }

  async function handleActionPlan() {
    if (actionPlan) { setShowActionPlan(true); return; }
    setIsActionPlanLoading(true);
    try {
      const result = await api.getActionPlan(sessionId);
      setActionPlan(result.action_plan);
      setShowActionPlan(true);
    } catch {
      setRegenError("액션플랜 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.");
    } finally {
      setIsActionPlanLoading(false);
      api.getUsage(sessionId).then(setUsageData).catch(() => {});
    }
  }

  async function handleDocumentCheck() {
    setIsDocumentChecking(true);
    try {
      const { result } = await api.getDocumentCheck(sessionId);
      setDocumentCheck(result);
    } finally {
      setIsDocumentChecking(false);
    }
  }

  async function handleDownload() {
    setIsDownloading(true);
    try {
      const res = await fetch(`/api/sessions/${sessionId}/export/docx?business_name=(미지정)`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `사업계획서_${sessionId}.docx`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setIsDownloading(false);
    }
  }

  const greenCount = sections.filter((s) => s.confidence_level === "green").length;
  const yellowCount = sections.filter((s) => s.confidence_level === "yellow").length;
  const redCount = sections.filter((s) => s.confidence_level === "red").length;
  const totalMemos = sections.reduce(
    (sum, s) => sum + s.inline_suggestions.filter((m) => m.severity !== "critical").length,
    0
  );
  const resolvedMemos = sections.reduce((sum, s) => sum + (s.resolved_memo_count ?? 0), 0);

  if (isLoading) {
    return (
      <div className="h-screen flex items-center justify-center bg-slate-50">
        <div className="w-8 h-8 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-screen flex flex-col items-center justify-center bg-slate-50 gap-4">
        <p className="text-red-600">{error}</p>
        <button onClick={() => router.back()} className="px-4 py-2 bg-slate-600 text-white rounded-lg text-sm">
          돌아가기
        </button>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col bg-white overflow-hidden">
      {/* 헤더 */}
      <header className="flex-shrink-0 border-b border-slate-200 bg-white px-4 py-3">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <button
              onClick={() => router.push("/")}
              className="text-slate-400 hover:text-slate-600 p-1 flex-shrink-0"
            >
              ←
            </button>
            <div className="min-w-0">
              <h1 className="font-semibold text-slate-800 text-sm truncate">{programName}</h1>
            </div>
          </div>

          <div className="flex items-center gap-3 flex-shrink-0">
            {isLoadingScore
              ? <span className="text-sm text-slate-400 animate-pulse">합격률 계산 중...</span>
              : <RubricBadge probPct={localProbPct} />
            }
            {(yellowCount > 0 || redCount > 0) && (
              <div className="relative group">
                <button
                  onClick={handleRegenerateAll}
                  disabled={isRegenAll}
                  title="보완 필요/미흡 섹션을 일괄 재생성합니다"
                  className="flex items-center gap-2 px-3 py-2 rounded-xl border border-slate-200 text-slate-600 text-sm font-medium hover:bg-slate-50 disabled:opacity-50 transition-colors"
                >
                  {isRegenAll ? (
                    <span className="w-4 h-4 border-2 border-slate-400 border-t-transparent rounded-full animate-spin" />
                  ) : "↺"}
                  전체 고도화 ({yellowCount + redCount}개 섹션)
                  <span className={(usageData.regenerate_all?.used ?? 0) >= (usageData.regenerate_all?.max ?? 1) ? "text-xs text-gray-400 ml-1" : "text-xs text-blue-500 ml-1"}>
                    ({usageData.regenerate_all?.used ?? 0}/{usageData.regenerate_all?.max ?? 1})
                  </span>
                </button>
                <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:block z-50">
                  <div className="bg-slate-800 text-white text-xs rounded-lg px-3 py-2 whitespace-nowrap shadow-lg">
                    보완 필요/미흡 섹션을 일괄 재생성합니다 (1회 한정)
                  </div>
                </div>
              </div>
            )}
            <div className="relative group">
              <button
                onClick={handleFeedback}
                disabled={isFeedbackRunning}
                title="섹션별 약점을 분석하고 사업계획서 고도화를 위한 구체적인 피드백을 제공합니다"
                className="flex items-center gap-2 px-3 py-2 rounded-xl border border-blue-200 text-blue-600 text-sm font-medium hover:bg-blue-50 disabled:opacity-50 transition-colors"
              >
                {isFeedbackRunning ? (
                  <>
                    <span className="w-4 h-4 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
                    피드백 생성 중 {feedbackDoneCount > 0 && feedbackTotal > 0 ? `(${feedbackDoneCount}/${feedbackTotal})` : ""}
                  </>
                ) : (
                  <>피드백 확인하기 <span className={(usageData.feedback?.used ?? 0) >= (usageData.feedback?.max ?? 1) ? "text-xs text-gray-400" : "text-xs text-blue-500"}>({usageData.feedback?.used ?? 0}/{usageData.feedback?.max ?? 1})</span></>
                )}
              </button>
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:block z-50">
                <div className="bg-slate-800 text-white text-xs rounded-lg px-3 py-2 whitespace-nowrap shadow-lg">
                  섹션별 약점을 분석하고 보완이 필요한 항목을 제시합니다
                </div>
              </div>
            </div>
            <div className="relative group">
              <button
                onClick={handleActionPlan}
                disabled={isActionPlanLoading}
                title="사업계획서 합격을 위해 대표님이 직접 실행해야 할 항목을 제시합니다"
                className="flex items-center gap-2 px-3 py-2 rounded-xl border border-purple-200 text-purple-600 text-sm font-medium hover:bg-purple-50 disabled:opacity-50 transition-colors"
              >
                {isActionPlanLoading ? (
                  <span className="w-4 h-4 border-2 border-purple-400 border-t-transparent rounded-full animate-spin" />
                ) : (
                  <>📋 액션플랜 <span className={(usageData.action_plan?.used ?? 0) >= (usageData.action_plan?.max ?? 1) ? "text-xs text-gray-400" : "text-xs text-blue-500"}>({usageData.action_plan?.used ?? 0}/{usageData.action_plan?.max ?? 1})</span></>
                )}
              </button>
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:block z-50">
                <div className="bg-slate-800 text-white text-xs rounded-lg px-3 py-2 whitespace-nowrap shadow-lg">
                  합격을 위해 대표님이 직접 실행해야 할 항목을 제시합니다
                </div>
              </div>
            </div>
            <div className="relative group">
              <button
                onClick={handleDocumentCheck}
                disabled={isDocumentChecking}
                title="오탈자, 문장 오류, 논리적 모순을 자동으로 점검합니다"
                className="flex items-center gap-2 px-3 py-2 rounded-xl border border-teal-200 text-teal-600 text-sm font-medium hover:bg-teal-50 disabled:opacity-50 transition-colors"
              >
                {isDocumentChecking ? (
                  <span className="w-4 h-4 border-2 border-teal-400 border-t-transparent rounded-full animate-spin" />
                ) : (
                  "🔍 문서 점검"
                )}
              </button>
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:block z-50">
                <div className="bg-slate-800 text-white text-xs rounded-lg px-3 py-2 whitespace-nowrap shadow-lg">
                  오탈자, 문장 오류, 논리적 모순을 자동으로 점검합니다
                </div>
              </div>
            </div>
            <button
              onClick={handleDownload}
              disabled={isDownloading}
              className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-800 text-white text-sm font-medium hover:bg-slate-900 disabled:opacity-50 transition-colors shadow-sm"
            >
              {isDownloading ? (
                <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
              ) : (
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
                  />
                </svg>
              )}
              DOCX 다운로드
            </button>
          </div>
        </div>
      </header>

      {/* 루브릭 채점 패널 */}
      <RubricScorePanel score={rubricScore} isLoading={isLoadingScore} />

      {/* 재생성 에러 배너 */}
      {regenError && (
        <div className="flex-shrink-0 bg-red-50 border-b border-red-200 px-4 py-2 flex items-center justify-between">
          <span className="text-sm text-red-700">{regenError}</span>
          <button
            onClick={() => setRegenError(null)}
            className="text-red-400 hover:text-red-600 text-xs ml-4 flex-shrink-0"
          >
            ✕
          </button>
        </div>
      )}

      {/* 본문: 좌우 분할 */}
      <div className="flex-1 flex overflow-hidden">
        {/* 좌: 문서 패널 (60%) */}
        <div className="w-3/5 border-r border-slate-200 flex flex-col overflow-hidden">
          <div className="flex-shrink-0 px-4 py-1.5 border-b border-slate-100 bg-white">
            <div className="flex items-center gap-4 text-xs text-slate-400">
              <span>🟢 완성 {greenCount}</span>
              <span>🟡 보완 필요 {yellowCount}</span>
              <span>🔴 미흡 {redCount}</span>
              {showAnchors && <span>📝 메모 {resolvedMemos}/{totalMemos}</span>}
            </div>
          </div>
          <DocumentPanel
            ref={documentPanelRef}
            sections={sections}
            activeSectionId={activeSectionId}
            editingSectionId={editingSectionId}
            editContent={editContent}
            showAnchors={showAnchors}
            onSectionClick={setActiveSectionId}
            onAnchorClick={handleAnchorClick}
            onRegenerate={handleRegenerate}
            isRegenerating={isRegenerating}
            usageData={usageData}
            onStartEdit={handleStartEdit}
            onEditContentChange={setEditContent}
            onSaveEdit={handleSaveEdit}
            onCancelEdit={handleCancelEdit}
            passedMemoMap={passedMemoMap}
          />
        </div>

        {/* 우: 메모 패널 (40%) */}
        <div className="w-2/5 flex flex-col overflow-hidden bg-slate-50">
          <MemoPanel
            ref={memoPanelRef}
            sections={sections}
            activeSectionId={activeSectionId}
            showAnchors={showAnchors}
            onMemoChange={(sectionId, memoIndex, response) => updateMemoResponse(sessionId, sectionId, memoIndex, response)}
            onRegenerate={handleMemoRegenerate}
            onMemoTitleClick={handleMemoTitleClick}
            isRegenerating={isRegenerating}
            usageData={usageData}
            onPassMemo={(sectionId, memoIndex) => {
              setPassedMemoMap(prev => ({
                ...prev,
                [sectionId]: new Set([...(prev[sectionId] ?? []), memoIndex])
              }));
            }}
          />
        </div>
      </div>

      {/* 액션플랜 모달 */}
      {showActionPlan && actionPlan !== null && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
          onClick={() => setShowActionPlan(false)}
        >
          <div
            className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl mx-4 flex flex-col max-h-[85vh]"
            onClick={(e) => e.stopPropagation()}
          >
            {/* 헤더 */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 flex-shrink-0">
              <div className="flex items-center gap-2">
                <span className="text-lg">📋</span>
                <h2 className="font-bold text-slate-800 text-base">액션플랜</h2>
              </div>
              <button
                onClick={() => setShowActionPlan(false)}
                className="w-7 h-7 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors text-sm"
              >
                ✕
              </button>
            </div>

            {/* 본문 */}
            <div className="overflow-y-auto px-6 py-5 flex-1">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  h1: ({ children }) => (
                    <h1 className="text-base font-bold text-slate-800 mt-5 mb-2 pb-1.5 border-b border-slate-200 first:mt-0">{children}</h1>
                  ),
                  h2: ({ children }) => (
                    <h2 className="text-sm font-bold text-slate-800 mt-4 mb-1.5">{children}</h2>
                  ),
                  h3: ({ children }) => (
                    <h3 className="text-sm font-semibold text-slate-700 mt-3 mb-1">{children}</h3>
                  ),
                  p: ({ children }) => (
                    <p className="text-sm text-slate-600 leading-relaxed my-1.5">{children}</p>
                  ),
                  ul: ({ children }) => (
                    <ul className="list-disc list-inside my-1.5 space-y-0.5 text-sm text-slate-600">{children}</ul>
                  ),
                  ol: ({ children }) => (
                    <ol className="list-decimal list-inside my-1.5 space-y-0.5 text-sm text-slate-600">{children}</ol>
                  ),
                  li: ({ children }) => (
                    <li className="text-sm text-slate-600 leading-relaxed">{children}</li>
                  ),
                  strong: ({ children }) => (
                    <strong className="font-semibold text-slate-800">{children}</strong>
                  ),
                  code: ({ children }) => (
                    <code className="text-xs text-blue-700 bg-blue-50 px-1 py-0.5 rounded">{children}</code>
                  ),
                  blockquote: ({ children }) => (
                    <blockquote className="border-l-2 border-slate-300 pl-3 my-2 text-slate-500 text-sm italic">{children}</blockquote>
                  ),
                  hr: () => <hr className="my-3 border-slate-200" />,
                  table: ({ children }) => (
                    <table className="w-full border-collapse my-3 text-xs">{children}</table>
                  ),
                  th: ({ children }) => (
                    <th className="border border-slate-200 px-3 py-2 bg-slate-50 text-left text-xs font-semibold text-slate-700">{children}</th>
                  ),
                  td: ({ children }) => (
                    <td className="border border-slate-200 px-3 py-2 text-xs text-slate-600">{children}</td>
                  ),
                }}
              >
                {actionPlan}
              </ReactMarkdown>
            </div>

            {/* 푸터 */}
            <div className="flex-shrink-0 px-6 py-3 border-t border-slate-100 flex justify-end">
              <button
                onClick={() => setShowActionPlan(false)}
                className="px-4 py-2 rounded-xl bg-slate-800 text-white text-sm font-medium hover:bg-slate-900 transition-colors"
              >
                닫기
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 문서 점검 모달 */}
      {documentCheck !== null && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
          onClick={() => setDocumentCheck(null)}
        >
          <div
            className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl mx-4 flex flex-col max-h-[85vh]"
            onClick={(e) => e.stopPropagation()}
          >
            {/* 헤더 */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 flex-shrink-0">
              <div className="flex items-center gap-2">
                <span className="text-lg">🔍</span>
                <h2 className="font-bold text-slate-800 text-base">문서 점검 결과</h2>
              </div>
              <button
                onClick={() => setDocumentCheck(null)}
                className="w-7 h-7 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors text-sm"
              >
                ✕
              </button>
            </div>

            {/* 본문 */}
            <div className="overflow-y-auto px-6 py-5 flex-1">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  h1: ({ children }) => (
                    <h1 className="text-base font-bold text-slate-800 mt-5 mb-2 pb-1.5 border-b border-slate-200 first:mt-0">{children}</h1>
                  ),
                  h2: ({ children }) => (
                    <h2 className="text-sm font-bold text-slate-800 mt-4 mb-1.5">{children}</h2>
                  ),
                  h3: ({ children }) => (
                    <h3 className="text-sm font-semibold text-slate-700 mt-3 mb-1">{children}</h3>
                  ),
                  p: ({ children }) => (
                    <p className="text-sm text-slate-600 leading-relaxed my-1.5">{children}</p>
                  ),
                  ul: ({ children }) => (
                    <ul className="list-disc list-inside my-1.5 space-y-0.5 text-sm text-slate-600">{children}</ul>
                  ),
                  ol: ({ children }) => (
                    <ol className="list-decimal list-inside my-1.5 space-y-0.5 text-sm text-slate-600">{children}</ol>
                  ),
                  li: ({ children }) => (
                    <li className="text-sm text-slate-600 leading-relaxed">{children}</li>
                  ),
                  strong: ({ children }) => (
                    <strong className="font-semibold text-slate-800">{children}</strong>
                  ),
                  code: ({ children }) => (
                    <code className="text-xs text-teal-700 bg-teal-50 px-1 py-0.5 rounded">{children}</code>
                  ),
                  blockquote: ({ children }) => (
                    <blockquote className="border-l-2 border-slate-300 pl-3 my-2 text-slate-500 text-sm italic">{children}</blockquote>
                  ),
                  hr: () => <hr className="my-3 border-slate-200" />,
                  table: ({ children }) => (
                    <table className="w-full border-collapse my-3 text-xs">{children}</table>
                  ),
                  th: ({ children }) => (
                    <th className="border border-slate-200 px-3 py-2 bg-slate-50 text-left text-xs font-semibold text-slate-700">{children}</th>
                  ),
                  td: ({ children }) => (
                    <td className="border border-slate-200 px-3 py-2 text-xs text-slate-600">{children}</td>
                  ),
                }}
              >
                {documentCheck}
              </ReactMarkdown>
            </div>

            {/* 푸터 */}
            <div className="flex-shrink-0 px-6 py-3 border-t border-slate-100 flex justify-end">
              <button
                onClick={() => setDocumentCheck(null)}
                className="px-4 py-2 rounded-xl bg-slate-800 text-white text-sm font-medium hover:bg-slate-900 transition-colors"
              >
                닫기
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
