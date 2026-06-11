"use client";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

const SHARED_COMPONENTS: Components = {
  h1: ({ children }) => (
    <h1 className="text-base font-bold text-slate-800 mt-5 mb-2 pb-1.5 border-b border-slate-200 first:mt-0">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="text-sm font-bold text-slate-800 mt-4 mb-1.5">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-sm font-semibold text-slate-700 mt-3 mb-1">{children}</h3>
  ),
  p: ({ children }) => (
    <p className="text-sm text-slate-600 leading-relaxed my-1.5">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="list-disc list-inside my-1.5 space-y-0.5 text-sm text-slate-600">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal list-inside my-1.5 space-y-0.5 text-sm text-slate-600">{children}</ol>
  ),
  li: ({ children }) => (
    <li className="text-sm text-slate-600 leading-relaxed">{children}</li>
  ),
  strong: ({ children }) => (
    <strong className="font-semibold text-slate-800">{children}</strong>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-slate-300 pl-3 my-2 text-slate-500 text-sm italic">{children}</blockquote>
  ),
  hr: () => <hr className="my-3 border-slate-200" />,
  del: ({ children }) => <span className="text-slate-600">{children}</span>,
  table: ({ children }) => (
    <table className="w-full border-collapse my-3 text-xs">{children}</table>
  ),
  th: ({ children }) => (
    <th className="border border-slate-200 px-3 py-2 bg-slate-50 text-left text-xs font-semibold text-slate-700">{children}</th>
  ),
  td: ({ children }) => (
    <td className="border border-slate-200 px-3 py-2 text-xs text-slate-600">{children}</td>
  ),
};

const BLUE_COMPONENTS: Components = {
  ...SHARED_COMPONENTS,
  code: ({ children }) => (
    <code className="text-xs text-blue-700 bg-blue-50 px-1 py-0.5 rounded">{children}</code>
  ),
};

const TEAL_COMPONENTS: Components = {
  ...SHARED_COMPONENTS,
  code: ({ children }) => (
    <code className="text-xs text-teal-700 bg-teal-50 px-1 py-0.5 rounded">{children}</code>
  ),
};

interface Props {
  children: string;
  variant?: "blue" | "teal";
}

export default function MarkdownRenderer({ children, variant = "blue" }: Props) {
  const components = variant === "teal" ? TEAL_COMPONENTS : BLUE_COMPONENTS;
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {children}
    </ReactMarkdown>
  );
}
