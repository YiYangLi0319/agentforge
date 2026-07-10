import { Bot, Plus, Sparkles, Trash2, Wrench } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge, Button, EmptyState, Input } from "../components/ui";
import { api } from "../lib/api";
import type { CustomAgentInfo, KnowledgeBaseInfo } from "../lib/types";

interface ToolOption {
  name: string;
  description: string;
}

const EMPTY = {
  name: "",
  description: "",
  system_prompt: "",
  tools: [] as string[],
  kb_ids: [] as string[],
  max_steps: 8,
  temperature: 0.3,
};

export default function AgentsPage() {
  const [agents, setAgents] = useState<CustomAgentInfo[]>([]);
  const [toolOptions, setToolOptions] = useState<ToolOption[]>([]);
  const [kbs, setKbs] = useState<KnowledgeBaseInfo[]>([]);
  const [form, setForm] = useState({ ...EMPTY });
  const [editingId, setEditingId] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const [a, t, k] = await Promise.all([
      api.get<CustomAgentInfo[]>("/api/agents"),
      api.get<ToolOption[]>("/api/agents/tools"),
      api.get<KnowledgeBaseInfo[]>("/api/kb"),
    ]);
    setAgents(a);
    setToolOptions(t);
    setKbs(k);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openNew = () => {
    setForm({ ...EMPTY });
    setEditingId(null);
    setError("");
    setShowForm(true);
  };

  const openEdit = (a: CustomAgentInfo) => {
    setForm({
      name: a.name,
      description: a.description,
      system_prompt: a.system_prompt,
      tools: a.tools,
      kb_ids: a.kb_ids,
      max_steps: a.max_steps,
      temperature: a.temperature,
    });
    setEditingId(a.id);
    setError("");
    setShowForm(true);
  };

  const toggle = (list: string[], value: string) =>
    list.includes(value) ? list.filter((x) => x !== value) : [...list, value];

  const submit = async () => {
    setError("");
    try {
      if (editingId) await api.patch(`/api/agents/${editingId}`, form);
      else await api.post("/api/agents", form);
      setShowForm(false);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    }
  };

  const remove = async (id: string) => {
    await api.delete(`/api/agents/${id}`);
    load();
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-5 px-6 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="flex items-center gap-2 text-lg font-semibold text-zinc-100">
              <Sparkles size={19} className="text-indigo-400" /> 自定义 Agent
            </h1>
            <p className="text-xs text-zinc-500">自定义人设、工具与知识库，创建后可在对话中选用</p>
          </div>
          <Button size="sm" onClick={openNew}>
            <Plus size={14} /> 新建 Agent
          </Button>
        </div>

        {showForm && (
          <div className="space-y-3 rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
            <div className="grid grid-cols-2 gap-2">
              <Input value={form.name} onChange={(v) => setForm({ ...form, name: v })} placeholder="Agent 名称" />
              <Input
                value={form.description}
                onChange={(v) => setForm({ ...form, description: v })}
                placeholder="一句话描述"
              />
            </div>
            <textarea
              value={form.system_prompt}
              onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
              rows={4}
              placeholder="系统提示词（人设）：例如 你是资深法律顾问，回答严谨、引用条款…"
              className="w-full resize-none rounded-lg border border-zinc-700/80 bg-zinc-900 px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600 focus:border-indigo-500 focus:outline-none"
            />
            <div>
              <div className="mb-1.5 text-xs text-zinc-500">工具</div>
              <div className="flex flex-wrap gap-1.5">
                {toolOptions.map((t) => (
                  <button
                    key={t.name}
                    title={t.description}
                    onClick={() => setForm({ ...form, tools: toggle(form.tools, t.name) })}
                    className={
                      "rounded-lg border px-2.5 py-1 font-mono text-[11px] transition-colors " +
                      (form.tools.includes(t.name)
                        ? "border-indigo-500 bg-indigo-500/15 text-indigo-300"
                        : "border-zinc-700/80 text-zinc-400 hover:border-zinc-600")
                    }
                  >
                    {t.name}
                  </button>
                ))}
              </div>
            </div>
            {kbs.length > 0 && (
              <div>
                <div className="mb-1.5 text-xs text-zinc-500">绑定知识库</div>
                <div className="flex flex-wrap gap-1.5">
                  {kbs.map((kb) => (
                    <button
                      key={kb.id}
                      onClick={() => setForm({ ...form, kb_ids: toggle(form.kb_ids, kb.id) })}
                      className={
                        "rounded-lg border px-2.5 py-1 text-[12px] transition-colors " +
                        (form.kb_ids.includes(kb.id)
                          ? "border-emerald-500 bg-emerald-500/15 text-emerald-300"
                          : "border-zinc-700/80 text-zinc-400 hover:border-zinc-600")
                      }
                    >
                      {kb.name}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <div className="flex items-center gap-4 text-xs text-zinc-500">
              <label className="flex items-center gap-2">
                最大步数
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={form.max_steps}
                  onChange={(e) => setForm({ ...form, max_steps: Number(e.target.value) })}
                  className="w-16 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-zinc-200"
                />
              </label>
              <label className="flex items-center gap-2">
                温度
                <input
                  type="number"
                  min={0}
                  max={2}
                  step={0.1}
                  value={form.temperature}
                  onChange={(e) => setForm({ ...form, temperature: Number(e.target.value) })}
                  className="w-16 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-zinc-200"
                />
              </label>
            </div>
            {error && <div className="rounded-lg bg-rose-500/10 px-3 py-1.5 text-xs text-rose-300">{error}</div>}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setShowForm(false)}>
                取消
              </Button>
              <Button size="sm" onClick={submit} disabled={!form.name}>
                {editingId ? "保存" : "创建"}
              </Button>
            </div>
          </div>
        )}

        {agents.length === 0 && !showForm ? (
          <EmptyState icon={<Bot size={26} />} title="还没有自定义 Agent" desc="创建你自己的专属 Agent，设定人设、工具与知识库" />
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {agents.map((a) => (
              <div key={a.id} className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-3.5">
                <div className="mb-1 flex items-center gap-2">
                  <Bot size={15} className="text-indigo-400" />
                  <span className="flex-1 truncate text-[14px] font-medium text-zinc-200">{a.name}</span>
                  <button onClick={() => openEdit(a)} className="text-[11px] text-zinc-500 hover:text-indigo-400">
                    编辑
                  </button>
                  <button onClick={() => remove(a.id)} className="text-zinc-600 hover:text-rose-400">
                    <Trash2 size={13} />
                  </button>
                </div>
                <div className="mb-2 line-clamp-2 text-[11px] text-zinc-500">{a.description || "（无描述）"}</div>
                <div className="flex flex-wrap gap-1">
                  {a.tools.map((t) => (
                    <Badge key={t} tone="indigo">
                      <Wrench size={9} /> {t}
                    </Badge>
                  ))}
                  {a.kb_ids.length > 0 && <Badge tone="green">{a.kb_ids.length} 知识库</Badge>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
