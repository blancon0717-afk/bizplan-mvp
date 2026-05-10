"use client";
import type { Program, SupportProgramMatch } from "@/lib/types";

// ── 홈페이지 프로그램 선택 카드 ──────────────────────────────
const PROGRAM_COLORS: Record<string, { bg: string; accent: string; icon: string }> = {
  initial_package: { bg: "bg-blue-50", accent: "border-blue-400 bg-blue-600", icon: "🚀" },
  youth_academy: { bg: "bg-violet-50", accent: "border-violet-400 bg-violet-600", icon: "⭐" },
  jumping_package: { bg: "bg-emerald-50", accent: "border-emerald-400 bg-emerald-600", icon: "📈" },
  comeback_package: { bg: "bg-orange-50", accent: "border-orange-400 bg-orange-600", icon: "🔄" },
  changjungdae: { bg: "bg-teal-50", accent: "border-teal-400 bg-teal-600", icon: "🏛️" },
};
const DEFAULT_COLOR = { bg: "bg-slate-50", accent: "border-slate-400 bg-slate-600", icon: "📋" };

function formatAmount(amount: number): string | null {
  if (amount === 0) return null;
  if (amount >= 10000) return `최대 ${amount / 10000}억원`;
  return `최대 ${amount.toLocaleString()}만원`;
}

// ── Props 타입 ────────────────────────────────────────────────
type SelectProps = {
  variant: "select";
  program: Program;
  selected: boolean;
  onClick: () => void;
};

type RecommendProps = {
  variant: "recommend";
  program: SupportProgramMatch;
  onWrite: (code: string) => void;
  isStarting: boolean;
};

type ProgramCardProps = SelectProps | RecommendProps;

// ── 컴포넌트 ─────────────────────────────────────────────────
export default function ProgramCard(props: ProgramCardProps) {
  if (props.variant === "select") {
    const { program, selected, onClick } = props;
    const colors = PROGRAM_COLORS[program.code] ?? DEFAULT_COLOR;
    const [accentBorder, accentBg] = colors.accent.split(" ");

    return (
      <button
        onClick={onClick}
        className={`
          w-full text-left rounded-xl border-2 p-5 transition-all duration-200 cursor-pointer
          ${selected
            ? `${accentBorder} ${colors.bg} shadow-md scale-[1.02]`
            : "border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm hover:scale-[1.01]"
          }
        `}
      >
        <div className="flex items-start gap-4">
          <div className={`flex-shrink-0 w-11 h-11 rounded-xl ${accentBg} flex items-center justify-center text-xl shadow-sm`}>
            {colors.icon}
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="font-semibold text-slate-900 text-sm leading-snug mb-1">
              {program.name}
            </h3>
            <p className="text-xs text-slate-500 mb-3 line-clamp-2">{program.target}</p>
            <div className="flex items-center gap-3 flex-wrap">
              <span className="inline-flex items-center gap-1 text-xs font-medium text-slate-700 bg-slate-100 rounded-md px-2 py-0.5">
                💰 최대 {program.max_funding}
              </span>
              <span className="inline-flex items-center gap-1 text-xs font-medium text-slate-700 bg-slate-100 rounded-md px-2 py-0.5">
                📄 {program.section_count}개 섹션
              </span>
            </div>
          </div>
          {selected && (
            <div className="flex-shrink-0 w-5 h-5 rounded-full bg-blue-600 flex items-center justify-center">
              <svg className="w-3 h-3 text-white" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
              </svg>
            </div>
          )}
        </div>
      </button>
    );
  }

  // variant === "recommend"
  const { program, onWrite, isStarting } = props;
  const amount = formatAmount(program.최대지원금액_만원);
  const FORMS_AVAILABLE = [
    "initial_package",
    "initial_package_deeptech",
    "youth_academy",
    "jumping_package",
    "jumping_package_deeptech",
    "comeback_package",
    "changjungdae",
    "deeptech_academy",
  ];
  const hasForm = FORMS_AVAILABLE.includes(program.program_code);

  return (
    <div
      className={`bg-white rounded-2xl border p-5 flex flex-col gap-3 transition-all ${
        program.is_eligible ? "border-slate-200 shadow-sm" : "border-slate-100 opacity-60"
      }`}
    >
      <div>
        {program.is_eligible && (
          <span className="inline-block text-xs font-medium px-2 py-0.5 rounded-full bg-blue-50 text-blue-600 mb-1.5">
            지원 가능
          </span>
        )}
        <h3 className="font-semibold text-slate-900 text-base leading-snug">{program.name}</h3>
      </div>

      {program.설명 && (
        <p className="text-sm text-slate-600 leading-relaxed">{program.설명}</p>
      )}

      <div className="flex flex-wrap gap-1.5">
        {program.지역 && (
          <span className="text-xs px-2 py-1 rounded-lg bg-slate-100 text-slate-600">{program.지역}</span>
        )}
        {amount && (
          <span className="text-xs px-2 py-1 rounded-lg bg-slate-100 text-slate-600">{amount}</span>
        )}
        {program.지원시기.slice(0, 3).map((t) => (
          <span key={t} className="text-xs px-2 py-1 rounded-lg bg-slate-100 text-slate-600">{t}</span>
        ))}
      </div>

      {program.match_reasons.length > 0 && (
        <ul className="space-y-0.5">
          {program.match_reasons.slice(0, 3).map((r, i) => (
            <li key={i} className="text-xs text-slate-500 flex items-center gap-1.5">
              <span className="w-1 h-1 rounded-full bg-blue-400 shrink-0" />
              {r}
            </li>
          ))}
        </ul>
      )}

      {program.is_eligible && (
        <div className="mt-1">
          <button
            onClick={() => {
              if (hasForm && !isStarting) onWrite(program.program_code);
            }}
            disabled={isStarting || !hasForm}
            className={`w-full py-2.5 rounded-xl text-sm font-semibold transition-all ${
              hasForm
                ? "bg-blue-600 text-white hover:bg-blue-700 active:scale-[0.99] disabled:opacity-50 disabled:cursor-not-allowed"
                : "bg-gray-200 text-gray-400 cursor-not-allowed"
            }`}
          >
            {!hasForm ? "준비 중" : isStarting ? "시작 중..." : "사업계획서 작성하러 가기 →"}
          </button>
        </div>
      )}
    </div>
  );
}
