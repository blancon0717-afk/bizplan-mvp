import type {
  Answer,
  CompanyProfile,
  GapQuestion,
  GenerationResults,
  Program,
  Question,
  RubricScoreResult,
  SectionResult,
  SupportProgramMatch,
} from "@/lib/types";

const API_BASE = "/api";

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const err = await res
      .json()
      .catch(() => ({ detail: res.statusText }));
    throw new Error(
      typeof err.detail === "string" ? err.detail : "Request failed"
    );
  }
  return res.json() as Promise<T>;
}

export const api = {
  getPrograms: () => request<{ programs: Program[] }>("/programs"),

  createSession: (program_code: string) =>
    request<{ session_id: string; program_code: string }>("/sessions", {
      method: "POST",
      body: JSON.stringify({ program_code }),
    }),

  getSession: (sessionId: string) =>
    request<{
      session_id: string;
      program_code: string;
      answers: Record<string, Answer>;
      has_results: boolean;
    }>(`/sessions/${sessionId}`),

  getSections: (sessionId: string) =>
    request<{ sections: { id: string; title: string; order: number }[] }>(`/sessions/${sessionId}/sections`),

  getQuestions: () =>
    request<{ questions: Question[] }>("/interview/questions"),

  saveAnswer: (sessionId: string, qid: string, text: string) =>
    request<Answer>(`/sessions/${sessionId}/answers/${qid}`, {
      method: "PUT",
      body: JSON.stringify({ text }),
    }),

  getResults: (sessionId: string) =>
    request<GenerationResults>(`/sessions/${sessionId}/results`),

  updateMemo: (
    sessionId: string,
    sectionId: string,
    memoIndex: number,
    response: string
  ) =>
    request<{
      overall_completion: number;
      effective_completion_score: number;
    }>(
      `/sessions/${sessionId}/results/${sectionId}/memo/${memoIndex}`,
      { method: "PUT", body: JSON.stringify({ response }) }
    ),

  editSection: (sessionId: string, sectionId: string, content: string) =>
    request<{ saved: boolean }>(
      `/sessions/${sessionId}/results/${sectionId}/edit`,
      { method: "PUT", body: JSON.stringify({ content }) }
    ),

  regenerateSection: (
    sessionId: string,
    sectionId: string,
    memoResponse?: string,
    memoIndex?: number
  ) =>
    request<{ section: SectionResult; overall_completion: number }>(
      `/sessions/${sessionId}/results/${sectionId}/regenerate`,
      {
        method: "POST",
        body: JSON.stringify({
          memo_response: memoResponse ?? null,
          memo_index: memoIndex ?? null,
        }),
      }
    ),

  getScore: (sessionId: string) =>
    request<RubricScoreResult>(`/sessions/${sessionId}/score`),

  recommend: (profile: CompanyProfile) =>
    request<{ programs: SupportProgramMatch[] }>("/matching/recommend", {
      method: "POST",
      body: JSON.stringify(profile),
    }),

  loadTestSession: (program_code: string) =>
    request<{ session_id: string; program_code: string; answers_loaded: number }>(
      "/dev/load-test-session",
      { method: "POST", body: JSON.stringify({ program_code }) }
    ),

  getActionPlan: (sessionId: string) =>
    request<{ action_plan: string }>(`/sessions/${sessionId}/action-plan`, { method: "POST" }),

  getDocumentCheck: (sessionId: string) =>
    request<{ result: string }>(`/sessions/${sessionId}/document-check`, { method: "POST" }),

  getUsage: (sessionId: string) =>
    request<Record<string, { used: number; max: number }>>(`/sessions/${sessionId}/usage`),

  generateFramework: (sessionId: string) =>
    fetch(`${API_BASE}/sessions/${sessionId}/generate_framework`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    }),

  getFramework: (sessionId: string) =>
    request<GenerationResults>(`/sessions/${sessionId}/framework`),

  convertToForm: (
    sessionId: string,
    program_code: string,
    voucher_options?: string[],
    gap_answers?: Record<string, string>
  ) =>
    fetch(`${API_BASE}/sessions/${sessionId}/convert_to_form`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        program_code,
        ...(voucher_options && voucher_options.length > 0 ? { voucher_options } : {}),
        ...(gap_answers && Object.keys(gap_answers).length > 0 ? { gap_answers } : {}),
      }),
    }),

  // 양식 변환 전 갭 보완 인터뷰 질문 조회.
  // sessionId를 넘기면 초안이 이미 커버하는 질문은 서버에서 제외(방안 A 필터).
  getGapQuestions: (programCode: string, sessionId?: string) =>
    request<{ program_code: string; questions: GapQuestion[] }>(
      `/forms/${programCode}/gap_questions` +
        (sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "")
    ),

  // DOCX 다운로드 전 리드 이메일 수집
  submitLead: (sessionId: string, email: string) =>
    request<{ ok: boolean }>(`/sessions/${sessionId}/lead`, {
      method: "POST",
      body: JSON.stringify({ email }),
    }),

  // 기존 사업계획서 PDF 업로드 → 인터뷰 답변 사전 채움.
  // multipart이므로 Content-Type을 직접 지정하지 않는다(브라우저가 boundary 설정).
  // ok=false + reason="no_text"이면 스캔본 등 추출 실패 → 일반 인터뷰로 유도.
  uploadPlan: async (sessionId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${API_BASE}/sessions/${sessionId}/upload_plan`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const err = await res
        .json()
        .catch(() => ({ detail: res.statusText }));
      throw new Error(
        typeof err.detail === "string" ? err.detail : "PDF 업로드에 실패했습니다."
      );
    }
    return res.json() as Promise<{
      ok: boolean;
      reason?: string;
      filled_qids?: string[];
      empty_qids?: string[];
      filled?: number;
      total?: number;
      text_chars?: number;
    }>;
  },
};
