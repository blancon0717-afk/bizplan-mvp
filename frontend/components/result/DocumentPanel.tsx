"use client";
import { forwardRef, useImperativeHandle, useRef, useState } from "react";
import type { SectionResult } from "@/lib/types";
import SegmentRenderer, { type SegmentRendererHandle } from "./SegmentRenderer";

export interface DocumentPanelHandle {
  scrollToAnchor: (sectionId: string, originalIndex: number) => void;
  scrollToAnchorByText: (sectionId: string, anchorText: string) => void;
  /** 피드백 카드 호버 시 문서 내 앵커 하이라이트 (anchorText가 null이면 해제) */
  setHoverAnchor: (sectionId: string, anchorText: string | null) => void;
}

const CATEGORY_KO: Record<string, string> = {
  Problem: "문제인식",
  Solution: "실현가능성",
  "Scale-up": "사업화전략",
  Team: "팀역량",
  General: "개요",
};

const CONFIDENCE_BADGE = {
  green: { label: "근거충분", cls: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  yellow: { label: "일부추론", cls: "bg-amber-50 text-amber-700 border-amber-200" },
  red: { label: "보완필요", cls: "bg-red-50 text-red-700 border-red-200" },
};

const CONFIDENCE_DOT = {
  green: "bg-emerald-500",
  yellow: "bg-amber-400",
  red: "bg-red-500",
};

interface DocumentPanelProps {
  sections: SectionResult[];
  activeSectionId: string | null;
  editingSectionId?: string | null;
  editContent?: string;
  showAnchors: boolean;
  /** true면 편집·고도화 버튼 숨김 (draft 초안 검토 화면용) */
  readOnly?: boolean;
  onSectionClick: (id: string) => void;
  onAnchorClick: (sectionId: string, memoIndex: number) => void;
  /** 잠긴 섹션의 "전체 내용 보기" CTA 클릭 — 결제 안내 모달 열기 */
  onUnlockClick?: () => void;
  onRegenerate?: (sectionId: string) => void;
  isRegenerating?: Record<string, boolean>;
  usageData?: Record<string, { used: number; max: number }>;
  onStartEdit?: (sectionId: string, content: string) => void;
  onEditContentChange?: (content: string) => void;
  onSaveEdit?: () => void;
  onCancelEdit?: () => void;
  passedMemoMap?: Record<string, Set<number>>;
}

function getMajorGroup(sectionId: string): string {
  if (sectionId === "overview") return "overview";
  return sectionId.split("-")[0];
}

interface SectionGroup {
  majorId: string;
  categoryKo: string;
  sections: SectionResult[];
}

function groupSections(sections: SectionResult[]): SectionGroup[] {
  const groups: SectionGroup[] = [];
  let current: SectionGroup | null = null;

  for (const s of sections) {
    const major = getMajorGroup(s.section_id);
    const categoryKo = CATEGORY_KO[s.category] ?? (s.category || "");

    if (!current || current.majorId !== major) {
      current = { majorId: major, categoryKo, sections: [s] };
      groups.push(current);
    } else {
      current.sections.push(s);
    }
  }
  return groups;
}

const SPY_OFFSET_PX = 140;

const DocumentPanel = forwardRef<DocumentPanelHandle, DocumentPanelProps>(function DocumentPanel({
  sections,
  activeSectionId,
  editingSectionId = null,
  editContent = "",
  showAnchors,
  readOnly = false,
  onSectionClick,
  onAnchorClick,
  onUnlockClick,
  onRegenerate,
  isRegenerating = {},
  usageData,
  onStartEdit,
  onEditContentChange,
  onSaveEdit,
  onCancelEdit,
  passedMemoMap,
}, ref) {
  const sectionRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const segRendererRefs = useRef<Record<string, SegmentRendererHandle | null>>({});
  const scrollRef = useRef<HTMLDivElement>(null);
  const progressRef = useRef<HTMLDivElement>(null);
  const rafId = useRef<number | null>(null);
  const spyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hoveredMarks = useRef<Element[]>([]);
  const [highlightInferred, setHighlightInferred] = useState(true);

  useImperativeHandle(ref, () => ({
    scrollToAnchor: (sectionId: string, originalIndex: number) => {
      segRendererRefs.current[sectionId]?.scrollToAnchor(originalIndex);
    },
    scrollToAnchorByText: (sectionId: string, anchorText: string) => {
      const container = sectionRefs.current[sectionId];
      if (!container) return;
      const marks = container.querySelectorAll('mark.memo-anchor');
      for (const mark of marks) {
        if (mark.textContent === anchorText) {
          mark.scrollIntoView({ behavior: "smooth", block: "center" });
          break;
        }
      }
    },
    setHoverAnchor: (sectionId: string, anchorText: string | null) => {
      hoveredMarks.current.forEach((m) => m.classList.remove("anchor-hover"));
      hoveredMarks.current = [];
      if (!anchorText) return;
      const container = sectionRefs.current[sectionId];
      if (!container) return;
      for (const mark of container.querySelectorAll("mark")) {
        if (mark.textContent === anchorText) {
          mark.classList.add("anchor-hover");
          hoveredMarks.current.push(mark);
        }
      }
    },
  }));

  function scrollToSection(id: string) {
    sectionRefs.current[id]?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /** 스크롤 위치 기준 현재 읽는 섹션 계산 → 활성 섹션 동기화 (피드백 패널이 따라옴) */
  function updateSpy() {
    const container = scrollRef.current;
    if (!container) return;
    const containerTop = container.getBoundingClientRect().top;
    let currentId: string | null = null;
    // ponytail: 섹션 수십 개 수준의 순차 스캔 — 수백 개로 늘면 이진 탐색으로 교체
    for (const s of sections) {
      const el = sectionRefs.current[s.section_id];
      if (!el) continue;
      if (el.getBoundingClientRect().top - containerTop <= SPY_OFFSET_PX) {
        currentId = s.section_id;
      } else {
        break;
      }
    }
    if (currentId && currentId !== activeSectionId) {
      onSectionClick(currentId);
    }
  }

  function handleScroll() {
    if (rafId.current == null) {
      rafId.current = requestAnimationFrame(() => {
        rafId.current = null;
        const container = scrollRef.current;
        if (container && progressRef.current) {
          const max = container.scrollHeight - container.clientHeight;
          const pct = max > 0 ? (container.scrollTop / max) * 100 : 0;
          progressRef.current.style.width = `${pct}%`;
        }
      });
    }
    if (spyTimer.current) clearTimeout(spyTimer.current);
    spyTimer.current = setTimeout(updateSpy, 150);
  }

  const groups = groupSections(sections);

  return (
    <div className="h-full flex flex-col">
      {/* 섹션 네비게이션 — 현재 읽는 위치 자동 하이라이트 */}
      <div className="flex-shrink-0 px-4 py-2 border-b border-slate-100 bg-white">
        <div className="flex gap-1.5 flex-wrap">
          {sections.map((s) => {
            const dot = CONFIDENCE_DOT[s.confidence_level] ?? CONFIDENCE_DOT.red;
            const isNavActive = activeSectionId === s.section_id;
            return (
              <button
                key={s.section_id}
                onClick={() => { onSectionClick(s.section_id); scrollToSection(s.section_id); }}
                title={s.section_title}
                className={`flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full border transition-all max-w-[180px] ${
                  isNavActive
                    ? "bg-slate-800 text-white border-slate-800 font-medium shadow-sm"
                    : "bg-white text-slate-500 border-slate-200 hover:bg-slate-50"
                }`}
              >
                <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dot}`} />
                <span className="truncate">
                  {s.section_id !== "overview" ? `${s.section_id} ` : ""}{s.section_title}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* 본문 신뢰도 범례 + AI 추론 표시 토글 */}
      <div className="flex-shrink-0 px-4 py-1.5 border-b border-slate-100 bg-white flex items-center justify-between">
        <div className="flex items-center gap-4 text-xs text-slate-400">
          <span>답변 기반 본문</span>
          <span className={highlightInferred ? "ai-inferred text-slate-500" : "text-slate-400"}>
            AI 추론 부분
          </span>
        </div>
        <button
          onClick={() => setHighlightInferred((v) => !v)}
          role="switch"
          aria-checked={highlightInferred}
          className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 transition-colors"
        >
          AI 추론 표시
          <span className={`relative w-7 h-4 rounded-full transition-colors ${highlightInferred ? "bg-emerald-400" : "bg-slate-300"}`}>
            <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-white shadow transition-all ${highlightInferred ? "left-3.5" : "left-0.5"}`} />
          </span>
        </button>
      </div>

      {/* 읽기 진행 바 */}
      <div className="flex-shrink-0 h-0.5 bg-slate-100 relative">
        <div
          ref={progressRef}
          className="absolute inset-y-0 left-0 bg-blue-500"
          style={{ width: 0 }}
        />
      </div>

      {/* 문서 본문 — 회색 바탕 위 지면(시트) */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto custom-scrollbar bg-slate-100/70 px-4 md:px-6 py-6"
      >
        <div className="max-w-[760px] mx-auto bg-white rounded-lg border border-slate-200/80 shadow-sm px-8 md:px-12 py-10">
          {groups.map((group, gi) => (
            <div key={group.majorId}>
              {/* 대분류 헤더 */}
              {group.categoryKo && (
                <div className={`${gi > 0 ? "mt-14" : "mt-0"} mb-6 pb-2.5 border-b-2 border-slate-800`}>
                  <h2 className="text-lg font-bold text-slate-900 tracking-tight">
                    {group.majorId !== "overview" ? `${group.majorId}. ` : ""}{group.categoryKo}
                  </h2>
                </div>
              )}

              {/* 소섹션들 */}
              {group.sections.map((section, si) => {
                const conf = CONFIDENCE_BADGE[section.confidence_level] ?? CONFIDENCE_BADGE.red;
                const dot = CONFIDENCE_DOT[section.confidence_level] ?? CONFIDENCE_DOT.red;
                const isActive = activeSectionId === section.section_id;
                const isEditing = editingSectionId === section.section_id;
                const resolvedCount = section.resolved_memo_count ?? 0;
                const totalMemos = section.inline_suggestions.length;

                return (
                  <div
                    key={section.section_id}
                    ref={(el) => { sectionRefs.current[section.section_id] = el; }}
                    className={`group relative mb-10 pl-3 border-l-2 transition-colors duration-200 ${
                      isActive ? "border-blue-500" : "border-transparent"
                    }`}
                    onClick={() => !isEditing && onSectionClick(section.section_id)}
                  >
                    {/* 소섹션 제목 행 — 관리 UI는 호버 시에만 노출 */}
                    <div className="flex items-start justify-between gap-3 mb-3 cursor-pointer">
                      <h3 className="text-base font-bold text-slate-900 leading-snug min-w-0">
                        {section.section_id !== "overview" && (
                          <span className="text-slate-400 font-semibold mr-1.5">{section.section_id}.</span>
                        )}
                        {section.section_title}
                      </h3>
                      <div className="flex items-center gap-2 flex-shrink-0 pt-0.5">
                        {section.truncated && (
                          <span
                            className="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200"
                            title="응답 잘림 — 재생성 권장"
                          >
                            잘림
                          </span>
                        )}
                        <span className={`w-2 h-2 rounded-full ${dot}`} title={`${conf.label} · 완성도 ${section.effective_completion_score}%`} />
                        {/* ponytail: 호버 전용 관리 UI — 터치 기기 대응 필요 시 focus-within/롱프레스 추가 */}
                        <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 pointer-events-none group-hover:pointer-events-auto transition-opacity">
                          {showAnchors && totalMemos > 0 && (
                            <span className="text-xs text-slate-400 whitespace-nowrap">
                              메모 {resolvedCount}/{totalMemos}
                            </span>
                          )}
                          <span className={`text-xs px-1.5 py-0.5 rounded border whitespace-nowrap ${conf.cls}`}>
                            {conf.label}
                          </span>
                          <span className="text-xs font-medium text-slate-500">
                            {section.effective_completion_score}%
                          </span>
                          {!readOnly && !isEditing && (
                            <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                              <button
                                onClick={() => onStartEdit?.(section.section_id, section.user_edited_content ?? section.content)}
                                disabled={(usageData?.edit?.used ?? 0) >= (usageData?.edit?.max ?? 1)}
                                className={`text-xs px-2 py-0.5 rounded border border-slate-200 hover:bg-slate-50 transition-colors whitespace-nowrap ${(usageData?.edit?.used ?? 0) >= (usageData?.edit?.max ?? 1) ? "text-gray-300 cursor-not-allowed" : "text-slate-400 hover:text-slate-600"}`}
                              >
                                편집 ({usageData?.edit?.used ?? 0}/{usageData?.edit?.max ?? 1})
                              </button>
                              <button
                                onClick={() => onRegenerate?.(section.section_id)}
                                disabled={!!isRegenerating[section.section_id] || (usageData?.regenerate?.used ?? 0) >= (usageData?.regenerate?.max ?? 1)}
                                className={`text-xs px-2 py-0.5 rounded border border-slate-200 hover:bg-slate-50 disabled:opacity-40 transition-colors whitespace-nowrap ${(usageData?.regenerate?.used ?? 0) >= (usageData?.regenerate?.max ?? 1) ? "text-gray-300" : "text-slate-400 hover:text-slate-600"}`}
                              >
                                {isRegenerating[section.section_id] ? (
                                  <span className="w-3 h-3 border border-slate-400 border-t-transparent rounded-full animate-spin inline-block" />
                                ) : `고도화 (${usageData?.regenerate?.used ?? 0}/${usageData?.regenerate?.max ?? 1})`}
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* 본문 — 잠긴 섹션: 티저 + 블러 + 결제 CTA (원문은 서버에서 미전송) */}
                    {section.locked ? (
                      <div className="relative overflow-hidden rounded-lg" aria-label="결제 후 열람 가능한 섹션">
                        <div className="text-[15px] text-slate-800 leading-[1.8] whitespace-pre-wrap blur-[4px] select-none pointer-events-none" aria-hidden="true">
                          {(section.preview || "이 섹션의 내용은 결제 후 열람할 수 있습니다.") + "\n"}
                          {/* 실제 분량감을 주는 장식용 반복 줄 — 원문 아님 */}
                          {"내용을 보호하기 위해 일부만 표시됩니다. 전체 내용은 결제 후 확인하실 수 있습니다. ".repeat(6)}
                        </div>
                        <div className="absolute inset-0 bg-gradient-to-b from-white/10 via-white/60 to-white flex flex-col items-center justify-center gap-3">
                          <span className="text-2xl" aria-hidden="true">🔒</span>
                          <p className="text-sm text-slate-600 font-medium text-center px-6">
                            성장 전략·자금 계획·기업 구성은<br />결제 후 열람할 수 있습니다
                          </p>
                          <button
                            onClick={(e) => { e.stopPropagation(); onUnlockClick?.(); }}
                            className="px-5 py-2.5 rounded-xl bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 transition-colors shadow-sm"
                          >
                            전체 내용 보기
                          </button>
                        </div>
                      </div>
                    ) : isEditing ? (
                      <div onClick={(e) => e.stopPropagation()}>
                        <textarea
                          value={editContent}
                          onChange={(e) => onEditContentChange?.(e.target.value)}
                          className="w-full min-h-[200px] border border-blue-300 rounded-xl px-4 py-3 text-sm text-slate-800 resize-y focus:outline-none focus:ring-2 focus:ring-blue-400 bg-blue-50/30 leading-relaxed"
                          autoFocus
                        />
                        <div className="flex justify-end gap-2 mt-2">
                          <button
                            onClick={() => onCancelEdit?.()}
                            className="px-3 py-1.5 text-xs text-slate-500 border border-slate-200 rounded-lg hover:bg-slate-50"
                          >
                            취소
                          </button>
                          <button
                            onClick={() => onSaveEdit?.()}
                            className="px-3 py-1.5 text-xs bg-blue-600 text-white rounded-lg hover:bg-blue-700"
                          >
                            저장
                          </button>
                        </div>
                      </div>
                    ) : section.user_edited_content !== null ? (
                      <div className="text-[15px] text-slate-800 leading-[1.8] whitespace-pre-wrap">
                        {section.user_edited_content}
                      </div>
                    ) : (
                      <SegmentRenderer
                        ref={(el) => { segRendererRefs.current[section.section_id] = el; }}
                        segments={section.content_segments}
                        suggestions={[...section.inline_suggestions]
                          .map((m) => ({ ...m, originalIndex: section.inline_suggestions.indexOf(m) }))
                          .filter((m) => !passedMemoMap?.[section.section_id]?.has(m.originalIndex))
                          .sort((a, b) => {
                            const order: Record<string, number> = { critical: 0, warning: 1, info: 2 };
                            return (order[a.severity] ?? 1) - (order[b.severity] ?? 1);
                          })
                          .slice(0, 5)
                          .sort((a, b) => {
                            const posA = (section.content_segments ?? []).map(s => s.text ?? "").join("").indexOf(a.anchor_text);
                            const posB = (section.content_segments ?? []).map(s => s.text ?? "").join("").indexOf(b.anchor_text);
                            return posA - posB;
                          })}
                        showAnchors={showAnchors}
                        highlightInferred={highlightInferred}
                        onAnchorClick={(idx) => onAnchorClick(section.section_id, idx)}
                      />
                    )}

                    {/* 섹션 간 구분선 (그룹 내 마지막 아니면 표시) */}
                    {si < group.sections.length - 1 && (
                      <div className="mt-8 border-t border-dashed border-slate-200" />
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
});
DocumentPanel.displayName = "DocumentPanel";
export default DocumentPanel;
