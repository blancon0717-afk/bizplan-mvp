"use client";
import { create } from "zustand";
import type { Answer, Question } from "@/lib/types";
import { api } from "@/lib/api";

interface InterviewState {
  sessionId: string | null;
  programCode: string | null;
  questions: Question[];
  answers: Record<string, string>;
  currentIndex: number;
  editingQid: string | null;
  isSubmitting: boolean;

  setSession: (sessionId: string, programCode: string) => void;
  setQuestions: (questions: Question[]) => void;
  setCurrentIndex: (index: number) => void;
  startEditing: (qid: string) => void;
  stopEditing: () => void;
  saveAnswer: (qid: string, text: string) => Promise<void>;
  loadExistingAnswers: (answers: Record<string, Answer>) => void;
}

export const useInterviewStore = create<InterviewState>((set, get) => ({
  sessionId: null,
  programCode: null,
  questions: [],
  answers: {},
  currentIndex: 0,
  editingQid: null,
  isSubmitting: false,

  setSession: (sessionId, programCode) => set({ sessionId, programCode }),
  setQuestions: (questions) => set({ questions }),
  setCurrentIndex: (index) => set({ currentIndex: index }),
  startEditing: (qid) => set({ editingQid: qid }),
  stopEditing: () => set({ editingQid: null }),

  saveAnswer: async (qid, text) => {
    const { sessionId } = get();
    if (!sessionId) return;
    set((state) => ({
      answers: { ...state.answers, [qid]: text },
      isSubmitting: true,
    }));
    try {
      await api.saveAnswer(sessionId, qid, text);
    } finally {
      set({ isSubmitting: false });
    }
  },

  loadExistingAnswers: (answers) => {
    const mapped: Record<string, string> = {};
    for (const [qid, a] of Object.entries(answers)) {
      mapped[qid] = a.text;
    }
    set({ answers: mapped });
  },
}));
