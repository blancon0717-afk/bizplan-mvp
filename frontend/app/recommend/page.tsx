"use client";
import { useEffect, useState, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { useRecommendStore } from "@/store/recommendStore";
import ProgramCard from "@/components/program/ProgramCard";
import type { GapQuestion, Program } from "@/lib/types";

// ── 양식 변환(convert) 화면에서 고정 노출할 3개 지원사업 ──────────────
const CONVERT_TARGET_CODES = ["initial_package", "deeptech_academy", "innovation_voucher"] as const;
// 혁신바우처는 선택 시 바우처 서비스(컨설팅/기술지원/마케팅)를 추가로 고른다.
// maxFunding은 2026년 1차 공고 [일반 바우처] 분야별 정부지원금 최대 한도(고정값).
const VOUCHER_CODE = "innovation_voucher";
const VOUCHER_SERVICES = [
  { label: "컨설팅", maxFunding: "5,000만원" },
  { label: "기술지원", maxFunding: "3,000만원" },
  { label: "마케팅", maxFunding: "2,000만원" },
] as const;

function RecommendPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const mode = searchParams.get("mode");
  const sessionParam = searchParams.get("session");

  const { programs: storedPrograms, profile, setSessionId } = useRecommendStore();
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // convert mode state
  const [convertTargets, setConvertTargets] = useState<Program[]>([]);
  const [isLoadingPrograms, setIsLoadingPrograms] = useState(false);
  const [isConverting, setIsConverting] = useState(false);
  const [convertingName, setConvertingName] = useState("");
  const [convertDone, setConvertDone] = useState(0);
  const [convertTotal, setConvertTotal] = useState(0);
  const [convertStage, setConvertStage] = useState("");

  // 혁신바우처 서비스 선택 상태
  const [voucherOpen, setVoucherOpen] = useState(false);
  const [voucherServices, setVoucherServices] = useState<string[]>([]);

  // 갭 보완 인터뷰 상태 — 변환 전 고정 5문항 (선택 답변)
  const [gapTarget, setGapTarget] = useState<{
    code: string;
    name: string;
    voucherOptions?: string[];
  } | null>(null);
  const [gapQuestions, setGapQuestions] = useState<GapQuestion[]>([]);
  const [gapAnswers, setGapAnswers] = useState<Record<string, string>>({});
  const [isLoadingGap, setIsLoadingGap] = useState(false);

  const isConvertMode = mode === "convert";
  const [sessionId] = useState<string>(
    () =>
      sessionParam ??
      (typeof window !== "undefined" ? localStorage.getItem("bizplan_session_id") : "") ??
      ""
  );

  useEffect(() => {
    if (!isConvertMode && storedPrograms.length === 0) {
      router.replace("/");
    }
  }, [isConvertMode, storedPrograms, router]);

  // convert 모드: 양식 YAML 목록(getPrograms)에서 고정 3개만 순서대로 노출
  useEffect(() => {
    if (!isConvertMode) return;
    setIsLoadingPrograms(true);
    api
      .getPrograms()
      .then((res) => {
        const byCode = new Map(res.programs.map((p) => [p.code, p]));
        setConvertTargets(
          CONVERT_TARGET_CODES.map((c) => byCode.get(c)).filter(
            (p): p is Program => Boolean(p)
          )
        );
      })
      .catch(() => setError("지원사업 양식을 불러올 수 없습니다."))
      .finally(() => setIsLoadingPrograms(false));
  }, [isConvertMode]);

  function toggleVoucherService(service: string) {
    setVoucherServices((prev) =>
      prev.includes(service) ? prev.filter((s) => s !== service) : [...prev, service]
    );
  }

  async function handleWrite(programCode: string) {
    setIsStarting(true);
    setError(null);
    try {
      const { session_id } = await api.createSession(programCode);
      setSessionId(session_id);
      router.push(`/interview/${session_id}`);
    } catch {
      setError("세션을 생성할 수 없습니다. 다시 시도해주세요.");
      setIsStarting(false);
    }
  }

  // 변환 버튼 → 갭 보완 인터뷰(고정 5문항) 모달을 먼저 연다.
  // 질문이 없거나 조회에 실패하면 인터뷰 없이 바로 변환 진행(변환 자체를 막지 않음).
  async function openGapInterview(
    programCode: string,
    programName: string,
    voucherOptions?: string[]
  ) {
    setError(null);
    setIsLoadingGap(true);
    try {
      const res = await api.getGapQuestions(programCode, sessionId || undefined);
      if (!res.questions || res.questions.length === 0) {
        await handleConvert(programCode, programName, voucherOptions);
        return;
      }
      setGapQuestions(res.questions);
      setGapAnswers({});
      setGapTarget({ code: programCode, name: programName, voucherOptions });
    } catch {
      await handleConvert(programCode, programName, voucherOptions);
    } finally {
      setIsLoadingGap(false);
    }
  }

  async function handleConvert(
    programCode: string,
    programName: string,
    voucherOptions?: string[],
    answers?: Record<string, string>
  ) {
    if (!sessionId) {
      setError("세션 ID를 찾을 수 없습니다. 처음부터 다시 시도해주세요.");
      return;
    }
    setGapTarget(null);
    setIsConverting(true);
    setConvertingName(programName);
    setConvertDone(0);
    setConvertTotal(0);
    setConvertStage("변환 준비 중");

    // 공백 답변 제거 — 빈 인터뷰는 보내지 않는다
    const filledAnswers = Object.fromEntries(
      Object.entries(answers ?? {}).filter(([, v]) => v.trim() !== "")
    );

    let response: Response;
    try {
      response = await api.convertToForm(
        sessionId,
        programCode,
        voucherOptions,
        filledAnswers
      );
    } catch {
      setError("변환 요청에 실패했습니다. 다시 시도해주세요.");
      setIsConverting(false);
      return;
    }

    if (!response.ok) {
      setError("양식 변환에 실패했습니다.");
      setIsConverting(false);
      return;
    }

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let completed = false;

    function processChunk(chunk: string) {
      const lines = chunk.split("\n");
      const eventType = lines.find((l) => l.startsWith("event:"))?.slice(6).trim();
      const dataLine = lines.find((l) => l.startsWith("data:"))?.slice(5).trim();
      if (!eventType || !dataLine) return;
      try {
        const data = JSON.parse(dataLine);
        if (eventType === "init") {
          setConvertTotal((data.sections as { order: number }[]).length);
        } else if (eventType === "stage") {
          const stageLabels: Record<string, string> = {
            analyzing: "초안 내용 분석 중",
            mapping: "양식 구조에 매핑 중",
            converting: "섹션별 내용 작성 중",
            reviewing: "형식 검수·보정 중",
          };
          setConvertStage(stageLabels[data.stage as string] ?? "");
        } else if (eventType === "section_done") {
          setConvertDone((c) => c + 1);
        } else if (eventType === "all_done") {
          completed = true;
        } else if (eventType === "error") {
          setError(data.message ?? "변환 중 오류가 발생했습니다.");
          setIsConverting(false);
          completed = true;
        }
      } catch {
        /* skip malformed events */
      }
    }

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() ?? "";
        for (const chunk of chunks) processChunk(chunk);
      }
      if (buffer.trim()) processChunk(buffer);
    } catch {
      /* network error */
    }

    if (completed) {
      router.push(`/result/${sessionId}`);
    } else {
      setError("변환 중 오류가 발생했습니다. 다시 시도해주세요.");
      setIsConverting(false);
    }
  }

  const eligible = storedPrograms.filter((p) => p.is_eligible);

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 flex flex-col">
      {isConverting && (
        <div className="fixed inset-0 z-50 bg-white/90 backdrop-blur-sm flex flex-col items-center justify-center gap-4">
          <div className="w-10 h-10 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-slate-700 font-semibold text-lg">{convertingName} 양식으로 변환 중</p>
          {convertStage && (
            <p className="text-blue-600 text-sm font-medium">{convertStage}</p>
          )}
          {convertTotal > 0 && (
            <div className="w-72">
              <div className="h-2 bg-slate-200 rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-500 rounded-full transition-all duration-500"
                  style={{ width: `${Math.round((convertDone / Math.max(convertTotal, 1)) * 100)}%` }}
                />
              </div>
              <p className="text-slate-500 text-sm text-center mt-2">
                {convertDone}/{convertTotal} 섹션 완료
              </p>
            </div>
          )}
        </div>
      )}

      {/* 갭 보완 인터뷰 모달 — 변환 전 고정 5문항 (모두 선택 답변) */}
      {gapTarget && !isConverting && (
        <div className="fixed inset-0 z-40 bg-slate-900/40 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-xl max-w-lg w-full max-h-[85vh] flex flex-col">
            <div className="px-6 pt-5 pb-4 border-b border-slate-100">
              <h3 className="font-bold text-slate-900 text-lg">추가 인터뷰</h3>
              <p className="text-sm text-slate-500 mt-1 leading-relaxed">
                {gapTarget.name} 양식에 필요하지만 초안에 없는 정보입니다.
                아는 항목만 답해도 되고, 건너뛰어도 변환됩니다.
              </p>
            </div>
            <div className="flex-1 overflow-y-auto px-6 py-4 flex flex-col gap-4">
              {gapQuestions.map((q, qi) => (
                <div key={q.id}>
                  <label
                    htmlFor={`gap-${q.id}`}
                    className="block text-sm font-medium text-slate-800 mb-1.5"
                  >
                    {qi + 1}. {q.question}
                  </label>
                  <textarea
                    id={`gap-${q.id}`}
                    rows={2}
                    value={gapAnswers[q.id] ?? ""}
                    onChange={(e) =>
                      setGapAnswers((prev) => ({ ...prev, [q.id]: e.target.value }))
                    }
                    placeholder={q.hint}
                    className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-800 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y"
                  />
                </div>
              ))}
            </div>
            <div className="px-6 py-4 border-t border-slate-100 flex gap-2">
              <button
                onClick={() =>
                  handleConvert(gapTarget.code, gapTarget.name, gapTarget.voucherOptions)
                }
                className="flex-1 py-2.5 rounded-xl border border-slate-200 text-slate-600 text-sm font-semibold hover:bg-slate-50 transition-colors"
              >
                건너뛰고 변환
              </button>
              <button
                onClick={() =>
                  handleConvert(
                    gapTarget.code,
                    gapTarget.name,
                    gapTarget.voucherOptions,
                    gapAnswers
                  )
                }
                className="flex-1 py-2.5 rounded-xl bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 transition-colors"
              >
                답변 반영하여 변환 →
              </button>
            </div>
          </div>
        </div>
      )}

      <header className="px-6 py-4 border-b border-slate-200 bg-white/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="max-w-3xl mx-auto flex items-center gap-3">
          <button
            onClick={() => router.back()}
            className="w-8 h-8 rounded-lg bg-slate-100 hover:bg-slate-200 flex items-center justify-center text-slate-600 transition-colors"
            aria-label="뒤로"
          >
            ←
          </button>
          <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center text-white font-bold text-sm">
            AI
          </div>
          <span className="font-semibold text-slate-800 text-lg">
            {isConvertMode ? "지원사업 선택" : "지원사업 추천 결과"}
          </span>
        </div>
      </header>

      <main className="flex-1 px-4 py-10">
        <div className="max-w-3xl mx-auto">
          {isConvertMode && (
            <p className="text-slate-500 text-sm mb-6">
              아래 양식 중 하나를 선택하면 기본 초안을 해당 양식으로 자동 변환합니다.
            </p>
          )}

          {profile && !isConvertMode && (
            <div className="mb-6 p-4 bg-white rounded-xl border border-slate-200 text-sm text-slate-600 flex flex-wrap gap-3">
              <span className="font-medium text-slate-800">입력 조건:</span>
              <span>{profile.업력}</span>
              <span>·</span>
              <span>{profile.지역}</span>
              <span>·</span>
              <span>{profile.청년 ? "청년 창업자" : "일반"}</span>
              {profile.아이템 && (
                <>
                  <span>·</span>
                  <span className="truncate max-w-xs">{profile.아이템}</span>
                </>
              )}
            </div>
          )}

          {error && (
            <div className="mb-6 p-4 rounded-xl bg-red-50 border border-red-200 text-red-700 text-sm">
              {error}
            </div>
          )}

          {isLoadingPrograms && (
            <div className="flex items-center justify-center py-20">
              <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
            </div>
          )}

          {!isConvertMode && eligible.length > 0 && (
            <section className="mb-8">
              <h2 className="text-lg font-bold text-slate-900 mb-4">
                지원 가능한 사업{" "}
                <span className="text-blue-600">{eligible.length}개</span>
              </h2>
              <div className="space-y-4">
                {eligible.map((p) => (
                  <ProgramCard
                    key={p.name}
                    variant="recommend"
                    program={p}
                    onWrite={handleWrite}
                    isStarting={isStarting}
                  />
                ))}
              </div>
            </section>
          )}

          {isConvertMode && !isLoadingPrograms && convertTargets.length > 0 && (
            <div className="space-y-4">
              {convertTargets.map((p) => {
                const isVoucher = p.code === VOUCHER_CODE;
                const expanded = isVoucher && voucherOpen;
                return (
                  <div
                    key={p.code}
                    className="bg-white rounded-2xl border border-slate-200 p-5 flex flex-col gap-3 shadow-sm"
                  >
                    <div>
                      <h3 className="font-semibold text-slate-900 text-base leading-snug">
                        {p.name}
                      </h3>
                      {p.target && (
                        <p className="text-sm text-slate-600 mt-1 leading-relaxed">{p.target}</p>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {p.max_funding && (
                        <span className="text-xs px-2 py-1 rounded-lg bg-slate-100 text-slate-600">
                          최대 {p.max_funding}
                        </span>
                      )}
                      <span className="text-xs px-2 py-1 rounded-lg bg-slate-100 text-slate-600">
                        {p.section_count}개 섹션
                      </span>
                    </div>

                    {/* 혁신바우처: 바우처 서비스 선택 패널 */}
                    {expanded && (
                      <div className="rounded-xl bg-slate-50 border border-slate-200 p-4 flex flex-col gap-3">
                        <p className="text-sm font-medium text-slate-700">
                          신청할 바우처 서비스를 선택하세요 (복수 선택 가능)
                        </p>
                        <div className="flex flex-col gap-2">
                          {VOUCHER_SERVICES.map((service) => (
                            <label
                              key={service.label}
                              className="flex items-center justify-between gap-2.5 text-sm text-slate-700 cursor-pointer"
                            >
                              <span className="flex items-center gap-2.5">
                                <input
                                  type="checkbox"
                                  checked={voucherServices.includes(service.label)}
                                  onChange={() => toggleVoucherService(service.label)}
                                  className="w-4 h-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                                />
                                {service.label}
                              </span>
                              <span className="text-xs text-slate-400">최대 {service.maxFunding}</span>
                            </label>
                          ))}
                        </div>
                      </div>
                    )}

                    {!isVoucher && (
                      <button
                        onClick={() => openGapInterview(p.code, p.name)}
                        disabled={isConverting || isLoadingGap}
                        className="w-full py-2.5 rounded-xl bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                      >
                        이 양식으로 변환하기 →
                      </button>
                    )}

                    {isVoucher && !expanded && (
                      <button
                        onClick={() => setVoucherOpen(true)}
                        disabled={isConverting}
                        className="w-full py-2.5 rounded-xl bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                      >
                        이 양식으로 변환하기 →
                      </button>
                    )}

                    {isVoucher && expanded && (
                      <button
                        onClick={() => openGapInterview(p.code, p.name, voucherServices)}
                        disabled={isConverting || isLoadingGap || voucherServices.length === 0}
                        className="w-full py-2.5 rounded-xl bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                      >
                        {voucherServices.length === 0
                          ? "서비스를 1개 이상 선택하세요"
                          : "선택한 서비스로 변환하기 →"}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {!isConvertMode && storedPrograms.length === 0 && !isLoadingPrograms && (
            <div className="text-center py-20 text-slate-400">추천 결과가 없습니다.</div>
          )}

          {isConvertMode && !isLoadingPrograms && convertTargets.length === 0 && !error && (
            <div className="text-center py-20 text-slate-400">지원 가능한 양식이 없습니다.</div>
          )}
        </div>
      </main>

      <footer className="py-4 text-center text-xs text-slate-400 border-t border-slate-100">
        사업계획서 AI MVP
      </footer>
    </div>
  );
}

export default function RecommendPage() {
  return (
    <Suspense
      fallback={
        <div className="h-screen flex items-center justify-center">
          <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      }
    >
      <RecommendPageInner />
    </Suspense>
  );
}
