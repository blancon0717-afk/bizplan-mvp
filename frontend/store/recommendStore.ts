"use client";
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { CompanyProfile, SupportProgramMatch } from "@/lib/types";

interface RecommendState {
  programs: SupportProgramMatch[];
  profile: CompanyProfile | null;
  sessionId: string | null;
  maxStep: number;
  setResults: (programs: SupportProgramMatch[], profile: CompanyProfile) => void;
  setSessionId: (id: string) => void;
  advanceStep: (step: number) => void;
}

export const useRecommendStore = create<RecommendState>()(
  persist(
    (set, get) => ({
      programs: [],
      profile: null,
      sessionId: null,
      maxStep: 1,
      setResults: (programs, profile) => set({ programs, profile }),
      setSessionId: (id) => set({ sessionId: id, maxStep: 3 }),
      advanceStep: (step) => set({ maxStep: Math.max(get().maxStep, step) }),
    }),
    {
      name: "bizplan-session",
      storage: createJSONStorage(() => localStorage),
    }
  )
);
