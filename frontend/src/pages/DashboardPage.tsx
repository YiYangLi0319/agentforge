import {
  Activity,
  Coins,
  Cpu,
  Database,
  Gauge,
  Radio,
  RefreshCw,
  Repeat,
  ShieldCheck,
  Timer,
  Trash2,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Area,
  AreaChart,
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
import type { DashboardStats, LiveSeries } from "../lib/types";

const LIVE_MINUTES = 30;
const LIVE_POLL_MS = 3000;

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
  const [live, setLive] = useState<LiveSeries | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const liveTimer = useRef<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStats(await api.get<DashboardStats>("/api/dashboard/stats"));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadLive = useCallback(async () => {
    try {
      setLive(await api.get<LiveSeries>(`/api/dashboard/live?minutes=${LIVE_MINUTES}&buckets=30`));
    } catch {
      /* 实时曲线为增强项，失败静默 */
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!autoRefresh) {
      if (liveTimer.current) window.clearInterval(liveTimer.current);
      return;
    }
    loadLive();
    liveTimer.current = window.setInterval(loadLive, LIVE_POLL_MS);
    return () => {
      if (liveTimer.current) window.clearInterval(liveTimer.current);
    };
  }, [autoRefresh, loadLive]);

  const clearCache = async () => {
    await api.post("/api/dashboard/cache/clear");
    load();
    loadLive();
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

        {/* 实时观测 */}
        <LivePanel live={live} autoRefresh={autoRefresh} onToggle={() => setAutoRefresh((v) => !v)} />

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

function LivePanel({
  live,
  autoRefresh,
  onToggle,
}: {
  live: LiveSeries | null;
  autoRefresh: boolean;
  onToggle: () => void;
}) {
  const points = (live?.points ?? []).map((p) => ({
    ...p,
    hit_pct: p.hit_rate == null ? null : Math.round(p.hit_rate * 100),
  }));
  const s = live?.summary;
  const hasActivity = points.some((p) => p.runs > 0 || p.cache_hits + p.cache_misses > 0);

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-medium text-zinc-200">
          <Radio size={15} className={autoRefresh ? "text-emerald-400" : "text-zinc-500"} />
          实时观测
          <span className="text-[11px] font-normal text-zinc-500">近 {live?.minutes ?? LIVE_MINUTES} 分钟 · 每 {LIVE_POLL_MS / 1000}s 刷新</span>
          {autoRefresh && (
            <span className="inline-flex h-2 w-2 animate-pulse rounded-full bg-emerald-400" title="实时刷新中" />
          )}
        </div>
        <Button size="sm" variant="outline" onClick={onToggle}>
          {autoRefresh ? "暂停" : "开启"}实时
        </Button>
      </div>

      <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <LiveStat label="运行数" value={String(s?.runs ?? 0)} />
        <LiveStat
          label="缓存命中率"
          value={s?.hit_rate == null ? "—" : `${Math.round(s.hit_rate * 100)}%`}
          sub={s ? `${s.cache_hits} 命中 / ${s.cache_misses} 未命中` : undefined}
        />
        <LiveStat label="Tokens" value={(s?.tokens ?? 0).toLocaleString()} />
        <LiveStat
          label="SSE 重连"
          value={String(s?.sse_reconnects ?? 0)}
          icon={<Repeat size={12} className={s && s.sse_reconnects > 0 ? "text-amber-400" : "text-zinc-600"} />}
        />
      </div>

      {!hasActivity ? (
        <div className="py-8 text-center text-xs text-zinc-600">
          最近 {live?.minutes ?? LIVE_MINUTES} 分钟暂无活动 —— 发起对话或研究后曲线会实时更新
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <div className="mb-1 text-[11px] text-zinc-500">缓存命中率（%）</div>
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={points} margin={{ top: 5, right: 10, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                <XAxis dataKey="t" stroke="#71717a" fontSize={10} minTickGap={24} />
                <YAxis stroke="#71717a" fontSize={10} domain={[0, 100]} />
                <Tooltip
                  contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 8, fontSize: 12 }}
                  labelStyle={{ color: "#e4e4e7" }}
                />
                <Line
                  type="monotone"
                  dataKey="hit_pct"
                  name="命中率%"
                  stroke="#f59e0b"
                  strokeWidth={2}
                  dot={false}
                  connectNulls={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div>
            <div className="mb-1 text-[11px] text-zinc-500">运行数与 SSE 重连</div>
            <ResponsiveContainer width="100%" height={160}>
              <AreaChart data={points} margin={{ top: 5, right: 10, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                <XAxis dataKey="t" stroke="#71717a" fontSize={10} minTickGap={24} />
                <YAxis stroke="#71717a" fontSize={10} allowDecimals={false} />
                <Tooltip
                  contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: 8, fontSize: 12 }}
                  labelStyle={{ color: "#e4e4e7" }}
                />
                <Area type="monotone" dataKey="runs" name="运行数" stroke="#6366f1" fill="#6366f133" strokeWidth={2} />
                <Area
                  type="monotone"
                  dataKey="sse_reconnects"
                  name="SSE 重连"
                  stroke="#f43f5e"
                  fill="#f43f5e22"
                  strokeWidth={2}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
}

function LiveStat({
  label,
  value,
  sub,
  icon,
}: {
  label: string;
  value: string;
  sub?: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-zinc-800/80 bg-zinc-950/40 px-3 py-2">
      <div className="flex items-center gap-1 text-[11px] text-zinc-500">
        {icon}
        {label}
      </div>
      <div className="font-mono text-lg text-zinc-100">{value}</div>
      {sub && <div className="text-[10px] text-zinc-600">{sub}</div>}
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
