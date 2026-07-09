import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Source } from "../lib/types";

/** 把回答中的 [n] 引用角标转换为可交互的引用链接 */
function linkifyCitations(text: string): string {
  return text.replace(/\[(\d{1,3})\]/g, (_m, n) => `[${n}](#cite-${n})`);
}

function CitationChip({ n, source }: { n: number; source?: Source }) {
  const [open, setOpen] = useState(false);
  if (!source) return <sup className="citation-chip">{n}</sup>;
  return (
    <span className="relative inline-block">
      <a
        href={source.url || `#cite-${n}`}
        target={source.url ? "_blank" : undefined}
        rel="noreferrer"
        className="citation-chip"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onClick={(e) => {
          if (!source.url) e.preventDefault();
        }}
      >
        {n}
      </a>
      {open && (
        <span className="absolute bottom-full left-1/2 z-50 mb-1.5 w-72 -translate-x-1/2 rounded-lg border border-zinc-700 bg-zinc-900 p-3 text-xs shadow-xl">
          <span className="mb-1 flex items-center gap-1.5 font-medium text-zinc-200">
            <span
              className={
                "rounded px-1 py-0.5 text-[10px] " +
                (source.origin === "kb" ? "bg-emerald-500/20 text-emerald-300" : "bg-sky-500/20 text-sky-300")
              }
            >
              {source.origin === "kb" ? "知识库" : "网页"}
            </span>
            <span className="truncate">{source.title || source.filename}</span>
          </span>
          {source.heading && <span className="block text-zinc-500">{source.heading}</span>}
          <span className="mt-1 block leading-5 text-zinc-400">{source.snippet}</span>
        </span>
      )}
    </span>
  );
}

export default function Markdown({
  content,
  sources = [],
  streaming = false,
}: {
  content: string;
  sources?: Source[];
  streaming?: boolean;
}) {
  const sourceMap = useMemo(() => new Map(sources.map((s) => [s.id, s])), [sources]);
  const processed = useMemo(() => linkifyCitations(content), [content]);

  return (
    <div className={"md-body" + (streaming ? " streaming-caret" : "")}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children, ...props }) => {
            const match = href?.match(/^#cite-(\d+)$/);
            if (match) {
              const n = Number(match[1]);
              return <CitationChip n={n} source={sourceMap.get(n)} />;
            }
            return (
              <a href={href} target="_blank" rel="noreferrer" {...props}>
                {children}
              </a>
            );
          },
        }}
      >
        {processed}
      </ReactMarkdown>
    </div>
  );
}
