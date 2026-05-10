"use client";
import { forwardRef, useImperativeHandle, useRef } from "react";
import type { SectionResult } from "@/lib/types";
import SegmentRenderer, { type SegmentRendererHandle } from "./SegmentRenderer";

export interface DocumentPanelHandle {
  scrollToAnchor: (sectionId: string, originalIndex: number) => void;
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
  editingSectionId: string | null;
  editContent: string;
  showAnchors: boolean;
  onSectionClick: (id: string) => void;
  onAnchorClick: (sectionId: string, memoIndex: number) => void;
  onRegenerate: (sectionId: string) => void;
  isRegenerating: Record<string, boolean>;
  onStartEdit: (sectionId: string, content: string) => void;
  onEditContentChange: (content: string) => void;
  onSaveEdit: () => void;
  onCancelEdit: () => void;
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

const DocumentPanel = forwardRef<DocumentPanelHandle, DocumentPanelProps>(function DocumentPanel({
  sections,
  activeSectionId,
  editingSectionId,
  editContent,
  showAnchors,
  onSectionClick,
  onAnchorClick,
  onRegenerate,
  isRegenerating,
  onStartEdit,
  onEditContentChange,
  onSaveEdit,
  onCancelEdit,
}, ref) {
  const sectionRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const segRendererRefs = useRef<Record<string, SegmentRendererHandle | null>>({});

  useImperativeHandle(ref, () => ({
    scrollToAnchor: (sectionId: string, originalIndex: number) => {
      segRendererRefs.current[sectionId]?.scrollToAnchor(originalIndex);
    },
  }));

  function scrollToSection(id: string) {
    sectionRefs.current[id]?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  const groups = groupSections(sections);

  return (
    <div className="h-full flex flex-col">
      {/* 섹션 네비게이션 */}
      <div className="flex-shrink-0 px-4 py-2 border-b border-slate-100 bg-white">
        <div className="flex gap-1.5 flex-wrap">
          {sections.map((s) => {
            const dot = CONFIDENCE_DOT[s.confidence_level] ?? CONFIDENCE_DOT.red;
            return (
              <button
                key={s.section_id}
                onClick={() => { onSectionClick(s.section_id); scrollToSection(s.section_id); }}
                className={`flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full border transition-all ${
                  activeSectionId === s.section_id
                    ? "bg-slate-800 text-white border-slate-800 font-medium shadow-sm"
                    : "bg-white text-slate-500 border-slate-200 hover:bg-slate-50"
                }`}
              >
                <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dot}`} />
                {s.section_id}
              </button>
            );
          })}
        </div>
      </div>

      {/* 본문 신뢰도 범례 */}
      <div className="flex-shrink-0 px-4 py-1.5 border-b border-slate-100 bg-slate-50">
        <div className="flex items-center gap-4 text-xs text-slate-400">
          <span className="flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-slate-600 inline-block flex-shrink-0" />
            답변 기반
          </span>
          <span className="flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block flex-shrink-0" />
            AI 추론
          </span>
        </div>
      </div>

      {/* 문서 본문 */}
      <div className="flex-1 overflow-y-auto custom-scrollbar px-8 py-6">
        {groups.map((group, gi) => (
          <div key={group.majorId}>
            {/* 대분류 헤더 */}
            {group.categoryKo && (
              <div className={`flex items-center gap-3 ${gi > 0 ? "mt-10" : "mt-0"} mb-5`}>
                <span className="text-xs font-bold text-slate-400 tracking-widest uppercase">
                  {group.majorId !== "overview" ? `${group.majorId}.` : ""} {group.categoryKo}
                </span>
                <div className="flex-1 h-px bg-slate-200" />
              </div>
            )}

            {/* 소섹션들 */}
            {group.sections.map((section, si) => {
              const conf = CONFIDENCE_BADGE[section.confidence_level] ?? CONFIDENCE_BADGE.red;
              const isActive = activeSectionId === section.section_id;
              const isEditing = editingSectionId === section.section_id;
              const resolvedCount = section.resolved_memo_count ?? 0;
              const totalMemos = section.inline_suggestions.length;

              return (
                <div
                  key={section.section_id}
                  ref={(el) => { sectionRefs.current[section.section_id] = el; }}
                  className={`relative mb-8 transition-all duration-200 ${
                    isActive ? "pl-3 border-l-2 border-blue-400" : "pl-3 border-l-2 border-transparent"
                  }`}
                  onClick={() => !isEditing && onSectionClick(section.section_id)}
                >
                  {/* 소섹션 제목 행 */}
                  <div className="flex items-start justify-between gap-2 mb-3 cursor-pointer">
                    <div className="flex items-center gap-2 min-w-0 flex-1">
                      <h3 className="text-sm font-bold text-slate-800 leading-snug">
                        {section.section_id !== "overview" && (
                          <span className="text-slate-400 mr-1">{section.section_id}.</span>
                        )}
                        {section.section_title}
                      </h3>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0">
                      {showAnchors && totalMemos > 0 && (
                        <span className="text-xs text-slate-400">
                          메모 {resolvedCount}/{totalMemos}
                        </span>
                      )}
                      <span className={`text-xs px-1.5 py-0.5 rounded border ${conf.cls}`}>
                        {conf.label}
                      </span>
                      <span className="text-xs font-medium text-slate-500">
                        {section.effective_completion_score}%
                      </span>
                      {section.truncated && (
                        <span className="relative group cursor-default">
                          <span className="text-sm">⚠️</span>
                          <span className="absolute right-0 top-full mt-1 z-10 px-2 py-1 rounded bg-slate-800 text-white text-xs whitespace-nowrap hidden group-hover:block shadow-lg">
                            응답 잘림 — 재생성 권장
                          </span>
                        </span>
                      )}
                      {!isEditing && (
                        <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                          <button
                            onClick={() => onStartEdit(section.section_id, section.user_edited_content ?? section.content)}
                            className="text-xs px-2 py-0.5 rounded border border-slate-200 text-slate-400 hover:text-slate-600 hover:bg-slate-50 transition-colors"
                          >
                            편집
                          </button>
                          <button
                            onClick={() => onRegenerate(section.section_id)}
                            disabled={!!isRegenerating[section.section_id]}
                            className="text-xs px-2 py-0.5 rounded border border-slate-200 text-slate-400 hover:text-slate-600 hover:bg-slate-50 disabled:opacity-40 transition-colors"
                          >
                            {isRegenerating[section.section_id] ? (
                              <span className="w-3 h-3 border border-slate-400 border-t-transparent rounded-full animate-spin inline-block" />
                            ) : "고도화"}
                          </button>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* 본문 */}
                  {isEditing ? (
                    <div onClick={(e) => e.stopPropagation()}>
                      <textarea
                        value={editContent}
                        onChange={(e) => onEditContentChange(e.target.value)}
                        className="w-full min-h-[200px] border border-blue-300 rounded-xl px-4 py-3 text-sm text-slate-800 resize-y focus:outline-none focus:ring-2 focus:ring-blue-400 bg-blue-50/30 leading-relaxed"
                        autoFocus
                      />
                      <div className="flex justify-end gap-2 mt-2">
                        <button
                          onClick={onCancelEdit}
                          className="px-3 py-1.5 text-xs text-slate-500 border border-slate-200 rounded-lg hover:bg-slate-50"
                        >
                          취소
                        </button>
                        <button
                          onClick={onSaveEdit}
                          className="px-3 py-1.5 text-xs bg-blue-600 text-white rounded-lg hover:bg-blue-700"
                        >
                          저장
                        </button>
                      </div>
                    </div>
                  ) : section.user_edited_content !== null ? (
                    <div className="text-sm text-slate-800 leading-relaxed whitespace-pre-wrap">
                      {section.user_edited_content}
                    </div>
                  ) : (
                    <SegmentRenderer
                      ref={(el) => { segRendererRefs.current[section.section_id] = el; }}
                      segments={section.content_segments}
                      suggestions={section.inline_suggestions}
                      showAnchors={showAnchors}
                      onAnchorClick={(idx) => onAnchorClick(section.section_id, idx)}
                    />
                  )}

                  {/* 섹션 간 구분선 (그룹 내 마지막 아니면 표시) */}
                  {si < group.sections.length - 1 && (
                    <div className="mt-8 border-t border-dashed border-slate-100" />
                  )}
                </div>
              );
            })}
          </div>
        ))}

      </div>
    </div>
  );
});
DocumentPanel.displayName = "DocumentPanel";
export default DocumentPanel;
