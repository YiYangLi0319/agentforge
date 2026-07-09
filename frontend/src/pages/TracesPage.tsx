import {
  Activity,
  Bot,
  Braces,
  ChevronRight,
  Database,
  MessageSquare,
  RefreshCw,
  Telescope,
  Wrench,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge, Button, EmptyState, formatCost, formatTime, statusTone, STATUS_LABEL } from "../components/ui";
import { api } from "../lib/api";
import type { RunSummary, SpanInfo } from "../lib/types";

const KIND_ICON: Record<string, typeof Bot> = {
  agent: Bot,
  llm: MessageSquare,
  tool: Wrench,
  retrieval: Database,
  chain: Braces,
};

const KIND_COLOR: Record<string, string> = {
  agent: "text-indigo-400",
  llm: "text-sky-400",
  tool: "text-emerald-400",
  retrieval: "text-violet-400",
  chain: "text-zinc-400",
};

interface SpanNode extends SpanInfo {
  children: SpanNode[];
  depth: number;
}

function buildSpanTree(spans: SpanInfo[]): SpanNode[] {
  const map = new Map<string, SpanNode>();
  spans.forEach((s) => map.set(s.id, { ...s, children: [], depth: 0 }));
  const roots: SpanNode[] = [];
  for (const node of map.values()) {
    const parent = node.parent_id ? map.get(node.parent_id) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  const flat: SpanNode[] = [];
  const walk = (nodes: SpanNode[], depth: number) => {
    for (const n of nodes) {
      n.depth = depth;
      flat.push(n);
      walk(n.children, depth + 1);
    }
  };
  walk(roots, 0);
  return flat;
}

export default function TracesPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [kind, setKind] = useState<"" | "chat" | "research">("");
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [detail, setDetail] = useState<{ run: Record<string, unknown>; spans: SpanInfo[] } | null>(null);
  const [selectedSpan, setSelectedSpan] = useState<SpanInfo | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRuns(await api.get<RunSummary[]>(`/api/traces/runs${kind ? `?kind=${kind}` : ""}`));
    } finally {
      setLoading(false);
    }
  }, [kind]);

  useEffect(() => {
    load();
  }, [load]);

  const openRun = async (id: string) => {
    setSelectedRun(id);
    setSelectedSpan(null);
    setDetail(await api.get(`/api/traces/runs/${id}`));
  };

  const tree = useMemo(() => (detail ? buildSpanTree(detail.spans) : []), [detail]);
  const totalMs = useMemo(() => Math.max(...tree.map((s) => s.duration_ms ?? 0), 1), [tree]);

  return (
    <div className="flex h-full">
      {/* 运行列表 */}
      <div className="flex w-[420px] shrink-0 flex-col border-r border-zinc-800/80">
        <div className="flex items-center gap-2 border-b border-zinc-800/80 px-4 py-3">
          <Activity size={14} className="text-indigo-400" />
          <span className="flex-1 text-xs font-medium tracking-wide text-zinc-400">运行记录</span>
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as typeof kind)}
            className="rounded-md border border-zinc-700/80 bg-zinc-900 px-1.5 py-1 text-[11px] text-zinc-300 focus:outline-none"
          >
            <option value="">全部类型</option>
            <option value="chat">对话</option>
            <option value="research">研究</option>
          </select>
          <Button size="sm" variant="ghost" onClick={load} loading={loading}>
            <RefreshCw size={12} />
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {runs.length === 0 && (
            <div className="px-4 py-10 text-center text-xs text-zinc-600">暂无运行记录</div>
          )}
          {runs.map((r) => (
            <button
              key={r.id}
              onClick={() => openRun(r.id)}
              className={
                "block w-full border-b border-zinc-800/50 px-4 py-3 text-left hover:bg-zinc-900/60 " +
                (selectedRun === r.id ? "bg-zinc-900" : "")
              }
            >
              <div className="mb-1 flex items-center gap-2">
                {r.kind === "research" ? (
                  <Telescope size={13} className="text-violet-400" />
                ) : (
                  <MessageSquare size={13} className="text-indigo-400" />
                )}
                <span className="flex-1 truncate text-[13px] text-zinc-300">
                  {r.input_preview || "（无输入预览）"}
                </span>
                <Badge tone={statusTone(r.status)}>{STATUS_LABEL[r.status] ?? r.status}</Badge>
              </div>
              <div className="flex items-center gap-3 pl-5 font-mono text-[10px] text-zinc-500">
                <span>{formatTime(r.created_at)}</span>
                <span>{(r.prompt_tokens + r.completion_tokens).toLocaleString()} tok</span>
                <span>{formatCost(r.cost)}</span>
                {r.duration_ms !== null && <span>{(r.duration_ms / 1000).toFixed(1)}s</span>}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Span 树 */}
      <div className="flex min-w-0 flex-1 flex-col">
        {detail ? (
          <>
            <div className="border-b border-zinc-800/80 px-5 py-3 text-xs text-zinc-400">
              调用链 · {detail.spans.length} 个 Span
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              <div className="space-y-0.5">
                {tree.map((s) => {
                  const Icon = KIND_ICON[s.kind] ?? Braces;
                  const width = Math.max(((s.duration_ms ?? 0) / totalMs) * 100, 2);
                  return (
                    <button
                      key={s.id}
                      onClick={() => setSelectedSpan(s)}
                      className={
                        "flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-zinc-900 " +
                        (selectedSpan?.id === s.id ? "bg-zinc-900 ring-1 ring-indigo-500/40" : "")
                      }
                      style={{ paddingLeft: `${s.depth * 20 + 8}px` }}
                    >
                      <Icon size={13} className={"shrink-0 " + (KIND_COLOR[s.kind] ?? "")} />
                      <span
                        className={
                          "w-52 shrink-0 truncate font-mono text-[12px] " +
                          (s.status === "error" ? "text-rose-400" : "text-zinc-300")
                        }
                      >
                        {s.name}
                      </span>
                      <span className="h-3 flex-1 overflow-hidden rounded-sm bg-zinc-900/80">
                        <span
                          className={
                            "block h-full rounded-sm " +
                            (s.status === "error" ? "bg-rose-500/60" : "bg-indigo-500/50")
                          }
                          style={{ width: `${width}%` }}
                        />
                      </span>
                      <span className="w-16 shrink-0 text-right font-mono text-[10px] text-zinc-500">
                        {s.duration_ms !== null ? `${s.duration_ms}ms` : "-"}
                      </span>
                      <span className="w-20 shrink-0 text-right font-mono text-[10px] text-zinc-500">
                        {s.prompt_tokens + s.completion_tokens > 0
                          ? `${(s.prompt_tokens + s.completion_tokens).toLocaleString()} tok`
                          : ""}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
            {/* Span 详情 */}
            {selectedSpan && (
              <div className="max-h-72 shrink-0 overflow-y-auto border-t border-zinc-800/80 bg-zinc-900/40 p-4">
                <div className="mb-2 flex items-center gap-2">
                  <ChevronRight size={13} className="text-zinc-500" />
                  <span className="font-mono text-xs text-zinc-200">{selectedSpan.name}</span>
                  <Badge tone={selectedSpan.status === "ok" ? "green" : "red"}>{selectedSpan.status}</Badge>
                  {selectedSpan.cost > 0 && (
                    <span className="font-mono text-[10px] text-zinc-500">{formatCost(selectedSpan.cost)}</span>
                  )}
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <div>
                    <div className="mb-1 text-[10px] text-zinc-500">输入</div>
                    <pre className="max-h-40 overflow-auto rounded-lg bg-zinc-950 p-2.5 font-mono text-[11px] whitespace-pre-wrap text-zinc-300">
                      {JSON.stringify(selectedSpan.input, null, 2)}
                    </pre>
                  </div>
                  <div>
                    <div className="mb-1 text-[10px] text-zinc-500">
                      {selectedSpan.status === "error" ? "错误" : "输出"}
                    </div>
                    <pre className="max-h-40 overflow-auto rounded-lg bg-zinc-950 p-2.5 font-mono text-[11px] whitespace-pre-wrap text-zinc-300">
                      {selectedSpan.status === "error"
                        ? selectedSpan.error
                        : JSON.stringify(selectedSpan.output, null, 2)}
                    </pre>
                  </div>
                </div>
              </div>
            )}
          </>
        ) : (
          <EmptyState
            icon={<Activity size={28} />}
            title="选择一条运行记录"
            desc="查看完整调用链：Agent 步骤、LLM 调用、工具执行的耗时、token 消耗与成本"
          />
        )}
      </div>
    </div>
  );
}
