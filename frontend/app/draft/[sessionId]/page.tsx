"use client";
import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import DocumentPanel, { type DocumentPanelHandle } from "@/components/result/DocumentPanel";
import MemoPanel, { type MemoPanelHandle } from "@/components/result/MemoPanel";
import { useResultStore } from "@/store/resultStore";
import { api } from "@/lib/api";
import EmailGateModal, { hasLeadEmail } from "@/components/EmailGateModal";

/** 기본 초안 검토 화면 — 읽기전용 초안 + 심사위원 피드백.
 *  양식 변환 후에도 이 URL로 초안을 다시 열람할 수 있다. */
export default function DraftPage() {
  const params = useParams();
  const router = useRouter();
  const sessionId = params.sessionId as string;

  const { sections, activeSectionId, init, setActiveSectionId, updateSectionSuggestions } =
    useResultStore();

  const documentPanelRef = useRef<DocumentPanelHandle>(null);
  const memoPanelRef = useRef<MemoPanelHandle>(null);

  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAnchors, setShowAnchors] = useState(false);
  const [isFeedbackRunning, setIsFeedbackRunning] = useState(false);
  const [feedbackDoneCount, setFeedbackDoneCount] = useState(0);
  const [feedbackTotal, setFeedbackTotal] = useState(0);
  const [feedbackError, setFeedbackError] = useState<string | null>(null);
  const [usageData, setUsageData] = useState<Record<string, { used: number; max: number }>>({});
  const [isDownloading, setIsDownloading] = useState(false);
  const [showEmailGate, setShowEmailGate] = useState(false);

  function handleDownloadDraft() {
    // 초안 열람까지 무료 — DOCX 다운로드 직전에만 이메일 수집 (최초 1회)
    if (!hasLeadEmail()) {
      setShowEmailGate(true);
      return;
    }
    void doDownloadDraft();
  }

  async function doDownloadDraft() {
    setIsDownloading(true);
    try {
      const res = await fetch(
        `/api/sessions/${sessionId}/export/docx?source=framework&business_name=(미지정)`
      );
      if (!res.ok) throw new Error("download failed");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `사업계획서초안_${sessionId}.docx`;
      a.click();
      URL.revokeObjectURL(url);
      (window as unknown as { gtag?: (...args: unknown[]) => void }).gtag?.("event", "docx_download", { source: "draft" });
    } catch {
      setFeedbackError("초안 DOCX 다운로드에 실패했습니다.");
    } finally {
      setIsDownloading(false);
    }
  }

  useEffect(() => {
    async function load() {
      try {
        const framework = await api.getFramework(sessionId);
        init(sessionId, framework.sections, Number(framework.overall_completion) || 0);
        localStorage.setItem("bizplan_session_id", sessionId);
        api.getUsage(sessionId).then(setUsageData).catch(() => {});
        // 피드백을 이미 받았던 초안이면 앵커 바로 표시
        const hasFeedback = framework.sections.some((s) => s.inline_suggestions.length > 0);
        if (hasFeedback) setShowAnchors(true);
      } catch {
        const savedId = localStorage.getItem("bizplan_session_id");
        if (savedId && savedId !== sessionId) {
          router.replace(`/draft/${savedId}`);
        } else {
          setError("초안을 불러올 수 없습니다.");
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

  function handleMemoTitleClick(anchorText: string) {
    if (activeSectionId) {
      documentPanelRef.current?.scrollToAnchorByText(activeSectionId, anchorText);
    }
  }

  async function handleFeedback() {
    setIsFeedbackRunning(true);
    setFeedbackError(null);
    setFeedbackDoneCount(0);
    setFeedbackTotal(0);
    try {
      const response = await fetch(`/api/sessions/${sessionId}/feedback`, { method: "POST" });
      if (response.status === 429) {
        setFeedbackError("피드백 확인은 1회만 가능합니다.");
        return;
      }
      if (!response.ok) {
        setFeedbackError("피드백 생성에 실패했습니다. 잠시 후 다시 시도해주세요.");
        return;
      }
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
              setShowAnchors(true);
            } else if (eventType === "all_done") {
              for (const s of (data.sections ?? [])) {
                updateSectionSuggestions(s.section_id, s.inline_suggestions);
              }
            } else if (eventType === "error") {
              setFeedbackError(data.message ?? "피드백 생성 중 오류가 발생했습니다.");
            }
          } catch { /* skip */ }
        }
      }
    } finally {
      setIsFeedbackRunning(false);
      api.getUsage(sessionId).then(setUsageData).catch(() => {});
    }
  }

  const greenCount = sections.filter((s) => s.confidence_level === "green").length;
  const yellowCount = sections.filter((s) => s.confidence_level === "yellow").length;
  const redCount = sections.filter((s) => s.confidence_level === "red").length;
  const totalFeedbacks = sections.reduce((sum, s) => sum + s.inline_suggestions.length, 0);

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
        <button onClick={() => router.push("/")} className="px-4 py-2 bg-slate-600 text-white rounded-lg text-sm">
          처음으로
        </button>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col bg-white overflow-hidden">
      {/* 헤더 */}
      <header className="flex-shrink-0 border-b border-slate-200 bg-white px-4 py-3 overflow-visible">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <button
              onClick={() => router.push("/")}
              className="text-slate-400 hover:text-slate-600 p-1 flex-shrink-0"
            >
              ←
            </button>
            <div className="min-w-0">
              <h1 className="font-semibold text-slate-800 text-sm truncate">사업계획서 기본 초안</h1>
            </div>
          </div>

          <div className="flex items-center gap-3 flex-shrink-0">
            <div className="relative group">
              <button
                onClick={handleFeedback}
                disabled={isFeedbackRunning}
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
              <div className="absolute top-full left-1/2 -translate-x-1/2 mt-2 hidden group-hover:block z-50">
                <div className="bg-white text-slate-700 text-xs rounded-xl px-3 py-2 whitespace-nowrap shadow-xl border border-slate-200 font-medium">
                  심사위원 관점에서 초안을 검토하고 피드백을 제시합니다
                </div>
              </div>
            </div>
            <button
              onClick={handleDownloadDraft}
              disabled={isDownloading}
              className="flex items-center gap-2 px-3 py-2 rounded-xl border border-slate-200 text-slate-600 text-sm font-medium hover:bg-slate-50 disabled:opacity-50 transition-colors"
            >
              {isDownloading ? (
                <>
                  <span className="w-4 h-4 border-2 border-slate-400 border-t-transparent rounded-full animate-spin" />
                  다운로드 중
                </>
              ) : (
                "초안 DOCX 다운로드"
              )}
            </button>
            <button
              onClick={() => router.push(`/recommend?session=${sessionId}&mode=convert`)}
              className="flex items-center gap-2 px-4 py-2 rounded-xl bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 transition-colors shadow-sm"
            >
              지원사업 선택하기 →
            </button>
          </div>
        </div>
      </header>

      {/* 피드백 에러 배너 */}
      {feedbackError && (
        <div className="flex-shrink-0 bg-red-50 border-b border-red-200 px-4 py-2 flex items-center justify-between">
          <span className="text-sm text-red-700">{feedbackError}</span>
          <button
            onClick={() => setFeedbackError(null)}
            className="text-red-400 hover:text-red-600 text-xs ml-4 flex-shrink-0"
          >
            ✕
          </button>
        </div>
      )}

      {/* 본문: 좌우 분할 */}
      <div className="flex-1 flex overflow-hidden">
        {/* 좌: 문서 패널 (60%) — 읽기전용 */}
        <div className="w-3/5 border-r border-slate-200 flex flex-col overflow-hidden">
          <div className="flex-shrink-0 px-4 py-1.5 border-b border-slate-100 bg-white">
            <div className="flex items-center gap-4 text-xs text-slate-400">
              <span>🟢 완성 {greenCount}</span>
              <span>🟡 보완 필요 {yellowCount}</span>
              <span>🔴 미흡 {redCount}</span>
              {showAnchors && <span>⚖️ 피드백 {totalFeedbacks}건</span>}
            </div>
          </div>
          <DocumentPanel
            ref={documentPanelRef}
            sections={sections}
            activeSectionId={activeSectionId}
            showAnchors={showAnchors}
            readOnly
            onSectionClick={setActiveSectionId}
            onAnchorClick={handleAnchorClick}
          />
        </div>

        {/* 우: 심사위원 피드백 패널 (40%) — 읽기전용 */}
        <div className="w-2/5 flex flex-col overflow-hidden bg-slate-50">
          <MemoPanel
            ref={memoPanelRef}
            sections={sections}
            activeSectionId={activeSectionId}
            showAnchors={showAnchors}
            onMemoTitleClick={handleMemoTitleClick}
          />
        </div>
      </div>

      <EmailGateModal
        sessionId={sessionId}
        open={showEmailGate}
        onClose={() => setShowEmailGate(false)}
        onDone={() => {
          setShowEmailGate(false);
          void doDownloadDraft();
        }}
      />
    </div>
  );
}
