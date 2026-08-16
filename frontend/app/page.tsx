import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "완성 말고, 합격. | 사업계획서 AI",
  description:
    "정부지원사업 사업계획서를 심사위원의 채점 기준으로 씁니다. 10문항 인터뷰로 초안·서류합격률 점수·심사위원 관점 피드백까지 무료로 받아보세요.",
};

const DIFF = [
  {
    label: "결과물",
    chatgpt: "그럴듯하게 완성된 문장",
    ours: "심사 항목 기준으로 구성된 초안",
  },
  {
    label: "판정",
    chatgpt: "잘 썼는지 알 수 없음",
    ours: "심사 루브릭 채점 → 서류합격률 %",
  },
  {
    label: "다음 행동",
    chatgpt: "어디를 고칠지 스스로 판단",
    ours: "탈락 사유가 될 부분을 본문에 메모로 표시",
  },
];

const FEATURES = [
  {
    title: "서류합격률 점수",
    desc: "실제 심사 루브릭으로 초안을 채점해 지금 서류가 어느 수준인지 %로 보여줍니다. 감이 아니라 숫자로 확인하세요.",
    tag: "채점",
  },
  {
    title: "심사위원 관점 피드백",
    desc: "심사위원이 걸고넘어질 문장을 본문 위에 메모로 짚어줍니다. 메모에 답할수록 완성도와 합격률이 올라갑니다.",
    tag: "피드백",
  },
  {
    title: "실제 공고 양식 변환",
    desc: "초기창업패키지 · 딥테크창업사관학교 · 혁신바우처 실제 양식 구조에 맞춰 재배치하고 DOCX로 내려받습니다.",
    tag: "양식",
  },
];

const STEPS = [
  { no: "01", title: "기업 정보 입력", desc: "업력·아이템 등 기본 정보 1분" },
  { no: "02", title: "10문항 인터뷰", desc: "핵심 질문만 답하면 AI가 나머지를 채움" },
  { no: "03", title: "초안 + 합격률 + 피드백", desc: "채점 결과와 보완 포인트를 함께 확인" },
  { no: "04", title: "양식 변환 · DOCX", desc: "지원할 공고 양식에 맞춰 문서로 다운로드" },
];

export default function LandingPage() {
  return (
    <div className="min-h-[100dvh] bg-white">
      <header className="px-5 py-4 sticky top-0 z-10 bg-slate-950/90 backdrop-blur-sm border-b border-slate-800">
        <div className="max-w-5xl mx-auto flex items-center justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center text-white font-bold text-sm">
              AI
            </div>
            <span className="font-semibold text-white text-base">사업계획서 AI</span>
          </div>
          <Link
            href="/start"
            className="px-4 py-2 rounded-xl bg-blue-600 text-white text-sm font-semibold hover:bg-blue-500 active:scale-[0.98] transition-all"
          >
            무료로 테스트해보기
          </Link>
        </div>
      </header>

      <main>
        {/* 히어로 — 다크, 후킹 대화 → USP */}
        <section className="bg-slate-950 px-5 pt-14 pb-16 md:pt-20 md:pb-24">
          <div className="max-w-5xl mx-auto">
            {/* 후킹 대화 */}
            <div className="max-w-md mb-10 space-y-3">
              <div className="flex justify-end">
                <p className="inline-block rounded-2xl rounded-br-sm bg-slate-800 text-slate-200 text-sm md:text-base px-4 py-2.5">
                  이거 그냥 ChatGPT로 써도 되지 않나…?
                </p>
              </div>
              <div className="flex justify-start">
                <p className="inline-block rounded-2xl rounded-bl-sm bg-blue-600 text-white text-sm md:text-base px-4 py-2.5">
                  네, <b>완성</b>은 됩니다. <b>붙는 건</b> 다른 문제예요.
                </p>
              </div>
            </div>

            <h1 className="text-5xl md:text-7xl font-bold text-white tracking-tight leading-[1.05] mb-6">
              완성 말고,
              <br />
              <span className="text-blue-500">합격.</span>
            </h1>
            <p className="text-base md:text-lg text-slate-400 leading-relaxed max-w-[46ch] mb-9">
              정부지원사업 사업계획서, 심사위원의 채점 기준으로 씁니다.
              10문항에 답하면 초안과 함께 <b className="text-slate-200">서류합격률 점수</b>,
              <b className="text-slate-200"> 심사위원 관점 피드백</b>까지 받아봅니다.
            </p>
            <div className="flex flex-col sm:flex-row sm:items-center gap-4">
              <Link
                href="/start"
                className="inline-block text-center px-8 py-4 rounded-xl bg-blue-600 text-white font-semibold text-base hover:bg-blue-500 active:scale-[0.98] transition-all"
              >
                무료로 초안 받아보기 →
              </Link>
              <p className="text-xs text-slate-500">회원가입 · 카드 등록 없음 &nbsp;·&nbsp; 약 10분 소요</p>
            </div>
          </div>
        </section>

        {/* ChatGPT 대비 — USP 핵심 */}
        <section className="px-5 py-16 md:py-20">
          <div className="max-w-5xl mx-auto">
            <p className="text-sm font-semibold text-blue-600 mb-2">왜 ChatGPT로는 부족한가</p>
            <h2 className="text-2xl md:text-4xl font-bold text-slate-900 tracking-tight leading-snug mb-3">
              심사위원은 &lsquo;완성된 서류&rsquo;가 아니라
              <br className="hidden md:block" /> &lsquo;붙는 서류&rsquo;를 뽑습니다
            </h2>
            <p className="text-sm md:text-base text-slate-500 leading-relaxed max-w-[52ch] mb-10">
              일반 AI 챗봇도 사업계획서를 완성해 줍니다. 하지만 그 서류가 심사 기준을
              통과하는지는 아무도 말해주지 않습니다. 우리는 채점하고, 짚어주고, 고치게 합니다.
            </p>

            <div className="rounded-2xl border border-slate-200 overflow-hidden">
              <div className="grid grid-cols-[72px_1fr_1fr] md:grid-cols-[120px_1fr_1fr] bg-slate-50 border-b border-slate-200 text-xs md:text-sm font-semibold">
                <div className="px-3 md:px-5 py-3 text-slate-400"></div>
                <div className="px-3 md:px-5 py-3 text-slate-500">일반 AI 챗봇</div>
                <div className="px-3 md:px-5 py-3 text-blue-700 bg-blue-50">사업계획서 AI</div>
              </div>
              {DIFF.map((row) => (
                <div
                  key={row.label}
                  className="grid grid-cols-[72px_1fr_1fr] md:grid-cols-[120px_1fr_1fr] border-b border-slate-100 last:border-b-0 text-xs md:text-sm"
                >
                  <div className="px-3 md:px-5 py-4 font-semibold text-slate-400">{row.label}</div>
                  <div className="px-3 md:px-5 py-4 text-slate-500 leading-relaxed">{row.chatgpt}</div>
                  <div className="px-3 md:px-5 py-4 text-slate-800 font-medium leading-relaxed bg-blue-50/50">
                    {row.ours}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* 근거 기능 3개 */}
        <section className="px-5 py-16 md:py-20 bg-slate-50 border-y border-slate-200">
          <div className="max-w-5xl mx-auto">
            <h2 className="text-2xl md:text-4xl font-bold text-slate-900 tracking-tight mb-10">
              &lsquo;합격&rsquo;에 붙는 세 가지 근거
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
              {FEATURES.map((f, i) => (
                <div
                  key={f.title}
                  className={`rounded-2xl p-7 ${
                    i === 0
                      ? "bg-slate-950 text-white"
                      : "bg-white border border-slate-200"
                  }`}
                >
                  <span
                    className={`inline-block text-[11px] font-bold tracking-wide px-2.5 py-1 rounded-full mb-4 ${
                      i === 0 ? "bg-blue-600 text-white" : "bg-blue-50 text-blue-700"
                    }`}
                  >
                    {f.tag}
                  </span>
                  <p className={`text-lg font-bold mb-2.5 ${i === 0 ? "text-white" : "text-slate-900"}`}>
                    {f.title}
                  </p>
                  <p className={`text-sm leading-relaxed ${i === 0 ? "text-slate-400" : "text-slate-500"}`}>
                    {f.desc}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* 이용 흐름 */}
        <section className="px-5 py-16 md:py-20">
          <div className="max-w-5xl mx-auto">
            <h2 className="text-2xl md:text-4xl font-bold text-slate-900 tracking-tight mb-10">
              10분이면 확인할 수 있습니다
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-x-6 gap-y-8">
              {STEPS.map((step) => (
                <div key={step.no} className="flex md:block items-start gap-4">
                  <p className="text-3xl font-bold text-blue-600 md:mb-3 flex-shrink-0 w-12 md:w-auto">
                    {step.no}
                  </p>
                  <div>
                    <p className="text-sm font-semibold text-slate-800 mb-1">{step.title}</p>
                    <p className="text-xs text-slate-500 leading-relaxed">{step.desc}</p>
                  </div>
                </div>
              ))}
            </div>

            {/* 정직 노트 — 과장광고 방어 */}
            <p className="mt-12 text-xs text-slate-400 leading-relaxed max-w-[60ch]">
              * 합격을 보장하는 서비스는 아닙니다. 어떤 서비스도 합격을 보장할 수 없습니다.
              대신 서류가 탈락하는 흔한 이유 — 심사 항목 누락, 근거 없는 주장, 양식 불일치 —
              를 하나씩 지울 수 있도록 돕습니다.
            </p>
          </div>
        </section>

        {/* 최종 CTA */}
        <section className="px-5 py-16 md:py-24 bg-slate-950">
          <div className="max-w-2xl mx-auto text-center">
            <h2 className="text-3xl md:text-5xl font-bold text-white tracking-tight mb-4">
              완성 말고, <span className="text-blue-500">합격.</span>
            </h2>
            <p className="text-slate-400 text-sm md:text-base mb-9">
              지금 10문항에 답하고 내 서류의 합격률부터 확인해보세요.
            </p>
            <Link
              href="/start"
              className="inline-block px-10 py-4 rounded-xl bg-blue-600 text-white font-semibold text-base hover:bg-blue-500 active:scale-[0.98] transition-all"
            >
              무료로 테스트해보기 →
            </Link>
          </div>
        </section>
      </main>

      <footer className="py-6 text-center text-xs text-slate-400 bg-slate-950 border-t border-slate-800">
        사업계획서 AI MVP
      </footer>
    </div>
  );
}
