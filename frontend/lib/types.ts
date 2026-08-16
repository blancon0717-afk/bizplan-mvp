export interface CompanyProfile {
  업력: "예비" | "초기" | "도약" | "장기";
  아이템: string;
  청년: boolean;
  지역: "수도권" | "비수도권" | "무관";
}

export interface SupportProgramMatch {
  name: string;
  연차: string[];
  특화분야: string[];
  지역: string;
  최대지원금액_만원: number;
  지원시기: string[];
  상태: string;
  program_code: string;
  설명: string;
  has_form: boolean;
  score: number;
  match_reasons: string[];
  is_eligible: boolean;
}

export interface Program {
  code: string;
  name: string;
  target: string;
  max_funding: string;
  page_limit: number;
  section_count: number;
  notes?: string;
}

// 양식 변환 전 갭 보완 인터뷰 고정 질문 (양식 YAML gap_questions)
export interface GapQuestion {
  id: string;
  question: string;
  hint: string;
  target_sections: string[];
}

export interface Question {
  qid: string;
  section: string;
  category: string;
  branch: string;
  text: string;
  hint: string;
  tags: string[];
}

export interface Answer {
  qid: string;
  text: string;
  updated_at?: string;
}

export interface ContentSegment {
  text: string;
  source: "user_answer" | "llm_inferred";
  source_qids: string[];
}

export interface InlineSuggestion {
  anchor_text: string;
  note: string;
  severity: "critical" | "warning" | "info";
  response: string;
}

export interface SectionResult {
  section_id: string;
  section_title: string;
  content: string;
  confidence_level: "green" | "yellow" | "red";
  reasoning: string;
  used_answer_ids: string[];
  missing_info: string[];
  inline_suggestions: InlineSuggestion[];
  content_segments: ContentSegment[];
  user_edited_content: string | null;
  rubric_check: Record<string, boolean>;
  llm_meta: Record<string, unknown>;
  completion_score: number;
  completion_reasoning: string;
  effective_completion_score: number;
  resolved_memo_count: number;
  category: string;
  truncated: boolean;
  /** 미결제 세션의 잠긴 섹션 — content 대신 preview만 내려옴 */
  locked?: boolean;
  /** 잠긴 섹션의 티저 텍스트 (앞 ~120자) */
  preview?: string;
}

export interface GenerationResults {
  overall_completion: number;
  sections: SectionResult[];
  /** 결제(언락) 완료 여부 — framework 응답에만 존재 */
  unlocked?: boolean;
}

export interface RubricFeature {
  feature: string;
  direction: string;
}

export interface RubricScoreResult {
  available: boolean;
  prob_pct?: number;
  base_rate_pct?: number;
  hits?: RubricFeature[];
}

export interface SectionProgress {
  id: string;
  title: string;
  order: number;
  status: "pending" | "generating" | "done" | "error";
  confidence_level?: "green" | "yellow" | "red";
  completion_score?: number;
}
