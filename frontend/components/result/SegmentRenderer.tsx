"use client";
import { forwardRef, useImperativeHandle, useRef } from "react";
import type { ContentSegment, InlineSuggestion } from "@/lib/types";

export interface SegmentRendererHandle {
  scrollToAnchor: (originalIndex: number) => void;
}

const MD_TABLE_ROW = /^\s*\|/;

function parseTableLines(lines: string[]): { headers: string[][]; body: string[][] } {
  function parseRow(line: string): string[] {
    const parts = line.split("|");
    return parts.slice(1, parts.length - 1).map((c) => c.trim());
  }
  const rows = lines.map(parseRow);
  const sepIdx = rows.findIndex(
    (r) => r.length > 0 && r.every((c) => /^[-:\s]+$/.test(c))
  );
  if (sepIdx >= 0) {
    return { headers: rows.slice(0, sepIdx), body: rows.slice(sepIdx + 1) };
  }
  return { headers: [], body: rows };
}

const BULLET_LINE = /^\s*-\s/;
const HEADING_LINE = /^■/;
const CAPTION_CELL_RE = /^<.*>$/;
const SOURCE_CELL_RE = /^출처:/;
const DESCRIPTION_CELL_RE = /^\[/;
const URL_RE = /https?:\/\/\S+/;

type CellStyle = { tdClass: string; content: React.ReactNode };

function renderVisualCell(text: string, anchorFn?: (t: string) => React.ReactNode[]): CellStyle {
  const t = text.trim();
  if (CAPTION_CELL_RE.test(t)) {
    return {
      tdClass: "border border-slate-200 px-3 py-2 text-center",
      content: <span className="text-xs text-slate-500">{t}</span>,
    };
  }
  if (SOURCE_CELL_RE.test(t)) {
    const rest = t.replace(/^출처:\s*/, "");
    const urlMatch = rest.match(URL_RE);
    if (urlMatch) {
      const url = urlMatch[0];
      const before = rest.slice(0, rest.indexOf(url));
      const after = rest.slice(rest.indexOf(url) + url.length);
      return {
        tdClass: "border border-slate-200 px-3 py-2",
        content: (
          <span className="text-xs">
            출처: {before}
            <a href={url} target="_blank" rel="noopener noreferrer" className="text-blue-500 underline">{url}</a>
            {after}
          </span>
        ),
      };
    }
    return {
      tdClass: "border border-slate-200 px-3 py-2",
      content: <span className="text-xs text-blue-500">{t}</span>,
    };
  }
  if (DESCRIPTION_CELL_RE.test(t)) {
    return {
      tdClass: "border border-slate-200 px-3 py-2 bg-blue-50",
      content: <span className="text-blue-700 italic">{t}</span>,
    };
  }
  return {
    tdClass: "border border-slate-200 px-2 py-1.5 align-top text-slate-600",
    content: anchorFn ? <>{anchorFn(t)}</> : <span>{t}</span>,
  };
}

function renderSegmentContent(
  text: string,
  suggestions: InlineSuggestion[],
  sortedSuggestions: InlineSuggestion[],
  isInferred: boolean,
  segKey: number,
  showAnchors: boolean,
  onAnchorClick?: (index: number) => void
): React.ReactNode[] {
  const lines = text.split("\n");
  const nodes: React.ReactNode[] = [];
  const renderedTableKeys = new Set<string>();
  let i = 0;
  let blockKey = 0;

  const anchor = (t: string) =>
    renderTextWithAnchors(t, suggestions, sortedSuggestions, isInferred, showAnchors, onAnchorClick);

  while (i < lines.length) {
    const line = lines[i];

    // 표 블록
    if (MD_TABLE_ROW.test(line)) {
      const tableLines: string[] = [];
      while (i < lines.length && MD_TABLE_ROW.test(lines[i])) {
        tableLines.push(lines[i]);
        i++;
      }
      const { headers, body } = parseTableLines(tableLines);
      const allRows = [...headers, ...body];
      if (allRows.length === 0) continue;
      const tableKey = (headers[0]?.[0] ?? body[0]?.[0] ?? "").trim();
      if (tableKey && renderedTableKeys.has(tableKey)) continue;
      renderedTableKeys.add(tableKey);
      const numCols = Math.max(...allRows.map((r) => r.length), 1);
      nodes.push(
        <table key={`seg${segKey}-tbl${blockKey++}`} className="w-full border-collapse my-2 text-xs">
          {headers.length > 0 && (
            <thead>
              {headers.map((row, ri) => (
                <tr key={ri}>
                  {Array.from({ length: numCols }).map((_, ci) => (
                    <th key={ci} className="border border-slate-200 px-3 py-2 bg-slate-50 font-semibold text-left text-slate-700">
                      {row[ci] ?? ""}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
          )}
          <tbody>
            {body.map((row, ri) => (
              <tr key={ri}>
                {Array.from({ length: numCols }).map((_, ci) => {
                  const { tdClass, content } = renderVisualCell(row[ci] ?? "", anchor);
                  return <td key={ci} className={tdClass}>{content}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      );
    }
    // 빈 줄 → 여백
    else if (line.trim() === "") {
      nodes.push(<div key={`seg${segKey}-sp${blockKey++}`} className="h-3" />);
      i++;
    }
    // ■ 중제목
    else if (HEADING_LINE.test(line)) {
      nodes.push(
        <div key={`seg${segKey}-h${blockKey++}`} className="font-semibold text-slate-800 leading-snug">
          {anchor(line)}
        </div>
      );
      i++;
    }
    // 세부항목 ( - ) — 연속된 불릿을 빈 줄 없이 묶음
    else if (BULLET_LINE.test(line)) {
      const bulletLines: string[] = [];
      while (i < lines.length && BULLET_LINE.test(lines[i])) {
        bulletLines.push(lines[i]);
        i++;
      }
      nodes.push(
        <div key={`seg${segKey}-ul${blockKey++}`}>
          {bulletLines.map((bl, bi) => (
            <div key={bi} className="leading-relaxed">
              {anchor(bl)}
            </div>
          ))}
        </div>
      );
    }
    // 일반 단락
    else {
      const textLines: string[] = [];
      while (
        i < lines.length &&
        !MD_TABLE_ROW.test(lines[i]) &&
        lines[i].trim() !== "" &&
        !HEADING_LINE.test(lines[i]) &&
        !BULLET_LINE.test(lines[i])
      ) {
        textLines.push(lines[i]);
        i++;
      }
      const joined = textLines.join("\n").trim();
      if (joined) {
        nodes.push(
          <p key={`seg${segKey}-txt${blockKey++}`} className="mb-2">
            {anchor(joined)}
          </p>
        );
      }
    }
  }
  return nodes;
}

interface SegmentRendererProps {
  segments: ContentSegment[];
  suggestions: InlineSuggestion[];
  showAnchors?: boolean;
  onAnchorClick?: (index: number) => void;
}

function renderTextWithAnchors(
  text: string,
  suggestions: InlineSuggestion[],
  sortedSuggestions: InlineSuggestion[],
  isInferred: boolean,
  showAnchors: boolean,
  onAnchorClick?: (index: number) => void
): React.ReactNode[] {
  const colorClass = isInferred ? "text-emerald-700" : "text-slate-800";

  if (!showAnchors) {
    return [<span key={0} className={colorClass}>{text}</span>];
  }

  const nodes: React.ReactNode[] = [];
  let remaining = text;
  let keyIdx = 0;

  const activeSuggestions = [...suggestions].filter((s) => s.anchor_text);

  while (remaining.length > 0) {
    let earliest = remaining.length;
    let match: InlineSuggestion | null = null;
    let matchIndex = -1;

    for (let i = 0; i < activeSuggestions.length; i++) {
      const pos = remaining.indexOf(activeSuggestions[i].anchor_text);
      if (pos >= 0 && pos < earliest) {
        earliest = pos;
        match = activeSuggestions[i];
        matchIndex = i;
      }
    }

    if (!match) {
      nodes.push(
        <span key={keyIdx++} className={isInferred ? "text-emerald-700" : "text-slate-800"}>
          {remaining}
        </span>
      );
      break;
    }

    if (earliest > 0) {
      nodes.push(
        <span key={keyIdx++} className={isInferred ? "text-emerald-700" : "text-slate-800"}>
          {remaining.slice(0, earliest)}
        </span>
      );
    }

    const sortedIndex = sortedSuggestions.indexOf(match);
    const originalIndex = suggestions.indexOf(match);
    const isResolved = match.response.trim() !== "";
    nodes.push(
      <span key={keyIdx++} className="inline">
        <mark
          className={isResolved ? "memo-anchor-resolved cursor-pointer bg-emerald-100 rounded px-0.5" : "memo-anchor cursor-pointer"}
          onClick={() => onAnchorClick?.(sortedIndex)}
          title={match.note}
        >
          {match.anchor_text}
        </mark>
        <sup
          data-anchor-index={originalIndex}
          className={isResolved ? "text-emerald-600 font-bold text-xs cursor-pointer ml-0.5 hover:text-emerald-800" : "text-red-500 font-bold text-xs cursor-pointer ml-0.5 hover:text-blue-600"}
          onClick={() => onAnchorClick?.(sortedIndex)}
        >
          {isResolved ? `✓[${sortedIndex + 1}]` : `[${sortedIndex + 1}]`}
        </sup>
      </span>
    );

    remaining = remaining.slice(earliest + match.anchor_text.length);
    activeSuggestions.splice(matchIndex, 1);
  }

  return nodes;
}

const SegmentRenderer = forwardRef<SegmentRendererHandle, SegmentRendererProps>(
  function SegmentRenderer({ segments, suggestions, showAnchors = true, onAnchorClick }, ref) {
    const containerRef = useRef<HTMLDivElement>(null);

    useImperativeHandle(ref, () => ({
      scrollToAnchor: (originalIndex: number) => {
        const el = containerRef.current?.querySelector<HTMLElement>(`[data-anchor-index="${originalIndex}"]`);
        if (!el) return;
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.classList.remove("anchor-highlight");
        void el.offsetWidth;
        el.classList.add("anchor-highlight");
        setTimeout(() => el.classList.remove("anchor-highlight"), 1000);
      },
    }));

    if (!segments || segments.length === 0) return null;

    const fullText = segments.map((s) => s.text ?? "").join("");
    const activeSuggestions = suggestions.filter((s) => s.anchor_text);
    const sortedSuggestions = [...activeSuggestions].sort((a, b) => {
      const posA = fullText.indexOf(a.anchor_text);
      const posB = fullText.indexOf(b.anchor_text);
      return posA - posB;
    });

    return (
      <div ref={containerRef} className="section-content text-sm leading-relaxed">
        {segments.map((seg, i) => (
          <div key={i} className="mb-1">
            {renderSegmentContent(
              seg.text,
              suggestions,
              sortedSuggestions,
              seg.source === "llm_inferred",
              i,
              showAnchors,
              onAnchorClick
            )}
          </div>
        ))}
      </div>
    );
  }
);
SegmentRenderer.displayName = "SegmentRenderer";
export default SegmentRenderer;
