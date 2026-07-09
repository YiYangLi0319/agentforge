import {
  BookOpenText,
  Database,
  FileText,
  FlaskConical,
  Plus,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge, Button, EmptyState, Input, statusTone, STATUS_LABEL } from "../components/ui";
import { api } from "../lib/api";
import type { DocumentInfo, KnowledgeBaseInfo, RetrievedChunkInfo } from "../lib/types";

export default function KnowledgePage() {
  const [kbs, setKbs] = useState<KnowledgeBaseInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  // 检索 playground
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"hybrid" | "vector" | "keyword">("hybrid");
  const [results, setResults] = useState<RetrievedChunkInfo[] | null>(null);
  const [searching, setSearching] = useState(false);

  const loadKbs = useCallback(async () => {
    const list = await api.get<KnowledgeBaseInfo[]>("/api/kb");
    setKbs(list);
    return list;
  }, []);

  const loadDocs = useCallback(async (kbId: string) => {
    setDocs(await api.get<DocumentInfo[]>(`/api/kb/${kbId}/documents`));
  }, []);

  useEffect(() => {
    loadKbs().then((list) => setSelected((prev) => prev ?? list[0]?.id ?? null));
  }, [loadKbs]);

  useEffect(() => {
    if (!selected) return;
    setResults(null);
    loadDocs(selected);
  }, [selected, loadDocs]);

  // 有处理中的文档时轮询状态
  useEffect(() => {
    if (!selected || !docs.some((d) => d.status === "pending" || d.status === "processing")) return;
    const timer = setInterval(() => {
      loadDocs(selected);
      loadKbs();
    }, 1200);
    return () => clearInterval(timer);
  }, [docs, selected, loadDocs, loadKbs]);

  const createKb = async () => {
    if (!newName.trim()) return;
    const kb = await api.post<{ id: string }>("/api/kb", { name: newName.trim(), description: newDesc });
    setShowCreate(false);
    setNewName("");
    setNewDesc("");
    await loadKbs();
    setSelected(kb.id);
  };

  const upload = async (files: FileList | null) => {
    if (!files?.length || !selected) return;
    setUploading(true);
    try {
      const form = new FormData();
      for (const f of Array.from(files)) form.append("files", f);
      await api.postForm(`/api/kb/${selected}/documents`, form);
      await loadDocs(selected);
    } catch (e) {
      alert(e instanceof Error ? e.message : "上传失败");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const loadSamples = async () => {
    if (!selected) return;
    await api.post(`/api/kb/${selected}/load-samples`);
    await loadDocs(selected);
  };

  const search = async () => {
    if (!query.trim() || !selected) return;
    setSearching(true);
    try {
      const resp = await api.post<{ results: RetrievedChunkInfo[] }>(`/api/kb/${selected}/search`, {
        query: query.trim(),
        top_k: 5,
        mode,
      });
      setResults(resp.results);
    } finally {
      setSearching(false);
    }
  };

  const maxScore = Math.max(...(results ?? []).map((r) => r.final_score), 0.000001);

  return (
    <div className="flex h-full">
      {/* 知识库列表 */}
      <div className="flex w-64 shrink-0 flex-col border-r border-zinc-800/80">
        <div className="p-3">
          <Button onClick={() => setShowCreate(true)} className="w-full" size="sm">
            <Plus size={14} /> 新建知识库
          </Button>
        </div>
        <div className="flex-1 space-y-1 overflow-y-auto px-2 pb-2">
          {kbs.map((kb) => (
            <button
              key={kb.id}
              onClick={() => setSelected(kb.id)}
              className={
                "w-full rounded-lg px-3 py-2.5 text-left " +
                (selected === kb.id ? "bg-zinc-800" : "hover:bg-zinc-800/50")
              }
            >
              <div className="flex items-center gap-2">
                <Database size={14} className="shrink-0 text-emerald-400" />
                <span className="flex-1 truncate text-[13px] text-zinc-200">{kb.name}</span>
              </div>
              <div className="mt-1 pl-6 text-[10px] text-zinc-500">
                {kb.doc_count} 文档 · {kb.chunk_count} 分块
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* 主区 */}
      <div className="min-w-0 flex-1 overflow-y-auto">
        {selected ? (
          <div className="mx-auto max-w-4xl space-y-6 px-6 py-6">
            {/* 文档管理 */}
            <section>
              <div className="mb-3 flex items-center justify-between">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-200">
                  <FileText size={15} className="text-indigo-400" /> 文档
                </h2>
                <div className="flex gap-2">
                  <Button size="sm" variant="outline" onClick={loadSamples}>
                    <Sparkles size={13} /> 导入演示样例
                  </Button>
                  <Button size="sm" onClick={() => fileRef.current?.click()} loading={uploading}>
                    <Upload size={13} /> 上传文档
                  </Button>
                  <input
                    ref={fileRef}
                    type="file"
                    multiple
                    accept=".pdf,.docx,.md,.markdown,.txt"
                    className="hidden"
                    onChange={(e) => upload(e.target.files)}
                  />
                </div>
              </div>
              <div className="overflow-hidden rounded-xl border border-zinc-800">
                {docs.length === 0 ? (
                  <div className="px-4 py-8 text-center text-xs text-zinc-600">
                    暂无文档。支持 PDF / Word / Markdown / TXT，上传后自动解析分块并建立混合索引。
                  </div>
                ) : (
                  <table className="w-full text-[13px]">
                    <thead>
                      <tr className="border-b border-zinc-800 bg-zinc-900/70 text-left text-[11px] text-zinc-500">
                        <th className="px-4 py-2.5 font-medium">文件名</th>
                        <th className="px-3 py-2.5 font-medium">状态</th>
                        <th className="px-3 py-2.5 font-medium">分块</th>
                        <th className="px-3 py-2.5 font-medium">大小</th>
                        <th className="px-3 py-2.5" />
                      </tr>
                    </thead>
                    <tbody>
                      {docs.map((d) => (
                        <tr key={d.id} className="border-b border-zinc-800/60 last:border-0 hover:bg-zinc-900/40">
                          <td className="max-w-64 truncate px-4 py-2.5 text-zinc-300">{d.filename}</td>
                          <td className="px-3 py-2.5">
                            <Badge tone={statusTone(d.status)}>{STATUS_LABEL[d.status] ?? d.status}</Badge>
                            {d.status === "failed" && (
                              <div className="mt-0.5 max-w-52 truncate text-[10px] text-rose-400" title={d.error}>
                                {d.error}
                              </div>
                            )}
                          </td>
                          <td className="px-3 py-2.5 font-mono text-zinc-400">{d.chunk_count}</td>
                          <td className="px-3 py-2.5 font-mono text-zinc-500">{(d.size / 1024).toFixed(1)}KB</td>
                          <td className="px-3 py-2.5 text-right">
                            <button
                              onClick={() =>
                                api.delete(`/api/kb/${selected}/documents/${d.id}`).then(() => loadDocs(selected))
                              }
                              className="rounded p-1 text-zinc-600 hover:text-rose-400"
                            >
                              <Trash2 size={13} />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </section>

            {/* 检索 Playground */}
            <section>
              <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-200">
                <FlaskConical size={15} className="text-violet-400" /> 检索 Playground
                <span className="text-[11px] font-normal text-zinc-500">对比向量 / BM25 / 混合检索的召回效果与评分</span>
              </h2>
              <div className="flex gap-2">
                <Input
                  value={query}
                  onChange={setQuery}
                  placeholder="输入检索问题，如：报销时限是多少天"
                  onKeyDown={(e) => e.key === "Enter" && search()}
                />
                <select
                  value={mode}
                  onChange={(e) => setMode(e.target.value as typeof mode)}
                  className="rounded-lg border border-zinc-700/80 bg-zinc-900 px-2 text-xs text-zinc-300 focus:outline-none"
                >
                  <option value="hybrid">混合检索</option>
                  <option value="vector">纯向量</option>
                  <option value="keyword">纯 BM25</option>
                </select>
                <Button onClick={search} loading={searching}>
                  检索
                </Button>
              </div>

              {results !== null && (
                <div className="mt-3 space-y-2">
                  {results.length === 0 && (
                    <div className="rounded-xl border border-zinc-800 px-4 py-6 text-center text-xs text-zinc-600">
                      没有检索到相关内容
                    </div>
                  )}
                  {results.map((r, i) => (
                    <div key={r.chunk_id} className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-3.5">
                      <div className="mb-1.5 flex items-center gap-2 text-[11px] text-zinc-500">
                        <span className="flex h-5 w-5 items-center justify-center rounded bg-indigo-500/20 font-mono font-semibold text-indigo-300">
                          {i + 1}
                        </span>
                        <span className="text-zinc-400">{r.filename}</span>
                        {r.heading && <span className="truncate">· {r.heading}</span>}
                      </div>
                      <div className="mb-2 line-clamp-3 text-[13px] leading-6 text-zinc-300">{r.content}</div>
                      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-zinc-500">
                        <ScoreBar label="综合" value={r.final_score / maxScore} display={r.final_score.toFixed(4)} color="bg-indigo-500" />
                        <ScoreBar label="向量" value={Math.max(r.vector_score, 0)} display={r.vector_score.toFixed(3)} color="bg-sky-500" />
                        <ScoreBar label="BM25" value={Math.min(r.bm25_score / 10, 1)} display={r.bm25_score.toFixed(2)} color="bg-emerald-500" />
                        {r.rerank_score !== null && (
                          <ScoreBar label="重排" value={r.rerank_score} display={r.rerank_score.toFixed(3)} color="bg-violet-500" />
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </div>
        ) : (
          <EmptyState
            icon={<BookOpenText size={28} />}
            title="创建知识库开始"
            desc="上传企业文档，自动解析分块、向量化并建立 BM25 索引；支持在对话中绑定并引用溯源"
          />
        )}
      </div>

      {/* 新建弹窗 */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setShowCreate(false)}>
          <div className="w-96 rounded-2xl border border-zinc-800 bg-zinc-900 p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="mb-4 text-sm font-semibold text-zinc-100">新建知识库</h3>
            <div className="space-y-3">
              <Input value={newName} onChange={setNewName} placeholder="名称，如：公司制度库" />
              <Input value={newDesc} onChange={setNewDesc} placeholder="描述（可选）" />
              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setShowCreate(false)}>
                  取消
                </Button>
                <Button onClick={createKb} disabled={!newName.trim()}>
                  创建
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ScoreBar({
  label,
  value,
  display,
  color,
}: {
  label: string;
  value: number;
  display: string;
  color: string;
}) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span>{label}</span>
      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-zinc-800">
        <span className={`block h-full rounded-full ${color}`} style={{ width: `${Math.min(value, 1) * 100}%` }} />
      </span>
      <span className="font-mono">{display}</span>
    </span>
  );
}
