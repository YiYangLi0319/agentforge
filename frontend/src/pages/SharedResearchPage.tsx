import { Boxes, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import Markdown from "../components/Markdown";
import type { Source } from "../lib/types";

interface SharedReport {
  query: string;
  report_md: string;
  sources: Source[];
  created_at: string;
}

export default function SharedResearchPage() {
  const { token } = useParams<{ token: string }>();
  const [report, setReport] = useState<SharedReport | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    // 公开接口，无需鉴权
    fetch(`/api/public/research/${token}`)
      .then((r) => {
        if (!r.ok) throw new Error("分享链接无效或已取消");
        return r.json();
      })
      .then(setReport)
      .catch((e) => setError(e instanceof Error ? e.message : "加载失败"));
  }, [token]);

  return (
    <div className="min-h-full bg-zinc-950">
      <div className="border-b border-zinc-800/80 bg-zinc-900/40">
        <div className="mx-auto flex max-w-3xl items-center gap-2 px-6 py-4">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600">
            <Boxes size={17} className="text-white" />
          </div>
          <div>
            <div className="text-sm font-bold text-zinc-100">AgentForge</div>
            <div className="text-[10px] text-zinc-500">深度研究报告 · 公开分享</div>
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-3xl px-6 py-8">
        {error && (
          <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
            {error}
          </div>
        )}
        {!report && !error && (
          <div className="flex items-center gap-2 text-sm text-zinc-500">
            <Loader2 size={16} className="animate-spin" /> 加载中…
          </div>
        )}
        {report && (
          <>
            <div className="mb-4">
              <div className="text-[11px] text-zinc-500">研究主题</div>
              <h1 className="text-lg font-semibold text-zinc-100">{report.query}</h1>
            </div>
            <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
              <Markdown content={report.report_md} sources={report.sources} />
            </div>
            <div className="mt-6 text-center text-[11px] text-zinc-600">
              由 AgentForge 深度研究 Agent 生成
            </div>
          </>
        )}
      </div>
    </div>
  );
}
