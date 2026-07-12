"use client";
import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { AiMessage, UserMessage } from "@/components/interview/ChatMessage";
import ChatInput from "@/components/interview/ChatInput";
import { useInterviewStore } from "@/store/interviewStore";
import { api } from "@/lib/api";
import { EXAMPLE_ANSWERS } from "@/lib/exampleAnswers";
import type { Question } from "@/lib/types";

type ChatItem =
  | { type: "ai"; question: Question }
  | { type: "user"; qid: string; text: string };

export default function InterviewPage() {
  const params = useParams();
  const router = useRouter();
  const sessionId = params.sessionId as string;

  const { questions, answers, currentIndex, editingQid, isSubmitting, programCode,
    setSession, setQuestions, setCurrentIndex, startEditing, stopEditing, saveAnswer } =
    useInterviewStore();

  const [chatHistory, setChatHistory] = useState<ChatItem[]>([]);
  const [isLoaded, setIsLoaded] = useState(false);
  const [inputHasText, setInputHasText] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  // PDF 업로드 등으로 이미 답변이 채워진 질문 — 인터뷰에서 다시 묻지 않고 건너뛴다.
  const prefilledQids = useRef<Set<string>>(new Set());

  useEffect(() => {
    async function init() {
      try {
        const [sessionData, questionData] = await Promise.all([
          api.getSession(sessionId),
          api.getQuestions(),
        ]);
        setSession(sessionId, sessionData.program_code);
        setQuestions(questionData.questions);

        const qs = questionData.questions;
        const isAnswered = (qid: string) => {
          const a = sessionData.answers[qid];
          return !!(a?.text && a.text.trim());
        };
        // 이미 답변 있는 질문(PDF 사전채움 포함)은 다시 묻지 않을 대상으로 기록
        prefilledQids.current = new Set(
          qs.filter((q) => isAnswered(q.qid)).map((q) => q.qid)
        );

        // 물어봐야 할 첫 번째 미답변 질문
        const firstIdx = qs.findIndex((q) => !isAnswered(q.qid));

        const history: ChatItem[] = [];
        for (let i = 0; i < qs.length; i++) {
          const q = qs[i];
          if (isAnswered(q.qid)) {
            // 채워진 답변은 확인·수정 가능하도록 채팅에 표시
            history.push({ type: "ai", question: q });
            history.push({ type: "user", qid: q.qid, text: sessionData.answers[q.qid].text });
          } else if (i === firstIdx) {
            // 첫 번째로 물어볼 질문만 프롬프트로 노출
            history.push({ type: "ai", question: q });
          }
        }

        setChatHistory(history);
        setCurrentIndex(firstIdx === -1 ? qs.length : firstIdx);
        setIsLoaded(true);
      } catch {
        router.push("/");
      }
    }
    init();
  }, [sessionId]);

  // 다음으로 물어볼 미답변 질문 인덱스(사전채움 질문은 건너뜀).
  function nextUnansweredIndex(from: number): number {
    for (let i = from; i < questions.length; i++) {
      if (!prefilledQids.current.has(questions[i].qid)) return i;
    }
    return questions.length;
  }

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatHistory]);

  function handleAnswer(text: string) {
    if (currentIndex >= questions.length) return;
    const q = questions[currentIndex];
    const capturedIndex = currentIndex;
    saveAnswer(q.qid, text).then(() => {
      const nextIdx = nextUnansweredIndex(capturedIndex + 1);
      setChatHistory((prev) => {
        const next = [...prev, { type: "user" as const, qid: q.qid, text }];
        if (nextIdx < questions.length) {
          next.push({ type: "ai" as const, question: questions[nextIdx] });
        }
        return next;
      });
      setCurrentIndex(nextIdx);
      setInputHasText(false);
    });
  }

  function handleSkip() {
    if (currentIndex >= questions.length) return;
    const q = questions[currentIndex];
    const capturedIndex = currentIndex;
    const nextIdx = nextUnansweredIndex(capturedIndex + 1);
    setChatHistory((prev) => {
      const next = [...prev, { type: "user" as const, qid: q.qid, text: "(건너뜀)" }];
      if (nextIdx < questions.length) {
        next.push({ type: "ai" as const, question: questions[nextIdx] });
      }
      return next;
    });
    setCurrentIndex(nextIdx);
    setInputHasText(false);
  }

  function handleEditSave(qid: string, text: string) {
    saveAnswer(qid, text).then(() => {
      setChatHistory((prev) =>
        prev.map((item) =>
          item.type === "user" && item.qid === qid ? { ...item, text } : item
        )
      );
      stopEditing();
    });
  }

  const isAllAnswered = currentIndex >= questions.length;

  if (!isLoaded) {
    return (
      <div className="h-screen flex items-center justify-center bg-slate-50">
        <div className="w-8 h-8 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col bg-slate-50">
      {/* 헤더 */}
      <header className="flex-shrink-0 bg-white border-b border-slate-200 px-4 py-3">
        <div className="max-w-2xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button onClick={() => router.push("/")} className="text-slate-400 hover:text-slate-600 p-1">
              ←
            </button>
            <span className="font-semibold text-slate-800 text-sm">인터뷰</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="text-xs text-slate-500">
              {Math.min(currentIndex, questions.length)}/{questions.length}
            </div>
            <div className="w-24 h-1.5 bg-slate-200 rounded-full overflow-hidden">
              <div
                className="h-full bg-blue-500 rounded-full transition-all duration-500"
                style={{ width: `${(Math.min(currentIndex, questions.length) / Math.max(questions.length, 1)) * 100}%` }}
              />
            </div>
          </div>
        </div>
      </header>

      {/* 채팅 영역 */}
      <div className="flex-1 overflow-y-auto custom-scrollbar">
        <div className="max-w-2xl mx-auto px-4 py-6 space-y-4">
          {/* 인트로 메시지 */}
          <div className="flex items-start gap-3">
            <div className="flex-shrink-0 w-8 h-8 rounded-full bg-blue-600 flex items-center justify-center text-white text-xs font-bold">
              AI
            </div>
            <div className="bg-white border border-slate-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm max-w-xl">
              <p className="text-slate-700 text-sm leading-relaxed">
                안녕하세요! 사업계획서 초안 작성을 위해 몇 가지 질문을 드리겠습니다.
                답변이 구체적일수록 더 좋은 초안이 생성됩니다.
              </p>
            </div>
          </div>

          {chatHistory.map((item, idx) =>
            item.type === "ai" ? (
              <AiMessage
                key={idx}
                text={item.question.text}
                hint={item.question.hint}
                questionNumber={questions.indexOf(item.question) + 1}
                total={questions.length}
                exampleAnswer={EXAMPLE_ANSWERS[item.question.qid]}
                showExample={idx === chatHistory.length - 1}
              />
            ) : (
              <UserMessage
                key={idx}
                text={item.text}
                qid={item.qid}
                isEditing={editingQid === item.qid}
                onStartEdit={() => startEditing(item.qid)}
                onSaveEdit={(text) => handleEditSave(item.qid, text)}
                onCancelEdit={stopEditing}
              />
            )
          )}

          {/* 모든 질문 완료 메시지 */}
          {isAllAnswered && (
            <div className="flex justify-center py-4 animate-fade-in-up">
              <p className="text-slate-500 text-sm">
                모든 질문을 완료했습니다!
              </p>
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      {/* 입력창 */}
      {/* 항상 표시되는 생성 버튼 + 미입력 안내 */}
      <div className="flex-shrink-0 border-t border-slate-200 bg-white px-4 py-2 flex items-center justify-between gap-3">
        <p className="text-xs text-slate-400">
          💡 미입력 항목은 AI가 추론하여 작성합니다
        </p>
        <button
          onClick={() => router.push(`/generating/${sessionId}`)}
          className="flex-shrink-0 px-4 py-2 bg-blue-600 text-white text-sm font-semibold rounded-xl hover:bg-blue-700 active:scale-95 transition-all shadow-sm"
        >
          초안 생성 ({Math.min(currentIndex, questions.length)}/{questions.length})
        </button>
      </div>
      {!isAllAnswered && (
        <ChatInput
          onSubmit={handleAnswer}
          onSkip={handleSkip}
          isSubmitting={isSubmitting}
          onInputChange={setInputHasText}
        />
      )}

    </div>
  );
}
