import {
  Boxes,
  Calculator,
  Clock,
  Code2,
  Database,
  Globe,
  Play,
  Plug,
  Plus,
  Search,
  Trash2,
  Wrench,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge, Button, EmptyState, Input } from "../components/ui";
import { api } from "../lib/api";
import type { BuiltinTool, CustomToolInfo, CustomToolParam } from "../lib/types";

const BUILTIN_ICONS: Record<string, React.ReactNode> = {
  search_knowledge_base: <Database size={15} className="text-emerald-400" />,
  web_search: <Search size={15} className="text-sky-400" />,
  web_fetch: <Globe size={15} className="text-sky-400" />,
  calculator: <Calculator size={15} className="text-amber-400" />,
  current_time: <Clock size={15} className="text-amber-400" />,
  python_execute: <Code2 size={15} className="text-violet-400" />,
};

interface McpState {
  status: Record<string, string>;
  tools: { name: string; description: string; tags: string[] }[];
}

const EMPTY_FORM = {
  name: "",
  description: "",
  method: "GET",
  url_template: "",
  params_schema: [] as CustomToolParam[],
  headers: {} as Record<string, string>,
  body_template: "",
  enabled: true,
  timeout: 15,
};

export default function ToolsPage() {
  const [builtins, setBuiltins] = useState<BuiltinTool[]>([]);
  const [mcp, setMcp] = useState<McpState>({ status: {}, tools: [] });
  const [custom, setCustom] = useState<CustomToolInfo[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [testResult, setTestResult] = useState<Record<string, { ok: boolean; content: string }>>({});
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const [b, m, c] = await Promise.all([
      api.get<BuiltinTool[]>("/api/tools/builtin"),
      api.get<McpState>("/api/tools/mcp"),
      api.get<CustomToolInfo[]>("/api/tools/custom"),
    ]);
    setBuiltins(b);
    setMcp(m);
    setCustom(c);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const addParam = () =>
    setForm((f) => ({
      ...f,
      params_schema: [...f.params_schema, { name: "", type: "string", required: true, description: "", location: "query" }],
    }));

  const submit = async () => {
    setError("");
    try {
      await api.post("/api/tools/custom", form);
      setShowForm(false);
      setForm({ ...EMPTY_FORM });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    }
  };

  const remove = async (id: string) => {
    await api.delete(`/api/tools/custom/${id}`);
    load();
  };

  const test = async (t: CustomToolInfo) => {
    const args: Record<string, unknown> = {};
    for (const p of t.params_schema) args[p.name] = p.type === "number" || p.type === "integer" ? 1 : "test";
    try {
      const res = await api.post<{ ok: boolean; content: string }>(`/api/tools/custom/${t.id}/test`, {
        arguments: args,
      });
      setTestResult((r) => ({ ...r, [t.id]: res }));
    } catch (e) {
      setTestResult((r) => ({ ...r, [t.id]: { ok: false, content: e instanceof Error ? e.message : "失败" } }));
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-6 px-6 py-6">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold text-zinc-100">
            <Wrench size={19} className="text-indigo-400" /> 工具生态
          </h1>
          <p className="text-xs text-zinc-500">内置工具 · MCP 外部工具 · 自定义 HTTP 工具，均可被 Agent 自动调用</p>
        </div>

        {/* 内置工具 */}
        <section>
          <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold text-zinc-300">
            <Boxes size={15} /> 内置工具
          </h2>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {builtins.map((t) => (
              <div key={t.name} className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-3">
                <div className="mb-1 flex items-center gap-2">
                  {BUILTIN_ICONS[t.name] ?? <Wrench size={15} className="text-zinc-400" />}
                  <span className="font-mono text-[13px] text-zinc-200">{t.name}</span>
                  {t.requires_approval && <Badge tone="amber">需审批</Badge>}
                </div>
                <div className="line-clamp-2 text-[11px] leading-4 text-zinc-500">{t.description}</div>
              </div>
            ))}
          </div>
        </section>

        {/* MCP */}
        <section>
          <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold text-zinc-300">
            <Plug size={15} /> MCP 外部工具
            <span className="text-[11px] font-normal text-zinc-500">
              （对齐 Anthropic Model Context Protocol；配置 MCP_CONFIG_PATH 后接入）
            </span>
          </h2>
          {Object.keys(mcp.status).length === 0 ? (
            <div className="rounded-xl border border-dashed border-zinc-800 px-4 py-5 text-center text-xs text-zinc-600">
              未配置 MCP 服务器。示例：<span className="font-mono">backend/samples/mcp_server.py</span>
              ，在 <span className="font-mono">.env</span> 设 <span className="font-mono">MCP_CONFIG_PATH</span> 后重启后端即可接入。
            </div>
          ) : (
            <div className="space-y-2">
              {Object.entries(mcp.status).map(([name, st]) => (
                <div key={name} className="flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900/50 px-3 py-2">
                  <span className="font-mono text-[13px] text-zinc-200">{name}</span>
                  <Badge tone={st === "connected" ? "green" : "red"}>{st}</Badge>
                </div>
              ))}
              <div className="flex flex-wrap gap-1.5">
                {mcp.tools.map((t) => (
                  <span key={t.name} className="rounded-lg border border-zinc-700/70 bg-zinc-800/50 px-2 py-1 font-mono text-[11px] text-zinc-300" title={t.description}>
                    {t.name}
                  </span>
                ))}
              </div>
            </div>
          )}
        </section>

        {/* 自定义 HTTP 工具 */}
        <section>
          <div className="mb-2 flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-300">
              <Globe size={15} /> 自定义 HTTP 工具
            </h2>
            <Button size="sm" onClick={() => setShowForm(!showForm)}>
              <Plus size={13} /> 新建
            </Button>
          </div>

          {showForm && (
            <div className="mb-3 space-y-3 rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
              <div className="grid grid-cols-2 gap-2">
                <Input value={form.name} onChange={(v) => setForm({ ...form, name: v })} placeholder="工具名(英文，如 get_weather)" />
                <div className="flex gap-2">
                  <select
                    value={form.method}
                    onChange={(e) => setForm({ ...form, method: e.target.value })}
                    className="rounded-lg border border-zinc-700/80 bg-zinc-900 px-2 text-xs text-zinc-300"
                  >
                    {["GET", "POST", "PUT", "DELETE", "PATCH"].map((m) => (
                      <option key={m}>{m}</option>
                    ))}
                  </select>
                  <Input value={form.description} onChange={(v) => setForm({ ...form, description: v })} placeholder="功能描述（给 Agent 看）" />
                </div>
              </div>
              <Input
                value={form.url_template}
                onChange={(v) => setForm({ ...form, url_template: v })}
                placeholder="URL 模板，如 https://api.example.com/weather?city={city}"
              />
              <div>
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-xs text-zinc-500">参数（{"{name}"} 会填入 URL/Body）</span>
                  <button onClick={addParam} className="text-xs text-indigo-400 hover:text-indigo-300">
                    + 添加参数
                  </button>
                </div>
                {form.params_schema.map((p, i) => (
                  <div key={i} className="mb-1.5 flex gap-1.5">
                    <input
                      value={p.name}
                      onChange={(e) => {
                        const ps = [...form.params_schema];
                        ps[i] = { ...p, name: e.target.value };
                        setForm({ ...form, params_schema: ps });
                      }}
                      placeholder="参数名"
                      className="flex-1 rounded-lg border border-zinc-700/80 bg-zinc-900 px-2 py-1 text-xs text-zinc-200"
                    />
                    <select
                      value={p.type}
                      onChange={(e) => {
                        const ps = [...form.params_schema];
                        ps[i] = { ...p, type: e.target.value as CustomToolParam["type"] };
                        setForm({ ...form, params_schema: ps });
                      }}
                      className="rounded-lg border border-zinc-700/80 bg-zinc-900 px-1.5 text-xs text-zinc-300"
                    >
                      {["string", "number", "integer", "boolean"].map((t) => (
                        <option key={t}>{t}</option>
                      ))}
                    </select>
                    <input
                      value={p.description}
                      onChange={(e) => {
                        const ps = [...form.params_schema];
                        ps[i] = { ...p, description: e.target.value };
                        setForm({ ...form, params_schema: ps });
                      }}
                      placeholder="说明"
                      className="flex-1 rounded-lg border border-zinc-700/80 bg-zinc-900 px-2 py-1 text-xs text-zinc-200"
                    />
                  </div>
                ))}
              </div>
              {error && <div className="rounded-lg bg-rose-500/10 px-3 py-1.5 text-xs text-rose-300">{error}</div>}
              <div className="flex justify-end gap-2">
                <Button variant="ghost" size="sm" onClick={() => setShowForm(false)}>
                  取消
                </Button>
                <Button size="sm" onClick={submit} disabled={!form.name || !form.url_template}>
                  创建
                </Button>
              </div>
            </div>
          )}

          {custom.length === 0 && !showForm ? (
            <EmptyState icon={<Globe size={24} />} title="还没有自定义工具" desc="把任意 HTTP API 封装成 Agent 可调用的工具" />
          ) : (
            <div className="space-y-2">
              {custom.map((t) => (
                <div key={t.id} className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-3">
                  <div className="flex items-center gap-2">
                    <Badge tone="sky">{t.method}</Badge>
                    <span className="font-mono text-[13px] text-zinc-200">{t.name}</span>
                    {!t.enabled && <Badge tone="zinc">已禁用</Badge>}
                    <div className="flex-1" />
                    <Button size="sm" variant="outline" onClick={() => test(t)}>
                      <Play size={12} /> 测试
                    </Button>
                    <button onClick={() => remove(t.id)} className="rounded p-1 text-zinc-600 hover:text-rose-400">
                      <Trash2 size={13} />
                    </button>
                  </div>
                  <div className="mt-1 font-mono text-[11px] text-zinc-500">{t.url_template}</div>
                  {testResult[t.id] && (
                    <pre className="mt-2 max-h-32 overflow-auto rounded bg-zinc-950 p-2 font-mono text-[11px] whitespace-pre-wrap text-zinc-400">
                      {testResult[t.id].ok ? "" : "[失败] "}
                      {testResult[t.id].content}
                    </pre>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
