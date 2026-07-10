import { BarChart3, Database, FileSpreadsheet, SendHorizonal, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import Markdown from "../components/Markdown";
import { Badge, Button, EmptyState } from "../components/ui";
import { api } from "../lib/api";
import type { AnalyzeResult, DatasetInfo } from "../lib/types";

export default function DataPage() {
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [active, setActive] = useState<DatasetInfo | null>(null);
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AnalyzeResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setDatasets(await api.get<DatasetInfo[]>("/api/datasets"));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openDataset = async (id: string) => {
    setResult(null);
    setError("");
    setActive(await api.get<DatasetInfo>(`/api/datasets/${id}`));
  };

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    setError("");
    try {
      const form = new FormData();
      form.append("file", files[0]);
      const ds = await api.postForm<DatasetInfo>("/api/datasets", form);
      await load();
      setActive(ds);
    } catch (e) {
      setError(e instanceof Error ? e.message : "上传失败");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const analyze = async () => {
    if (!active || !question.trim()) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const r = await api.post<AnalyzeResult>(`/api/datasets/${active.id}/analyze`, { question: question.trim() });
      setResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "分析失败");
    } finally {
      setLoading(false);
    }
  };

  const remove = async (id: string) => {
    await api.delete(`/api/datasets/${id}`);
    if (active?.id === id) setActive(null);
    load();
  };

  const chartData =
    result && !result.error && result.chart.type !== "table" && result.chart.x && result.chart.y
      ? result.result.rows.map((row) => {
          const obj: Record<string, unknown> = {};
          result.result.columns.forEach((c, i) => (obj[c] = row[i]));
          return obj;
        })
      : null;

  return (
    <div className="flex h-full">
      {/* 数据集列表 */}
      <div className="flex w-64 shrink-0 flex-col border-r border-zinc-800/80">
        <div className="p-3">
          <Button className="w-full" size="sm" onClick={() => fileRef.current?.click()} loading={uploading}>
            <Upload size={14} /> 上传 CSV
          </Button>
          <input ref={fileRef} type="file" accept=".csv" className="hidden" onChange={(e) => upload(e.target.files)} />
        </div>
        <div className="flex-1 space-y-1 overflow-y-auto px-2 pb-2">
          {datasets.map((d) => (
            <div
              key={d.id}
              onClick={() => openDataset(d.id)}
              className={
                "group flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 " +
                (active?.id === d.id ? "bg-zinc-800" : "hover:bg-zinc-800/50")
              }
            >
              <FileSpreadsheet size={14} className="shrink-0 text-emerald-400" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] text-zinc-200">{d.name}</div>
                <div className="text-[10px] text-zinc-500">
                  {d.row_count} 行 · {d.columns.length} 列
                </div>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  remove(d.id);
                }}
                className="hidden text-zinc-600 hover:text-rose-400 group-hover:block"
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* 分析区 */}
      <div className="min-w-0 flex-1 overflow-y-auto">
        {active ? (
          <div className="mx-auto max-w-4xl space-y-4 px-6 py-6">
            <div>
              <h1 className="flex items-center gap-2 text-lg font-semibold text-zinc-100">
                <BarChart3 size={19} className="text-violet-400" /> {active.name}
              </h1>
              <div className="mt-1 flex flex-wrap gap-1">
                {active.columns.map((c) => (
                  <Badge key={c.name} tone="zinc">
                    {c.name}: {c.type}
                  </Badge>
                ))}
              </div>
            </div>

            <div className="flex gap-2">
              <input
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && analyze()}
                placeholder="用自然语言提问，如：各城市销售额排名 / 每季度总量趋势"
                className="flex-1 rounded-xl border border-zinc-700/80 bg-zinc-900 px-4 py-2.5 text-sm text-zinc-200 placeholder:text-zinc-600 focus:border-indigo-500 focus:outline-none"
              />
              <Button onClick={analyze} loading={loading} disabled={!question.trim()}>
                <SendHorizonal size={15} /> 分析
              </Button>
            </div>

            {error && <div className="rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-300">{error}</div>}

            {result?.error && (
              <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-300">
                {result.error}
                <pre className="mt-2 overflow-x-auto rounded bg-zinc-950 p-2 font-mono text-[11px]">{result.sql}</pre>
              </div>
            )}

            {result && !result.error && (
              <>
                <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
                  <Markdown content={result.summary} />
                  <details className="mt-2">
                    <summary className="cursor-pointer text-[11px] text-zinc-500">查看生成的 SQL</summary>
                    <pre className="mt-1 overflow-x-auto rounded bg-zinc-950 p-2 font-mono text-[11px] text-emerald-300">
                      {result.sql}
                    </pre>
                  </details>
                </div>

                {chartData && (
                  <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
                    <ResponsiveContainer width="100%" height={260}>
                      {result.chart.type === "line" ? (
                        <LineChart data={chartData}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                          <XAxis dataKey={result.chart.x} stroke="#71717a" fontSize={11} />
                          <YAxis stroke="#71717a" fontSize={11} />
                          <Tooltip contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 8, fontSize: 12 }} />
                          <Line type="monotone" dataKey={result.chart.y} stroke="#a855f7" strokeWidth={2} />
                        </LineChart>
                      ) : (
                        <BarChart data={chartData}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                          <XAxis dataKey={result.chart.x} stroke="#71717a" fontSize={11} />
                          <YAxis stroke="#71717a" fontSize={11} />
                          <Tooltip
                            contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 8, fontSize: 12 }}
                            cursor={{ fill: "#27272a55" }}
                          />
                          <Bar dataKey={result.chart.y} fill="#8b5cf6" radius={[4, 4, 0, 0]} />
                        </BarChart>
                      )}
                    </ResponsiveContainer>
                  </div>
                )}

                <ResultTable columns={result.result.columns} rows={result.result.rows} />
              </>
            )}

            {/* 数据预览 */}
            {!result && active.preview && active.preview.length > 0 && (
              <div>
                <div className="mb-2 text-xs text-zinc-500">数据预览（前 {active.preview.length} 行）</div>
                <ResultTable
                  columns={active.columns.map((c) => c.name)}
                  rows={active.preview.map((r) => active.columns.map((c) => r[c.name]))}
                />
              </div>
            )}
          </div>
        ) : (
          <EmptyState
            icon={<Database size={28} />}
            title="数据分析 Agent"
            desc="上传 CSV → 用自然语言提问 → 自动生成 SQL、执行并出图表与结论（Text2SQL）"
          />
        )}
      </div>
    </div>
  );
}

function ResultTable({ columns, rows }: { columns: string[]; rows: unknown[][] }) {
  if (!columns.length) return null;
  return (
    <div className="overflow-x-auto rounded-xl border border-zinc-800">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-b border-zinc-800 bg-zinc-900/70 text-left text-[11px] text-zinc-500">
            {columns.map((c) => (
              <th key={c} className="px-3 py-2 font-medium">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 100).map((row, i) => (
            <tr key={i} className="border-b border-zinc-800/60 last:border-0">
              {row.map((cell, j) => (
                <td key={j} className="px-3 py-1.5 text-zinc-300">
                  {cell === null || cell === undefined ? "-" : String(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
