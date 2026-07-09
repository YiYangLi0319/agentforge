import {
  Activity,
  Coins,
  Cpu,
  Database,
  Gauge,
  RefreshCw,
  ShieldCheck,
  Timer,
  Trash2,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Badge, Button, formatCost } from "../components/ui";
import { api } from "../lib/api";
import type { DashboardStats } from "../lib/types";

const KIND_COLORS: Record<string, string> = { chat: "#6366f1", research: "#a855f7" };
const STATUS_COLORS: Record<string, string> = {
  succeeded: "#10b981",
  failed: "#f43f5e",
  cancelled: "#71717a",
  running: "#6366f1",
};

function Card({
  icon,
  label,
  value,
  sub,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
      <div className="mb-2 flex items-center gap-2 text-xs text-zinc-500">
        {icon}
        {label}
      </div>
      <div className="text-2xl font-semibold text-zinc-100">{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-zinc-500">{sub}</div>}
    </div>
  );
}

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStats(await api.get<DashboardStats>("/api/dashboard/stats"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const clearCache = async () => {
    await api.post("/api/dashboard/cache/clear");
    load();
  };

  if (!stats) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-zinc-500">
        {loading ? "加载中…" : "暂无数据"}
      </div>
    );
  }

  const t = stats.totals;
  const cap = stats.capabilities;
  const kindData = Object.entries(stats.by_kind).map(([k, v]) => ({ name: k, value: v }));
  const statusData = Object.entries(stats.by_status).map(([k, v]) => ({ name: k, value: v }));

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl space-y-5 px-6 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="flex items-center gap-2 text-lg font-semibold text-zinc-100">
              <Gauge size={19} className="text-indigo-400" /> 可观测看板
            </h1>
            <p className="text-xs text-zinc-500">近 {stats.range_days} 天的用量、成本、延迟与系统能力</p>
          </div>
          <Button variant="ghost" size="sm" onClick={load} loading={loading}>
            <RefreshCw size={13} /> 刷新
          </Button>
        </div>

        {/* 概览卡片 */}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Card icon={<Activity size={13} />} label="总运行数" value={String(t.runs)} sub={`成功率 ${(t.success_rate * 100).toFixed(0)}%`} />
          <Card icon={<Cpu size={13} />} label="Token 总量" value={t.total_tokens.toLocaleString()} sub={`输入 ${t.prompt_tokens.toLocaleString()} / 输出 ${t.completion_tokens.toLocaleString()}`} />
          <Card icon={<Coins size={13} />} label="累计成本" value={formatCost(t.cost)} sub="按模型定价估算" />
          <Card icon={<Timer size={13} />} label="平均延迟" value={`${t.avg_latency_s}s`} sub="每次运行" />
        </div>

        {/* 趋势 */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
          <div className="mb-3 text-sm font-medium text-zinc-200">用量趋势</div>
          {stats.trend.length === 0 ? (
            <div className="py-10 text-center text-xs text-zinc-600">暂无数据</div>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={stats.trend} margin={{ top: 5, right: 10, left: -10, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                <XAxis dataKey="day" stroke="#71717a" fontSize={11} />
                <YAxis stroke="#71717a" fontSize={11} />
                <Tooltip
                  contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 8, fontSize: 12 }}
                  labelStyle={{ color: "#e4e4e7" }}
                />
                <Line type="monotone" dataKey="runs" name="运行数" stroke="#6366f1" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="tokens" name="tokens" stroke="#a855f7" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          {/* 工具使用 */}
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
            <div className="mb-3 text-sm font-medium text-zinc-200">工具使用 Top</div>
            {stats.tool_usage.length === 0 ? (
              <div className="py-10 text-center text-xs text-zinc-600">暂无工具调用</div>
            ) : (
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={stats.tool_usage} layout="vertical" margin={{ left: 30, right: 10 }}>
                  <XAxis type="number" stroke="#71717a" fontSize={11} />
                  <YAxis type="category" dataKey="tool" stroke="#71717a" fontSize={10} width={110} />
                  <Tooltip
                    contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 8, fontSize: 12 }}
                    cursor={{ fill: "#27272a55" }}
                  />
                  <Bar dataKey="count" name="调用次数" fill="#10b981" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>

          {/* 运行分布 */}
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
            <div className="mb-3 text-sm font-medium text-zinc-200">运行分布</div>
            <div className="grid grid-cols-2 gap-3">
              <MiniBars title="按类型" data={kindData} colors={KIND_COLORS} />
              <MiniBars title="按状态" data={statusData} colors={STATUS_COLORS} />
            </div>
          </div>
        </div>

        {/* 缓存 + 能力 */}
        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
            <div className="mb-3 flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm font-medium text-zinc-200">
                <Zap size={15} className="text-amber-400" /> 语义缓存
              </div>
              <Button size="sm" variant="outline" onClick={clearCache}>
                <Trash2 size={12} /> 清空
              </Button>
            </div>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <Stat label="缓存条目" value={String(stats.cache.entries)} />
              <Stat label="本次会话命中率" value={`${(stats.cache.hit_rate * 100).toFixed(0)}%`} />
              <Stat label="命中 / 未命中" value={`${stats.cache.session_hits} / ${stats.cache.session_misses}`} />
              <Stat label="累计命中" value={String(stats.cache.lifetime_hits)} />
            </div>
          </div>

          <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-medium text-zinc-200">
              <ShieldCheck size={15} className="text-emerald-400" /> 系统能力
            </div>
            <div className="space-y-2 text-[13px]">
              <CapRow label="对话模型" value={`${cap.llm.provider} / ${cap.llm.model}`} />
              <CapRow label="Embedding" value={`${cap.embedding.provider} / ${cap.embedding.model || "-"}`} />
              <CapRow
                label="安全护栏"
                value={cap.guardrails_enabled ? "启用" : "关闭"}
                tone={cap.guardrails_enabled ? "green" : "zinc"}
              />
              <CapRow
                label="语义缓存"
                value={cap.semantic_cache_enabled ? "启用" : "关闭"}
                tone={cap.semantic_cache_enabled ? "green" : "zinc"}
              />
              <CapRow
                label="MCP 工具"
                value={`${cap.mcp_tools} 个 / ${Object.keys(cap.mcp_servers).length} 服务器`}
                tone={cap.mcp_tools > 0 ? "indigo" : "zinc"}
              />
              <div className="flex items-center gap-1.5 pt-1">
                <Database size={12} className="text-zinc-500" />
                <span className="text-xs text-zinc-500">RAG 增强：</span>
                {cap.rag.parent_child && <Badge tone="green">父子分块</Badge>}
                {cap.rag.query_rewrite && <Badge tone="indigo">查询改写</Badge>}
                {cap.rag.hyde && <Badge tone="indigo">HyDE</Badge>}
                {cap.rag.compression && <Badge tone="indigo">上下文压缩</Badge>}
              </div>
            </div>
          </div>
        </div>

        <div className="text-center text-[11px] text-zinc-600">
          Prometheus 指标可在 <span className="font-mono text-zinc-500">/api/dashboard/metrics</span> 抓取
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[11px] text-zinc-500">{label}</div>
      <div className="font-mono text-lg text-zinc-100">{value}</div>
    </div>
  );
}

function CapRow({ label, value, tone }: { label: string; value: string; tone?: "green" | "indigo" | "zinc" }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-zinc-500">{label}</span>
      {tone ? <Badge tone={tone}>{value}</Badge> : <span className="font-mono text-zinc-300">{value}</span>}
    </div>
  );
}

function MiniBars({
  title,
  data,
  colors,
}: {
  title: string;
  data: { name: string; value: number }[];
  colors: Record<string, string>;
}) {
  return (
    <div>
      <div className="mb-1 text-[11px] text-zinc-500">{title}</div>
      {data.length === 0 ? (
        <div className="py-6 text-center text-[11px] text-zinc-600">-</div>
      ) : (
        <ResponsiveContainer width="100%" height={130}>
          <BarChart data={data} margin={{ top: 5, right: 5, left: -20, bottom: 0 }}>
            <XAxis dataKey="name" stroke="#71717a" fontSize={10} />
            <YAxis stroke="#71717a" fontSize={10} allowDecimals={false} />
            <Tooltip
              contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 8, fontSize: 12 }}
              cursor={{ fill: "#27272a55" }}
            />
            <Bar dataKey="value" radius={[4, 4, 0, 0]}>
              {data.map((d) => (
                <Cell key={d.name} fill={colors[d.name] ?? "#6366f1"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
