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
  /** 벤치마크 유래 질문의 근거 문구 (합격작 N%가 언급) */
  evidence?: string;
  source?: "benchmark" | string;
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

/** 합격작 벤치마크 항목 — 실제 서류심사 통계 대비 이 문서의 보유 여부 */
export interface BenchmarkItem {
  feature: string;
  label: string;
  pass_pct: number;
  fail_pct: number;
  delta_pp: number;
  present: boolean;
}

export interface BenchmarkResult {
  available: boolean;
  reason?: string;
  program?: string;
  group?: string;
  /** empirical_rate: 점수대 실측 합격률 표기 허용 / distribution_position: 합격작 평균 대비 위치만 */
  display_mode?: "empirical_rate" | "distribution_position";
  score?: number;
  score_max?: number;
  pass_mean?: number;
  pass_median?: number;
  fail_mean?: number;
  band?: { band: string; score_min: number; score_max: number; n: number } | null;
  empirical_pass_rate_pct?: number | null;
  n_docs?: number;
  n_pass_docs?: number;
  gaps?: BenchmarkItem[];
  strengths?: BenchmarkItem[];
}

export interface SectionProgress {
  id: string;
  title: string;
  order: number;
  status: "pending" | "generating" | "done" | "error";
  confidence_level?: "green" | "yellow" | "red";
  completion_score?: number;
}
