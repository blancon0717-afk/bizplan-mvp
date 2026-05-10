"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useRecommendStore } from "@/store/recommendStore";
import ProgramCard from "@/components/program/ProgramCard";

export default function RecommendPage() {
  const router = useRouter();
  const { programs, profile } = useRecommendStore();
  const setSessionId = useRecommendStore((s) => s.setSessionId);
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (programs.length === 0) {
      router.replace("/");
    }
  }, [programs, router]);

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

  const eligible = programs.filter((p) => p.is_eligible);
  const ineligible = programs.filter((p) => !p.is_eligible);

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50 flex flex-col">
      <header className="px-6 py-4 border-b border-slate-200 bg-white/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="max-w-3xl mx-auto flex items-center gap-3">
          <button
            onClick={() => router.push("/")}
            className="w-8 h-8 rounded-lg bg-slate-100 hover:bg-slate-200 flex items-center justify-center text-slate-600 transition-colors"
            aria-label="뒤로"
          >
            ←
          </button>
          <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center text-white font-bold text-sm">
            AI
          </div>
          <span className="font-semibold text-slate-800 text-lg">지원사업 추천 결과</span>
        </div>
      </header>

      <main className="flex-1 px-4 py-10">
        <div className="max-w-3xl mx-auto">
          {profile && (
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

          {eligible.length > 0 && (
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

          {programs.length === 0 && (
            <div className="text-center py-20 text-slate-400">
              추천 결과가 없습니다.
            </div>
          )}
        </div>
      </main>

      <footer className="py-4 text-center text-xs text-slate-400 border-t border-slate-100">
        사업계획서 AI MVP
      </footer>
    </div>
  );
}
