"use client";
import { create } from "zustand";
import type { SectionResult, InlineSuggestion } from "@/lib/types";
import { api } from "@/lib/api";

interface ResultState {
  sections: SectionResult[];
  overallCompletion: number;
  localProbPct: number;
  activeSectionId: string | null;
  isRegenerating: Record<string, boolean>;

  init: (sessionId: string, sections: SectionResult[], overall: number) => void;
  setActiveSectionId: (id: string) => void;
  updateMemoResponse: (
    sessionId: string,
    sectionId: string,
    memoIndex: number,
    response: string
  ) => Promise<void>;
  updateSectionAfterRegen: (section: SectionResult, overall: number) => void;
  updateSectionSuggestions: (sectionId: string, suggestions: InlineSuggestion[]) => void;
  editSection: (sessionId: string, sectionId: string, content: string) => Promise<void>;
  regenerateSection: (sessionId: string, sectionId: string, memoResponse?: string, memoIndex?: number) => Promise<void>;
  syncProbPct: (pct: number) => void;
}

function recomputeEffective(section: SectionResult): number {
  const base = Math.max(0, Math.min(100, section.completion_score));
  const total = section.inline_suggestions.length;
  if (total === 0) return base;
  const resolved = section.inline_suggestions.filter(
    (s) => s.response.trim()
  ).length;
  return Math.round(base + (100 - base) * (resolved / total));
}

function computeLocalProbPct(overallCompletion: number): number {
  return Math.round(overallCompletion * 0.85);
}

function computeOverall(sections: SectionResult[]): number {
  if (!sections.length) return 0;
  const sum = sections.reduce(
    (acc, s) => acc + recomputeEffective(s),
    0
  );
  return Math.round(sum / sections.length);
}

export const useResultStore = create<ResultState>((set, get) => ({
  sections: [],
  overallCompletion: 0,
  localProbPct: 0,
  activeSectionId: null,
  isRegenerating: {},

  init: (_sessionId, sections, overall) =>
    set({
      sections,
      overallCompletion: overall,
      localProbPct: computeLocalProbPct(overall),
      activeSectionId: sections[0]?.section_id ?? null,
      isRegenerating: {},
    }),

  setActiveSectionId: (id) => set({ activeSectionId: id }),

  updateMemoResponse: async (sessionId, sectionId, memoIndex, response) => {
    const { sections } = get();
    const updated = sections.map((s) => {
      if (s.section_id !== sectionId) return s;
      const suggestions = s.inline_suggestions.map((m, i) =>
        i === memoIndex ? { ...m, response } : m
      );
      return { ...s, inline_suggestions: suggestions };
    });
    set({ sections: updated });
    await api.updateMemo(sessionId, sectionId, memoIndex, response).catch(() => {});
  },

  updateSectionAfterRegen: (section, overall) => {
    set((state) => ({
      sections: state.sections.map((s) =>
        s.section_id === section.section_id
          ? { ...section, inline_suggestions: s.inline_suggestions }
          : s
      ),
      overallCompletion: overall,
      localProbPct: computeLocalProbPct(overall),
    }));
  },

  updateSectionSuggestions: (sectionId, suggestions) => {
    set((state) => ({
      sections: state.sections.map((s) =>
        s.section_id === sectionId
          ? { ...s, inline_suggestions: suggestions }
          : s
      ),
    }));
  },

  editSection: async (sessionId, sectionId, content) => {
    await api.editSection(sessionId, sectionId, content);
    set((state) => ({
      sections: state.sections.map((s) =>
        s.section_id === sectionId
          ? { ...s, user_edited_content: content || null }
          : s
      ),
    }));
  },

  regenerateSection: async (sessionId, sectionId, memoResponse, memoIndex) => {
    set((state) => ({
      isRegenerating: { ...state.isRegenerating, [sectionId]: true },
    }));
    try {
      const { section, overall_completion } = await api.regenerateSection(sessionId, sectionId, memoResponse, memoIndex);
      get().updateSectionAfterRegen(section, overall_completion);
    } finally {
      set((state) => ({
        isRegenerating: { ...state.isRegenerating, [sectionId]: false },
      }));
    }
  },

  syncProbPct: (pct: number) => set({ localProbPct: pct }),
}));
