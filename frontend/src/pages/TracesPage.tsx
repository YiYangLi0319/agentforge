import {
  Activity,
  Bot,
  Braces,
  ChevronRight,
  Database,
  GitCompare,
  MessageSquare,
  RefreshCw,
  Telescope,
  Wrench,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge, Button, EmptyState, formatCost, formatTime, statusTone, STATUS_LABEL } from "../components/ui";
import { api } from "../lib/api";
import type { RunCompareItem, RunComparison, RunSummary, SpanDiffRow, SpanInfo } from "../lib/types";

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
  const [compareMode, setCompareMode] = useState(false);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [comparison, setComparison] = useState<RunComparison | null>(null);

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

  useEffect(() => {
    if (compareIds.length !== 2) {
      setComparison(null);
      return;
    }
    let active = true;
    api
      .get<RunComparison>(`/api/traces/compare?a=${compareIds[0]}&b=${compareIds[1]}`)
      .then((c) => active && setComparison(c))
      .catch(() => active && setComparison(null));
    return () => {
      active = false;
    };
  }, [compareIds]);

  const openRun = async (id: string) => {
    setSelectedRun(id);
    setSelectedSpan(null);
    setDetail(await api.get(`/api/traces/runs/${id}`));
  };

  const toggleCompare = (id: string) => {
    setCompareIds((ids) =>
      ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id].slice(-2),
    );
  };

  const toggleCompareMode = () => {
    setCompareMode((on) => !on);
    setCompareIds([]);
    setComparison(null);
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
          <Button
            size="sm"
            variant={compareMode ? "primary" : "ghost"}
            onClick={toggleCompareMode}
            aria-label="对比模式：选择两条运行进行对比"
          >
            <GitCompare size={12} />
          </Button>
          <Button size="sm" variant="ghost" onClick={load} loading={loading}>
            <RefreshCw size={12} />
          </Button>
        </div>
        {compareMode && (
          <div className="border-b border-zinc-800/80 bg-indigo-500/5 px-4 py-1.5 text-[11px] text-indigo-300">
            对比模式：勾选两条运行（已选 {compareIds.length}/2）
          </div>
        )}
        <div className="flex-1 overflow-y-auto">
          {runs.length === 0 && (
            <div className="px-4 py-10 text-center text-xs text-zinc-600">暂无运行记录</div>
          )}
          {runs.map((r) => {
            const compareIdx = compareIds.indexOf(r.id);
            const active = compareMode ? compareIdx >= 0 : selectedRun === r.id;
            return (
            <button
              key={r.id}
              onClick={() => (compareMode ? toggleCompare(r.id) : openRun(r.id))}
              className={
                "block w-full border-b border-zinc-800/50 px-4 py-3 text-left hover:bg-zinc-900/60 " +
                (active ? "bg-zinc-900" : "")
              }
            >
              <div className="mb-1 flex items-center gap-2">
                {compareMode && (
                  <span
                    className={
                      "flex h-4 w-4 shrink-0 items-center justify-center rounded text-[9px] font-bold " +
                      (compareIdx >= 0 ? "bg-indigo-500 text-white" : "border border-zinc-600 text-transparent")
                    }
                  >
                    {compareIdx >= 0 ? "AB"[compareIdx] : ""}
                  </span>
                )}
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
            );
          })}
        </div>
      </div>

      {/* Span 树 / 运行对比 */}
      <div className="flex min-w-0 flex-1 flex-col">
        {compareMode ? (
          comparison ? (
            <RunComparisonView comparison={comparison} />
          ) : (
            <EmptyState
              icon={<GitCompare size={28} />}
              title="选择两条运行进行对比"
              desc="在左侧勾选 A、B 两条运行，查看用量 / 耗时 / 成本与工具调用的差异"
            />
          )
        ) : detail ? (
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

function fmtDuration(ms: number | null): string {
  if (ms == null) return "-";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

/** 资源类指标：越高越"差"（红），越低越"好"（绿）；用于成本/耗时/token/调用数的差值着色。 */
function DeltaCell({ a, b, format }: { a: number | null; b: number | null; format: (v: number | null) => string }) {
  if (a == null || b == null) return <span className="text-zinc-600">-</span>;
  const delta = b - a;
  if (delta === 0) return <span className="text-zinc-500">0</span>;
  const worse = delta > 0;
  const pct = a !== 0 ? ` (${delta > 0 ? "+" : ""}${Math.round((delta / a) * 100)}%)` : "";
  return (
    <span className={worse ? "text-rose-400" : "text-emerald-400"}>
      {delta > 0 ? "▲" : "▼"} {format(Math.abs(delta))}
      <span className="text-zinc-600">{pct}</span>
    </span>
  );
}

function RunComparisonView({ comparison }: { comparison: RunComparison }) {
  const [a, b] = comparison.runs;
  const num = (v: number | null) => (v == null ? "-" : v.toLocaleString());
  const rows: { label: string; a: number | null; b: number | null; fmt: (v: number | null) => string }[] = [
    { label: "总 tokens", a: a.totals.total_tokens, b: b.totals.total_tokens, fmt: num },
    { label: "输入 tokens", a: a.totals.prompt_tokens, b: b.totals.prompt_tokens, fmt: num },
    { label: "输出 tokens", a: a.totals.completion_tokens, b: b.totals.completion_tokens, fmt: num },
    { label: "成本", a: a.totals.cost, b: b.totals.cost, fmt: (v) => formatCost(v ?? 0) },
    { label: "耗时", a: a.totals.duration_ms, b: b.totals.duration_ms, fmt: fmtDuration },
    { label: "Span 数", a: a.totals.span_count, b: b.totals.span_count, fmt: num },
    { label: "LLM 调用", a: a.totals.llm_calls, b: b.totals.llm_calls, fmt: num },
    { label: "工具调用", a: a.totals.tool_calls, b: b.totals.tool_calls, fmt: num },
    { label: "检索次数", a: a.totals.retrievals, b: b.totals.retrievals, fmt: num },
  ];
  const toolNames = Array.from(new Set([...a.tools, ...b.tools].map((t) => t.name))).sort();
  const toolCount = (item: RunCompareItem, name: string) => item.tools.find((t) => t.name === name)?.count ?? 0;

  return (
    <div className="flex-1 overflow-y-auto p-5">
      <div className="mb-4 grid grid-cols-2 gap-3">
        {[a, b].map((r, i) => (
          <div key={r.id} className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-3">
            <div className="mb-1 flex items-center gap-1.5">
              <span className="flex h-4 w-4 items-center justify-center rounded bg-indigo-500 text-[9px] font-bold text-white">
                {"AB"[i]}
              </span>
              <Badge tone={statusTone(r.status)}>{STATUS_LABEL[r.status] ?? r.status}</Badge>
              <span className="text-[10px] text-zinc-500">{r.kind}</span>
            </div>
            <div className="truncate text-[12px] text-zinc-300" title={r.input_preview}>
              {r.input_preview || "（无输入预览）"}
            </div>
            <div className="mt-0.5 font-mono text-[10px] text-zinc-600">{formatTime(r.created_at)}</div>
          </div>
        ))}
      </div>

      <div className="overflow-hidden rounded-xl border border-zinc-800">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="bg-zinc-900/80 text-zinc-500">
              <th className="px-3 py-2 text-left font-medium">指标</th>
              <th className="px-3 py-2 text-right font-medium">A</th>
              <th className="px-3 py-2 text-right font-medium">B</th>
              <th className="px-3 py-2 text-right font-medium">Δ (B−A)</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/70">
            {rows.map((row) => (
              <tr key={row.label} className="text-zinc-300">
                <td className="px-3 py-1.5 text-zinc-400">{row.label}</td>
                <td className="px-3 py-1.5 text-right font-mono">{row.fmt(row.a)}</td>
                <td className="px-3 py-1.5 text-right font-mono">{row.fmt(row.b)}</td>
                <td className="px-3 py-1.5 text-right font-mono">
                  <DeltaCell a={row.a} b={row.b} format={row.fmt} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {toolNames.length > 0 && (
        <div className="mt-4">
          <div className="mb-2 text-xs font-medium text-zinc-300">工具调用分布</div>
          <div className="overflow-hidden rounded-xl border border-zinc-800">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="bg-zinc-900/80 text-zinc-500">
                  <th className="px-3 py-2 text-left font-medium">工具</th>
                  <th className="px-3 py-2 text-right font-medium">A</th>
                  <th className="px-3 py-2 text-right font-medium">B</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/70">
                {toolNames.map((name) => (
                  <tr key={name} className="text-zinc-300">
                    <td className="px-3 py-1.5 font-mono text-zinc-400">{name}</td>
                    <td className="px-3 py-1.5 text-right font-mono">{toolCount(a, name)}</td>
                    <td className="px-3 py-1.5 text-right font-mono">{toolCount(b, name)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <SpanDiffTable rows={comparison.span_diffs ?? []} />
    </div>
  );
}

function SpanDiffTable({ rows }: { rows: SpanDiffRow[] }) {
  if (rows.length === 0) {
    return (
      <div className="mt-4 rounded-xl border border-dashed border-zinc-800 px-3 py-6 text-center text-[11px] text-zinc-600">
        暂无 Span 可对比（两侧均无追踪数据）
      </div>
    );
  }
  const matchLabel = { both: "双侧", only_a: "仅 A", only_b: "仅 B" } as const;
  return (
    <div className="mt-4">
      <div className="mb-2 flex items-center justify-between">
        <div className="text-xs font-medium text-zinc-300">Span Diff</div>
        <div className="text-[10px] text-zinc-600">按 |Δ耗时| 排序，最多 80 行 · 同名按出现序对齐</div>
      </div>
      <div className="overflow-hidden rounded-xl border border-zinc-800">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="bg-zinc-900/80 text-zinc-500">
              <th className="px-3 py-2 text-left font-medium">名称</th>
              <th className="px-3 py-2 text-left font-medium">类型</th>
              <th className="px-3 py-2 text-left font-medium">对齐</th>
              <th className="px-3 py-2 text-right font-medium">A 耗时</th>
              <th className="px-3 py-2 text-right font-medium">B 耗时</th>
              <th className="px-3 py-2 text-right font-medium">Δ耗时</th>
              <th className="px-3 py-2 text-right font-medium">A tok</th>
              <th className="px-3 py-2 text-right font-medium">B tok</th>
              <th className="px-3 py-2 text-right font-medium">Δtok</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/70">
            {rows.map((row, i) => (
              <tr key={`${row.name}-${row.kind}-${row.match}-${i}`} className="text-zinc-300">
                <td className="max-w-[140px] truncate px-3 py-1.5 font-mono text-zinc-400" title={row.name}>
                  {row.name}
                </td>
                <td className="px-3 py-1.5 text-zinc-500">{row.kind}</td>
                <td className="px-3 py-1.5">
                  <span
                    className={
                      "rounded px-1 py-0.5 text-[10px] " +
                      (row.match === "both"
                        ? "bg-zinc-800 text-zinc-400"
                        : row.match === "only_a"
                          ? "bg-amber-500/15 text-amber-300"
                          : "bg-sky-500/15 text-sky-300")
                    }
                  >
                    {matchLabel[row.match]}
                  </span>
                </td>
                <td className="px-3 py-1.5 text-right font-mono">{fmtDuration(row.a?.duration_ms ?? null)}</td>
                <td className="px-3 py-1.5 text-right font-mono">{fmtDuration(row.b?.duration_ms ?? null)}</td>
                <td className="px-3 py-1.5 text-right font-mono">
                  <DeltaCell a={row.a?.duration_ms ?? null} b={row.b?.duration_ms ?? null} format={fmtDuration} />
                </td>
                <td className="px-3 py-1.5 text-right font-mono">{row.a ? row.a.tokens.toLocaleString() : "-"}</td>
                <td className="px-3 py-1.5 text-right font-mono">{row.b ? row.b.tokens.toLocaleString() : "-"}</td>
                <td className="px-3 py-1.5 text-right font-mono">
                  <DeltaCell
                    a={row.a?.tokens ?? null}
                    b={row.b?.tokens ?? null}
                    format={(v) => (v == null ? "-" : v.toLocaleString())}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
