"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import type { CompanyProfile } from "@/lib/types";
import { api } from "@/lib/api";
import { useRecommendStore } from "@/store/recommendStore";

const STAGE_OPTIONS: { value: CompanyProfile["업력"]; label: string; desc: string }[] = [
  { value: "예비", label: "예비창업자", desc: "사업자 미등록" },
  { value: "초기", label: "초기 (0~3년)", desc: "사업자 등록 후 3년 미만" },
  { value: "도약", label: "도약 (3~7년)", desc: "창업 3~7년" },
  { value: "장기", label: "장기 (7년+)", desc: "창업 7년 이상" },
];

const REGION_OPTIONS: { value: CompanyProfile["지역"]; label: string }[] = [
  { value: "수도권", label: "수도권 (서울·경기·인천)" },
  { value: "비수도권", label: "비수도권 (지방)" },
  { value: "무관", label: "무관 / 모르겠음" },
];

export default function HomePage() {
  const router = useRouter();
  const { setResults, setSessionId } = useRecommendStore();
  const [profile, setProfile] = useState<CompanyProfile>({
    업력: "초기",
    아이템: "",
    청년: false,
    지역: "무관",
  });
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [planFile, setPlanFile] = useState<File | null>(null);
  const [savedSessionId, setSavedSessionId] = useState<string | null>(null);
  const [hasCompletedPlan, setHasCompletedPlan] = useState(false);
  const [resumeTarget, setResumeTarget] = useState<"result" | "draft">("draft");

  useEffect(() => {
    const saved = useRecommendStore.getState().profile;
    if (saved) setProfile(saved);
    const savedId = localStorage.getItem("bizplan_session_id");
    if (savedId) {
      // 변환 결과가 있으면 최종 결과로, 초안만 있으면 초안 검토 화면으로 이어하기
      (async () => {
        try {
          const r = await fetch(`/api/sessions/${savedId}/results`);
          if (r.ok) {
            setSavedSessionId(savedId);
            setResumeTarget("result");
            setHasCompletedPlan(true);
            return;
          }
          const f = await fetch(`/api/sessions/${savedId}/framework`);
          if (f.ok) {
            setSavedSessionId(savedId);
            setResumeTarget("draft");
            setHasCompletedPlan(true);
          }
        } catch { /* 배너 미표시 */ }
      })();
    }
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!profile.아이템.trim()) {
      setError("아이템/서비스 설명을 입력해주세요.");
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const res = await api.createSession("none");
      const sessionId = res.session_id;
      localStorage.setItem("bizplan_session_id", sessionId);
      setResults([], profile);
      setSessionId(sessionId);

      // 보유 사업계획서 PDF 업로드 시: 인터뷰 답변 사전 채움 후 보완 인터뷰로 이동
      if (planFile) {
        try {
          const up = await api.uploadPlan(sessionId, planFile);
          if (!up.ok) {
            // 스캔본 등 텍스트 추출 실패 → 파일 없이 일반 인터뷰로 유도
            setError(
              "업로드한 PDF에서 텍스트를 인식하지 못했습니다(스캔본일 수 있습니다). 파일 없이 '인터뷰 시작하기'로 진행하거나 텍스트 PDF를 선택해주세요."
            );
            setPlanFile(null);
            setIsLoading(false);
            return;
          }
        } catch (err) {
          setError(err instanceof Error ? err.message : "PDF 업로드에 실패했습니다.");
          setIsLoading(false);
          return;
        }
      }

      router.push(`/interview/${sessionId}`);
    } catch {
      setError("서버 연결에 실패했습니다. 백엔드 서버를 확인해주세요.");
      setIsLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 flex flex-col">
      <header className="px-6 py-4 border-b border-slate-200 bg-white/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="max-w-2xl mx-auto flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center text-white font-bold text-sm">
            AI
          </div>
          <span className="font-semibold text-slate-800 text-lg">사업계획서 AI</span>
        </div>
      </header>

      {hasCompletedPlan && (
        <div className="bg-blue-50 border-b border-blue-200 px-4 py-3">
          <div className="max-w-2xl mx-auto flex items-center justify-between gap-4">
            <p className="text-sm text-blue-700 font-medium">이전에 작성한 사업계획서가 있습니다.</p>
            <div className="flex items-center gap-2 flex-shrink-0">
              <button
                onClick={() => router.push(`/${resumeTarget}/${savedSessionId}`)}
                className="px-4 py-1.5 bg-blue-600 text-white text-xs font-semibold rounded-lg hover:bg-blue-700 transition-colors"
              >
                {resumeTarget === "result" ? "최종 결과 보기" : "초안 보기"}
              </button>
              <button
                disabled
                className="px-4 py-1.5 bg-white text-slate-500 text-xs font-semibold rounded-lg border border-slate-200 opacity-50 cursor-not-allowed transition-colors"
              >
                새로 시작하기
              </button>
            </div>
          </div>
        </div>
      )}

      <main className="flex-1 flex flex-col items-center justify-center px-4 py-12">
        <div className="w-full max-w-2xl">
          <div className="text-center mb-10">
            <h1 className="text-3xl font-bold text-slate-900 mb-3 tracking-tight">
              기업 정보를 입력해주세요
            </h1>
            <p className="text-slate-500 text-base">
              정보를 입력하면 AI가 사업계획서 초안을 작성합니다
            </p>
          </div>

          {error && (
            <div className="mb-6 p-4 rounded-xl bg-red-50 border border-red-200 text-red-700 text-sm text-center">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="bg-white rounded-2xl shadow-sm border border-slate-200 p-8 space-y-8">
            {/* 업력 */}
            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-3">
                업력 <span className="text-red-500">*</span>
              </label>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {STAGE_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setProfile((p) => ({ ...p, 업력: opt.value }))}
                    className={`p-3 rounded-xl border-2 text-left transition-all ${
                      profile.업력 === opt.value
                        ? "border-blue-600 bg-blue-50"
                        : "border-slate-200 hover:border-slate-300"
                    }`}
                  >
                    <div className={`text-sm font-semibold ${profile.업력 === opt.value ? "text-blue-700" : "text-slate-700"}`}>
                      {opt.label}
                    </div>
                    <div className="text-xs text-slate-400 mt-0.5">{opt.desc}</div>
                  </button>
                ))}
              </div>
            </div>

            {/* 아이템 */}
            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-2">
                아이템 / 서비스 설명 <span className="text-red-500">*</span>
              </label>
              <textarea
                value={profile.아이템}
                onChange={(e) => setProfile((p) => ({ ...p, 아이템: e.target.value }))}
                placeholder="예: AI 기반 헬스케어 앱, 친환경 포장재 제조, 소상공인 전용 POS 솔루션..."
                rows={3}
                className="w-full rounded-xl border border-slate-300 px-4 py-3 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none"
              />
            </div>

            {/* 청년 여부 */}
            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-3">
                청년 창업자 여부
              </label>
              <button
                type="button"
                onClick={() => setProfile((p) => ({ ...p, 청년: !p.청년 }))}
                className={`flex items-center gap-3 px-5 py-3 rounded-xl border-2 transition-all ${
                  profile.청년
                    ? "border-blue-600 bg-blue-50"
                    : "border-slate-200 hover:border-slate-300"
                }`}
              >
                <div className={`w-5 h-5 rounded-md border-2 flex items-center justify-center transition-all ${
                  profile.청년 ? "border-blue-600 bg-blue-600" : "border-slate-300"
                }`}>
                  {profile.청년 && (
                    <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 12 12" stroke="currentColor" strokeWidth={2.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M2 6l3 3 5-5" />
                    </svg>
                  )}
                </div>
                <span className={`text-sm font-medium ${profile.청년 ? "text-blue-700" : "text-slate-600"}`}>
                  만 39세 이하 청년 창업자입니다
                </span>
              </button>
            </div>

            {/* 지역 */}
            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-3">
                사업장 위치
              </label>
              <div className="flex flex-col gap-2">
                {REGION_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setProfile((p) => ({ ...p, 지역: opt.value }))}
                    className={`px-4 py-3 rounded-xl border-2 text-left text-sm font-medium transition-all ${
                      profile.지역 === opt.value
                        ? "border-blue-600 bg-blue-50 text-blue-700"
                        : "border-slate-200 text-slate-600 hover:border-slate-300"
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            {/* 보유 사업계획서 PDF 업로드 (선택) */}
            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-2">
                보유 사업계획서 업로드{" "}
                <span className="text-slate-400 font-normal">(선택 · PDF)</span>
              </label>
              <p className="text-xs text-slate-400 mb-2 leading-relaxed">
                기존 사업계획서 PDF가 있으면 업로드하세요. 내용을 분석해 인터뷰를 자동으로 채우고,
                부족한 항목만 질문합니다. (텍스트 추출이 가능한 PDF만 지원)
              </p>
              <input
                type="file"
                accept="application/pdf,.pdf"
                onChange={(e) => setPlanFile(e.target.files?.[0] ?? null)}
                className="block w-full text-sm text-slate-600 file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-semibold file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100 cursor-pointer"
              />
              {planFile && (
                <p className="mt-1.5 text-xs text-slate-500">선택됨: {planFile.name}</p>
              )}
            </div>

            <button
              type="submit"
              disabled={isLoading}
              className="w-full py-4 rounded-xl font-semibold text-base bg-blue-600 text-white hover:bg-blue-700 transition-colors disabled:bg-slate-300 disabled:text-slate-500 disabled:cursor-not-allowed"
            >
              {isLoading
                ? planFile
                  ? "PDF 분석 중..."
                  : "준비 중..."
                : planFile
                ? "PDF 분석 후 계속 →"
                : "인터뷰 시작하기 →"}
            </button>
          </form>
        </div>
      </main>

      <footer className="py-4 text-center text-xs text-slate-400 border-t border-slate-100">
        사업계획서 AI MVP
      </footer>
    </div>
  );
}
